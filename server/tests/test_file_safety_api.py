# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: file safety (specification 8.4, 13.5 and 16.2). Resumable uploads checked against their fingerprint,
byte-range downloads, blocked program files, the recycle bin, the two-admin purge and the clean-up of files that were
never fully stored."""
import hashlib
import os
import time

from kosha.cad import dxfwriter as W


def _dxf(pn, desc):
    return W.flange(values={"PART_NO": pn, "DESCRIPTION": desc, "MATERIAL": "S235"})


def _folder(admin, name):
    root = next(x["id"] for x in admin.call("GET", "/api/folders")["folders"] if not x["parent_id"])
    return admin.call("POST", "/api/folders", {"parent_id": root, "name": name})["folder"]["id"]


def _put(c, folder, name, data):
    r = c.call("POST", "/api/documents/upload", files={"file": (name, data)}, fields={"folder_id": folder})
    assert not r["errors"], r
    return r["documents"][0]


def _piece(c, uid, offset, data, expect=200):
    return c.call("PUT", f"/api/uploads/{uid}?offset={offset}", raw_body=data, expect=expect)


def test_resumable_upload_and_ranges(kosha):
    admin = kosha.client()
    eng = kosha.add_user("fs-eng", "engineer")
    f = _folder(admin, "Big files")
    data = _dxf("FS-100-001", "BASE FRAME") + b"\n999\n" + os.urandom(200_000).hex().encode() + b"\n"
    sha = hashlib.sha256(data).hexdigest()

    # refused before a byte is sent: programs, missing fingerprint, too large
    assert eng.call("POST", "/api/uploads", {"filename": "setup.exe", "size": 10, "sha256": sha},
                    expect=400)["code"] == "blocked_type"
    assert "SHA-256" in eng.call("POST", "/api/uploads", {"filename": "a.dxf", "size": 10, "sha256": "x"},
                                 expect=400)["error"]
    assert eng.call("POST", "/api/uploads", {"filename": "a.dxf", "size": 10 ** 13, "sha256": sha},
                    expect=400)["code"] == "too_large"

    up = eng.call("POST", "/api/uploads", {"filename": "FS-100-001 Base frame.dxf", "size": len(data), "sha256": sha})
    uid, half = up["id"], len(data) // 2
    _piece(eng, uid, 0, data[:half])
    # the connection broke: the client asks how far it got and continues from there
    assert eng.call("GET", f"/api/uploads/{uid}")["received"] == half
    assert _piece(eng, uid, half + 10, data[half + 10:], expect=409)["received"] == half
    _piece(eng, uid, 0, data[:half])                    # a piece sent twice changes nothing
    assert eng.call("POST", f"/api/uploads/{uid}/finish", {"folder_id": f}, expect=409)["code"] == "incomplete"
    _piece(eng, uid, half, data[half:])
    # someone else's upload is not found
    kosha.add_user("fs-other", "engineer").call("GET", f"/api/uploads/{uid}", expect=404)
    doc = eng.call("POST", f"/api/uploads/{uid}/finish", {"folder_id": f, "comment": "first", "path": "Frames"})
    assert doc["part_number"] == "FS-100-001" and doc["version"] == 1
    assert eng.call("POST", f"/api/uploads/{uid}/finish", {"folder_id": f})["id"] == doc["id"]    # sent twice

    # downloads in byte ranges, so a big download resumes too
    whole, _ = eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)
    assert whole == data
    part, _ = eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True, headers={"Range": "bytes=100-199"},
                       expect=206)
    assert part == data[100:200]
    tail, _ = eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True, headers={"Range": "bytes=-50"}, expect=206)
    assert tail == data[-50:]
    eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True, headers={"Range": f"bytes={len(data)}-"}, expect=416)

    # a damaged transfer is never stored: the fingerprint does not match
    bad = eng.call("POST", "/api/uploads", {"filename": "FS-100-002 Plate.dxf", "size": len(data), "sha256": sha})
    _piece(eng, bad["id"], 0, data[:-1] + b"X")
    assert eng.call("POST", f"/api/uploads/{bad['id']}/finish", {"folder_id": f},
                    expect=409)["code"] == "fingerprint_mismatch"
    assert eng.call("GET", f"/api/uploads/{bad['id']}")["status"] == "damaged"

    # checking in a new version through a resumable upload
    eng.call("POST", f"/api/documents/{doc['id']}/checkout")
    v2 = data + b"\n"
    up2 = eng.call("POST", "/api/uploads", {"filename": "whatever.dxf", "size": len(v2),
                                            "sha256": hashlib.sha256(v2).hexdigest()})
    _piece(eng, up2["id"], 0, v2)
    assert eng.call("POST", f"/api/uploads/{up2['id']}/finish", {"document_id": doc["id"]})["version"] == 2

    # program files are refused on the ordinary upload too
    r = eng.call("POST", "/api/documents/upload", files={"file": ("tool.ps1", b"Write-Host hi")},
                 fields={"folder_id": f})
    assert r["errors"][0]["code"] == "blocked_type" and "program" in r["errors"][0]["error"]
    ok = eng.call("POST", "/api/documents/upload", files={"file": ("Calc sheet.xlsm", b"PK\x03\x04 macros")},
                  fields={"folder_id": f})
    assert not ok["errors"]                             # macro sheets are allowed; macros never run on the server


def test_recycle_bin_and_two_admin_purge(kosha):
    admin = kosha.client()
    admin2 = kosha.add_user("fs-admin2", "admin")
    eng = kosha.add_user("rb-eng", "engineer")
    other = kosha.add_user("rb-other", "engineer")
    f = _folder(admin, "Bin test")
    sub = admin.call("POST", "/api/folders", {"parent_id": f, "name": "Sub"})["folder"]["id"]
    a = _put(eng, f, "RB-100-001 Cover.dxf", _dxf("RB-100-001", "COVER"))
    b = _put(eng, sub, "RB-100-002 Lid.dxf", _dxf("RB-100-002", "LID"))

    # a deleted file waits in the bin and goes back where it was
    eng.call("DELETE", f"/api/documents/{a['id']}")
    eng.call("GET", f"/api/documents/{a['id']}", expect=404)
    binned = eng.call("GET", "/api/recycle-bin")["files"]
    assert binned[0]["id"] == a["id"] and binned[0]["where"] == "Bin test" and binned[0]["can_restore"]
    assert other.call("GET", "/api/recycle-bin")["files"] == []           # not theirs
    other.call("POST", f"/api/recycle-bin/{a['id']}/restore", expect=403)
    assert eng.call("POST", f"/api/recycle-bin/{a['id']}/restore")["folder_id"] == f
    assert eng.call("GET", f"/api/documents/{a['id']}")["id"] == a["id"]

    # deleting a folder sends its files to the bin; putting one back recreates the folder
    assert admin.call("DELETE", f"/api/folders/{sub}")["files_to_bin"] == 1
    back = eng.call("POST", f"/api/recycle-bin/{b['id']}/restore")
    assert [x["name"] for x in eng.call("GET", f"/api/folders/{f}/contents")["folders"]] == ["Sub"]
    assert [x["id"] for x in eng.call("GET", f"/api/folders/{back['folder_id']}/contents")["documents"]] == [b["id"]]

    # a folder with a released file is not deleted, and nothing in it moves
    appr = kosha.add_user("rb-appr", "approver")
    c = _put(eng, f, "RB-100-003 Shaft.dxf", _dxf("RB-100-003", "SHAFT"))
    appr.call("POST", f"/api/documents/{c['id']}/state", {"to": "released"})
    r = admin.call("DELETE", f"/api/folders/{f}", expect=409)
    assert "released" in r["error"]
    assert eng.call("GET", f"/api/documents/{a['id']}")["id"] == a["id"]

    # after the bin's days, only an admin puts a file back
    eng.call("DELETE", f"/api/documents/{a['id']}")
    with kosha.db() as con:
        con.execute("UPDATE documents SET deleted_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (a["id"],))
    row = eng.call("GET", "/api/recycle-bin")["files"][0]
    assert row["past_retention"] and not row["can_restore"] and "admin" in row["why_not"]
    eng.call("POST", f"/api/recycle-bin/{a['id']}/restore", expect=403)

    # purge: one admin asks with a reason, a second confirms; the content goes, the record stays
    with kosha.db() as con:
        sha = con.execute("SELECT blob_sha FROM versions WHERE document_id = ?", (a["id"],)).fetchone()[0]
    blob = kosha.data_dir / "data" / "blobs" / sha[:2] / sha
    assert blob.exists()
    assert "why" in admin.call("POST", "/api/purges", {"document_ids": [a["id"]]}, expect=400)["error"]
    assert admin.call("POST", "/api/purges", {"document_ids": [c["id"]], "note": "x"}, expect=409)["code"] == "not_in_bin"
    eng.call("POST", "/api/purges", {"document_ids": [a["id"]], "note": "Customer drawing returned"}, expect=403)
    req = admin.call("POST", "/api/purges", {"document_ids": [a["id"]], "note": "Customer drawing returned"})
    assert req["status"] == "waiting"
    assert eng.call("GET", "/api/recycle-bin")["files"][0]["purge_waiting"]
    assert "second admin" in admin.call("POST", f"/api/purges/{req['id']}/confirm", expect=403)["error"]
    done = admin2.call("POST", f"/api/purges/{req['id']}/confirm")
    assert done["status"] == "done" and done["decided_by_name"] and done["removed_files"] >= 1
    assert not blob.exists()
    row = admin.call("GET", "/api/recycle-bin")["files"][0]
    assert row["purged_at"] and not row["can_restore"]
    admin.call("POST", f"/api/recycle-bin/{a['id']}/restore", expect=403)
    with kosha.db() as con:
        assert con.execute("SELECT COUNT(*) FROM audit WHERE action = 'purge' AND entity_id = ?",
                           (a["id"],)).fetchone()[0] == 1
    admin2.call("POST", f"/api/purges/{req['id']}/cancel", expect=409)     # already decided


def test_cleanup_of_unfinished_files(kosha):
    admin = kosha.client()
    eng = kosha.add_user("cl-eng", "engineer")
    f = _folder(admin, "Clean-up")
    plate = _dxf("CL-100-001", "PLATE")
    doc = _put(eng, f, "CL-100-001 Plate.dxf", plate)
    blobs = kosha.data_dir / "data" / "blobs"
    old = time.time() - 9 * 86400
    # a stored file whose record never got written, eight days old; and a used one, just as old
    orphan_sha = "ab" * 32
    (blobs / "ab").mkdir(parents=True, exist_ok=True)
    orphan = blobs / "ab" / orphan_sha
    orphan.write_bytes(b"left over from a crash")
    os.utime(orphan, (old, old))
    with kosha.db() as con:
        used_sha = con.execute("SELECT blob_sha FROM versions WHERE document_id = ?", (doc["id"],)).fetchone()[0]
    used = blobs / used_sha[:2] / used_sha
    os.utime(used, (old, old))
    # a temporary file of an upload that broke off, and an upload abandoned for eight days
    tmp = kosha.data_dir / "data" / "work" / "up_broken"
    tmp.write_bytes(b"half")
    os.utime(tmp, (old, old))
    up = eng.call("POST", "/api/uploads", {"filename": "x.dxf", "size": 10, "sha256": "0" * 64})
    with kosha.db() as con:
        con.execute("UPDATE upload_sessions SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (up["id"],))
    # a fresh file nobody refers to yet (an upload in progress) is left alone
    fresh = blobs / "cd" / ("cd" * 32)
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"being written")

    eng.call("POST", "/api/maintenance/cleanup", expect=403)
    r = admin.call("POST", "/api/maintenance/cleanup")
    assert r["orphans_moved"] == 1 and r["temp_files"] == 1 and r["uploads"] == 1
    assert not orphan.exists() and (kosha.data_dir / "data" / "orphans" / orphan_sha).exists()
    assert used.exists() and fresh.exists() and not tmp.exists()
    assert eng.call("GET", f"/api/uploads/{up['id']}")["status"] == "expired"
    whole, _ = eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)
    assert whole == plate
