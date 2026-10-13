# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shop view with QR labels (specification 5.7), and the practice folder (5.6), end to end."""
import pytest

from kosha import db, practice


def _root(admin) -> int:
    return next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])


def _upload(client, folder, pn, description):
    return client.call("POST", "/api/documents/upload", files={"file": (f"{pn} {description.title()}.dxf",
                                                                        b"0\nSECTION\n0\nEOF\n")},
                       fields={"folder_id": folder, "part_number": pn, "description": description,
                               "material": "S235"})["documents"][0]


def test_shop_view_shows_only_released_revisions(kosha):
    admin = kosha.client()
    eng = kosha.add_user("sh-eng", "engineer")
    appr = kosha.add_user("sh-appr", "approver")
    shop = kosha.add_user("sh-shop", "viewer")
    root = _root(admin)
    doc = _upload(eng, root, "SH-100-001", "PUMP BASE")
    _upload(eng, root, "SH-100-002", "COVER")

    # never released: nothing to build from
    r = shop.call("GET", "/api/shop?pn=sh-100-002")
    assert r["item"]["part_number"] == "SH-100-002" and r["drawings"] == []
    assert "No part SH-999 in Kosha" in shop.call("GET", "/api/shop?pn=SH-999", expect=404)["error"]
    shop.call("GET", "/api/shop?pn=%20", expect=404)

    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released", "reason": "first issue"})
    d = shop.call("GET", "/api/shop?pn=SH-100-001")["drawings"]
    assert [(x["id"], x["revision"], x["version"], x["note"]) for x in d] == [(doc["id"], "A", 1, "")]

    # rev B in work: the shop still gets rev A, with a note
    eng.call("POST", f"/api/documents/{doc['id']}/revise")
    eng.call("POST", f"/api/documents/{doc['id']}/checkout")
    eng.call("POST", f"/api/documents/{doc['id']}/checkin", files={"file": (doc["filename"], b"0\nSECTION\n0\nEOF\n\n")},
             fields={"comment": "holes moved"})
    d = shop.call("GET", "/api/shop?pn=SH-100-001")["drawings"][0]
    assert (d["revision"], d["version"]) == ("A", 1) and "Rev B is being worked on" in d["note"]

    # the QR label: an SVG that links to the shop view of this part
    item_id = eng.call("GET", "/api/items?q=SH-100-001")["items"][0]["id"]
    data, ctype = shop.call("GET", f"/api/items/{item_id}/qr.svg", raw=True)
    svg = data.decode()
    assert ctype.startswith("image/svg+xml") and svg.startswith("<svg") and "SH-100-001 PUMP BASE" in svg
    shop.call("GET", "/api/items/999999/qr.svg", expect=404)


def test_qr_label_reads_back_as_a_shop_link(kosha):
    zxingcpp = pytest.importorskip("zxingcpp")
    np = pytest.importorskip("numpy")
    import re
    admin = kosha.client()
    _upload(admin, _root(admin), "SH-200-001", "SHAFT")
    item_id = admin.call("GET", "/api/items?q=SH-200-001")["items"][0]["id"]
    svg = admin.call("GET", f"/api/items/{item_id}/qr.svg", raw=True)[0].decode()
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    side = int(m.group(1))
    img = np.full((side, side), 255, dtype=np.uint8)
    for x, y in re.findall(r"M(\d+),(\d+)h8v8h-8z", svg):
        img[int(y):int(y) + 8, int(x):int(x) + 8] = 0
    text = zxingcpp.read_barcodes(img)[0].text
    assert text == f"{kosha.base}/#/shop?pn=SH-200-001"


def test_practice_folder_never_reaches_the_erp(kosha):
    admin = kosha.client()
    eng = kosha.add_user("pr-eng", "engineer")
    appr = kosha.add_user("pr-appr", "approver")
    where = eng.call("GET", "/api/practice")
    assert where["name"] == "Training — safe to try"
    folders = {f["id"]: f for f in admin.call("GET", "/api/folders")["folders"]}
    assert folders[where["folder_id"]]["parent_id"] == _root(admin)

    real = _upload(eng, _root(admin), "PR-100-001", "REAL BRACKET")
    toy = _upload(eng, where["folder_id"], "PR-100-002", "PRACTICE BRACKET")
    for d in (real, toy):
        appr.call("POST", f"/api/documents/{d['id']}/state", {"to": "released", "reason": "trying"})
    appr.call("POST", "/api/erp/process")
    jobs = {j["label"].split()[0]: j for j in admin.call("GET", "/api/erp/jobs")["jobs"] if j["kind"] == "item"}
    assert jobs["PR-100-002"]["status"] == "skipped" and "practice" in jobs["PR-100-002"]["response_text"]
    assert jobs["PR-100-001"]["status"] == "dry_run"


def test_practice_folder_is_made_once_and_emptied_daily(kdb):
    from kosha.lifecycle import root_folder_id
    root = root_folder_id()
    fid = practice.ensure()
    assert practice.ensure() == fid and practice.folder_id() == fid
    sub = db.insert("folders", {"parent_id": fid, "name": "Mine", "created_at": db.now()})
    now = db.now()
    ids = [db.insert("documents", {"folder_id": f, "filename": f"T-{f}.dxf", "ext": "dxf", "doc_type": "drawing",
                                   "created_at": now, "updated_at": now}) for f in (fid, sub, root)]
    assert practice.clear() == {"cleared": 2}
    deleted = {r["id"]: r for r in db.rows("SELECT id, deleted, deleted_path FROM documents")}
    assert [deleted[i]["deleted"] for i in ids] == [1, 1, 0]
    assert deleted[ids[1]]["deleted_path"] == "Training — safe to try/Mine"
    assert practice.in_practice(deleted[ids[1]])            # still known as practice in the recycle bin
    assert practice.clear() == {"cleared": 0}

    # an admin who deletes the folder (its files wait in the bin under the top folder) is not overruled
    db.execute("UPDATE documents SET folder_id = ? WHERE folder_id IN (?, ?)", (root, sub, fid))
    db.execute("DELETE FROM folders WHERE id IN (?, ?)", (sub, fid))
    assert practice.ensure() is None and practice.folder_id() is None and practice.clear() == {"cleared": 0}
