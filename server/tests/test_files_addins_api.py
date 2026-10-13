# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files and add-ins end to end (specification 8 and 20): previews of text, archives, e-mails and videos through
the API, and three add-in kinds written here as fakes: a file-type handler, a storage add-in that keeps copies and
brings back a lost file, and an importer that brings files from another system into a normal import."""
import json
import textwrap
import time
import zipfile
import io

import pytest

from harness import KoshaServer
from kosha.cad import dxfwriter as W

ADDINS = {
    "com.example.abc-files": ("file-type", "files:preview", '''
from kosha_sdk.files import FilePreview, FileTypeHandler


class Main(FileTypeHandler):
    extensions = ("abc",)

    def preview(self, filename, data):
        if data.startswith(b"BAD"):
            raise ValueError("cannot read this one")
        return FilePreview(summary={"type": "text", "title": "ABC part", "lines": data.decode().splitlines()},
                           meta={"format": "ABC 2"})
'''),
    "com.example.copies": ("storage", "storage:copy", '''
from kosha_sdk import AddinError, Health
from kosha_sdk.files import StorageProvider


class Main(StorageProvider):
    def folder(self):
        p = self.ctx.workdir / "copies"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def health(self):
        return Health.up("Copies folder ready")

    def put(self, key, data):
        (self.folder() / key).write_bytes(data)

    def get(self, key):
        p = self.folder() / key
        if not p.exists():
            raise AddinError("no copy")
        return p.read_bytes()

    def exists(self, key):
        return (self.folder() / key).exists()
'''),
    "com.example.old-pdm": ("importer", "import:provide", '''
from kosha.cad import dxfwriter as W
from kosha_sdk.files import Importer


class Main(Importer):
    def collect(self, dest, options):
        (dest / "Pumps").mkdir()
        (dest / "Pumps" / "OP-100-001.dxf").write_bytes(W.flange(values={"PART_NO": "OP-100-001"}))
        (dest / "OP-100-002 Note.txt").write_text("Old PDM note")
        return [{"filename": "OP-100-001.dxf", "part_number": "OP-100-001", "description": "PUMP FLANGE",
                 "material": "S275JR"}, "not a row"]
'''),
}


def _make(root, addin_id, kind, perm, code):
    folder = root / addin_id.rsplit(".", 1)[1]
    pkg = folder / (addin_id.rsplit(".", 1)[1].replace("-", "_") + "_addin")
    pkg.mkdir(parents=True)
    (folder / "kosha-addin.yaml").write_text(textwrap.dedent(f"""\
        id: {addin_id}
        name: Example {kind}
        version: 1.0.0
        publisher: Example
        licence: Apache-2.0
        kosha: ">=0.5,<2.0"
        kind: [{kind}]
        entry_points: {{server: "{pkg.name}.main:Main"}}
        permissions: [{perm}]
        """))
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(code)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("addins")
    for addin_id, (kind, perm, code) in ADDINS.items():
        _make(root, addin_id, kind, perm, code)
    s = KoshaServer(env={"KOSHA_ADDIN_DIR": str(root)}).start()
    admin = s.client()
    for addin_id in ADDINS:
        admin.call("POST", f"/api/addins/{addin_id}/enable")
    yield s
    s.stop()


def _root(c) -> int:
    return next(f["id"] for f in c.call("GET", "/api/folders")["folders"] if not f["parent_id"])


def _upload(c, name, data, **fields):
    return c.call("POST", "/api/documents/upload", files={"file": (name, data)},
                  fields={"folder_id": _root(c), **fields})["documents"][0]


def _viewer(c, doc_id):
    data, ctype = c.call("GET", f"/api/documents/{doc_id}/viewer", raw=True)
    return json.loads(data) if "json" in ctype else (data, ctype)


def test_previews_through_the_api(server):
    eng = server.add_user("pv-eng", "engineer")
    t = _upload(eng, "PV-100-001 Notes.txt", b"Check the hub seat\nRev B")
    assert _viewer(eng, t["id"])["lines"] == ["Check the hub seat", "Rev B"]
    data, ctype = eng.call("GET", f"/api/documents/{t['id']}/preview", raw=True)
    assert ctype.startswith("image/svg") and b"TEXT" in data

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a/b.dxf", b"0\nEOF\n")
    z = _upload(eng, "PV-100-002 Order.zip", buf.getvalue())
    assert _viewer(eng, z["id"])["entries"][0]["name"] == "a/b.dxf"

    video = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40
    v = _upload(eng, "PV-100-003 Test run.mp4", video)
    whole, ctype = _viewer(eng, v["id"])
    assert ctype == "video/mp4" and whole == video
    part, _ = eng.call("GET", f"/api/documents/{v['id']}/viewer", headers={"Range": "bytes=10-19"}, raw=True,
                       expect=206)
    assert part == video[10:20]
    detail = eng.call("GET", f"/api/documents/{v['id']}")
    assert detail["type_label"] == "Video"


def test_file_type_add_in(server):
    eng = server.add_user("ft-eng", "engineer")
    d = _upload(eng, "FT-100-001 Pump.abc", b"ABC PART\nlength 120")
    s = _viewer(eng, d["id"])
    assert s["title"] == "ABC part" and s["lines"] == ["ABC PART", "length 120"]
    meta = eng.call("GET", f"/api/documents/{d['id']}")["meta"]
    assert meta["preview_by"] == "Example file-type" and meta["addin_format"] == "ABC 2"
    bad = _upload(eng, "FT-100-002 Broken.abc", b"BAD data")
    meta = eng.call("GET", f"/api/documents/{bad['id']}")["meta"]
    assert "could not show this file" in meta["preview_note"]          # the check-in itself went through


def test_storage_copies_bring_back_a_lost_file(server):
    admin, eng = server.client(), server.add_user("sc-eng", "engineer")
    d = _upload(eng, "SC-100-001 Plate.dxf", W.flange(values={"PART_NO": "SC-100-001"}))
    r = admin.call("POST", "/api/admin/health/storage-copies")
    assert r["copied"] >= 1 and r["failed"] == 0, r
    prov = r["status"]["storage_copies"]["providers"][0]
    assert prov["name"] == "Example storage" and prov["missing"] == 0 and prov["copied"] == r["status"]["storage_copies"]["files"]
    assert admin.call("POST", "/api/admin/health/storage-copies")["copied"] == 0          # nothing new to copy

    with server.db() as c:
        sha = c.execute("SELECT blob_sha FROM versions WHERE document_id = ?", (d["id"],)).fetchone()[0]
    blob = server.data_dir / "data" / "blobs" / sha[:2] / sha
    original = blob.read_bytes()
    blob.unlink()                                              # the disk lost it
    r = admin.call("POST", "/api/admin/health/storage-copies")
    assert r["restore"]["restored"] == 1 and blob.read_bytes() == original
    data, _ = eng.call("GET", f"/api/documents/{d['id']}/file", raw=True)
    assert data == original

    blob.write_bytes(b"damaged")                               # damaged on disk: the fingerprint check repairs it
    r = admin.call("POST", "/api/admin/health/fingerprints", {"everything": True})
    assert r["repaired"] >= 1 and blob.read_bytes() == original


def test_importer_add_in_runs_a_normal_import(server):
    admin = server.client()
    assert [a["name"] for a in admin.call("GET", "/api/import/addins")["addins"]] == ["Example importer"]
    admin.call("POST", "/api/import/from-addin", {"addin": "com.example.copies", "target": "X"}, expect=400)
    admin.call("POST", "/api/import/from-addin", {"addin": "com.example.old-pdm"}, expect=400)
    job = admin.call("POST", "/api/import/from-addin", {"addin": "com.example.old-pdm", "target": "Old PDM",
                                                        "state": "in_work"})["job"]
    for _ in range(300):
        j = next(x for x in admin.call("GET", "/api/import/jobs")["jobs"] if x["id"] == job["id"])
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done" and j["imported"] == 2 and j["source"] == "Example importer (add-in)", j
    item = next(i for i in admin.call("GET", "/api/items?q=OP-100-001")["items"] if i["part_number"] == "OP-100-001")
    assert item["description"] == "PUMP FLANGE" and item["material"] == "S275JR"
    docs = admin.call("GET", "/api/search?q=OP-100-001")["documents"]
    assert docs and docs[0]["folder_name"] == "Pumps"
    assert admin.call("GET", f"/api/import/jobs/{j['id']}/reconcile")["adds_up"]
    assert not list((server.data_dir / "data" / "work").glob("addin-import-*"))      # its copies were removed


def test_catalogue_groups_add_ins_by_kind(server):
    c = server.client().call("GET", "/api/addins/catalogue")
    kinds = {g["kind"]: g for g in c["groups"]}
    assert {a["name"] for a in kinds["storage"]["addins"]} == {"Example storage"}
    assert kinds["storage"]["label"] == "Storage copies" and kinds["storage"]["addins"][0]["where"] == ["Kosha server"]
    assert kinds["ai-provider"]["template"] == "kosha-sdk new ai-provider my-addin"
    assert any(a["id"] == "org.kosha.erpnext" for a in kinds["erp-connector"]["addins"])
    server.add_user("cat-eng", "engineer").call("GET", "/api/addins/catalogue", expect=403)
