# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: file fingerprints read back and repaired from the backup, the nightly consistency check, and health
alerts to the admins (specification 16.5 and 16.6)."""
import json


def _upload(admin, name, data):
    root = next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])
    r = admin.call("POST", "/api/documents/upload", files={"file": (name, data)},
                   fields={"folder_id": root, "part_number": name.split()[0], "description": "HEALTH CHECK",
                           "material": "S235"})
    assert not r["errors"], r
    return r["documents"][0]


def _sha(kosha, doc_id):
    with kosha.db() as con:
        return con.execute("SELECT blob_sha FROM versions WHERE document_id = ?", (doc_id,)).fetchone()[0]


def test_fingerprints_damage_and_repair(kosha):
    admin = kosha.client()
    doc = _upload(admin, "HC-100-001 Notes.txt", b"pump housing notes\n" * 50)
    sha = _sha(kosha, doc["id"])
    blob = kosha.data_dir / "data" / "blobs" / sha[:2] / sha
    good = blob.read_bytes()

    s = admin.call("GET", "/api/admin/health")
    assert s["fingerprints"]["files"] >= 1 and s["fingerprints"]["checked_last_30_days"] == 0
    r = admin.call("POST", "/api/admin/health/fingerprints", {"everything": True})
    assert r["checked"] >= 1 and r["damaged"] == 0
    assert r["status"]["fingerprints"]["checked_last_30_days"] == r["status"]["fingerprints"]["files"]

    # silent disk damage, no backup yet: reported, the file is left as it is
    blob.write_bytes(b"X" + good[1:])
    r = admin.call("POST", "/api/admin/health/fingerprints", {"everything": True})
    assert r["damaged"] == 1 and r["repaired"] == 0 and r["not_repaired"][0]["why"] == "no copy in the backup folder"
    keys = {p["key"] for p in r["status"]["problems"]}
    assert "fingerprints" in keys

    # with a good copy in the backup folder, it is repaired and the damaged file kept aside
    backup = kosha.data_dir / "backup"
    (backup / "blobs" / sha[:2]).mkdir(parents=True)
    (backup / "blobs" / sha[:2] / sha).write_bytes(good)
    with kosha.db() as con:
        con.execute("INSERT INTO settings(key, value_json) VALUES ('backup_last', ?) ON CONFLICT(key) DO UPDATE "
                    "SET value_json = excluded.value_json",
                    (json.dumps({"at": "2026-10-13T00:00:00+00:00", "ok": True, "folder": str(backup)}),))
    r = admin.call("POST", "/api/admin/health/fingerprints", {"everything": True})
    assert r["damaged"] == 1 and r["repaired"] == 1
    assert blob.read_bytes() == good
    kept = list((kosha.data_dir / "data" / "damaged").iterdir())
    assert len(kept) == 1 and kept[0].read_bytes().startswith(b"X")
    assert "fingerprints" not in {p["key"] for p in r["status"]["problems"]}
    assert r["status"]["fingerprints"]["repaired"][0]["sha"] == sha
    with kosha.db() as con:
        assert con.execute("SELECT COUNT(*) FROM notifications WHERE title LIKE 'A damaged stored file%'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM audit WHERE action = 'file_repaired'").fetchone()[0] == 1
    assert admin.call("GET", f"/api/documents/{doc['id']}/file", raw=True)[0] == good

    # only admins
    eng = kosha.add_user("hc-eng", "engineer")
    eng.call("GET", "/api/admin/health", expect=403)
    eng.call("POST", "/api/admin/health/consistency", expect=403)


def test_consistency_check(kosha):
    admin = kosha.client()
    r = admin.call("POST", "/api/admin/health/consistency")
    assert r["ok"] and r["count"] == 0
    doc = _upload(admin, "HC-200-001 Spec.txt", b"conveyor spec\n")
    sha = _sha(kosha, doc["id"])
    (kosha.data_dir / "data" / "blobs" / sha[:2] / sha).unlink()
    item = admin.call("POST", "/api/items", {"part_number": "HC-300-001", "description": "MIXER FRAME"})
    item_id = item.get("id") or item["item"]["id"]
    with kosha.db() as con:
        con.execute("PRAGMA foreign_keys = OFF")
        now = "2026-10-13T00:00:00+00:00"
        bom = con.execute("INSERT INTO boms(item_id, revision, created_at, updated_at) VALUES (?, 'A', ?, ?)",
                          (item_id, now, now)).lastrowid
        con.execute("INSERT INTO bom_lines(bom_id, line_no, child_item_id) VALUES (?, 1, 999999)", (bom,))
    r = admin.call("POST", "/api/admin/health/consistency")
    kinds = sorted(p["kind"] for p in r["problems"])
    assert not r["ok"] and kinds == ["bom_line_without_item", "missing_content"], r["problems"]
    assert any("HC-200-001 Spec.txt" in p["text"] for p in r["problems"])
    assert "consistency" in {p["key"] for p in r["status"]["problems"]}
