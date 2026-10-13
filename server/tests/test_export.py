# SPDX-License-Identifier: AGPL-3.0-or-later
"""Full data export and its check, on a real (small) Kosha data folder, without a server."""
import json
import zipfile

import pytest

import datafolder
from datafolder import sha as _sha
from kosha import config, db, export, store


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A Kosha data folder with two documents, three versions and their previews."""
    yield datafolder.make(tmp_path, monkeypatch)
    db.close_thread_connection()


def test_full_export_and_verify(data, tmp_path):
    out = tmp_path / "exports" / "kosha.zip"
    m = export.export(out)
    assert not m["problems"] and out.is_file() and not out.with_name("kosha.zip.partial").exists()
    tables = {t["name"]: t for t in m["tables"]}
    assert tables["versions"]["rows"] == 3 and tables["documents"]["rows"] == 2
    assert "schema_migrations" in tables and m["schema_migrations"][0] == "m20261013_0900_core_baseline"
    assert m["files"]["count"] == 6 and m["index"]["rows"] == 3
    with zipfile.ZipFile(out) as zf:
        rows = [json.loads(x) for x in zf.read("tables/documents.jsonl").splitlines()]
        assert {r["filename"] for r in rows} == {"MX-100-020 Shaft.dxf", "MX-100-030 Housing.dxf"}
        index = [json.loads(x) for x in zf.read("index/versions.jsonl").splitlines()]
        assert {(i["path"], i["version"]) for i in index} >= {("Kosha/Mixers/MX-100-020 Shaft.dxf", 2)}
        v2 = next(i for i in index if i["version"] == 2)
        assert zf.read(v2["file"]) == b"shaft v2" and v2["sha256"] == _sha(b"shaft v2")
        assert zf.read("README.txt").startswith(b"Kosha data export")
    r = export.verify(out)
    assert r["ok"] and r["files"] == 6 and r["tables"] == len(m["tables"])
    assert export.main(["--verify", str(out)]) == 0
    with pytest.raises(export.ExportError, match="already exists"):
        export.export(out)


def test_export_keeps_binary_values(data, tmp_path):
    db.execute("CREATE TABLE IF NOT EXISTS blobs_test (id INTEGER PRIMARY KEY, data BLOB)")
    db.execute("INSERT INTO blobs_test (data) VALUES (?)", (b"\x00\xff",))
    db.close_thread_connection()
    out = tmp_path / "k.zip"
    export.export(out)
    with zipfile.ZipFile(out) as zf:
        assert json.loads(zf.read("tables/blobs_test.jsonl")) == {"id": 1, "data": {"$base64": "AP8="}}


def test_verify_finds_a_damaged_export(data, tmp_path):
    out = tmp_path / "k.zip"
    export.export(out)
    damaged = tmp_path / "damaged.zip"
    sha = _sha(b"shaft v1")
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(damaged, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == f"files/{sha[:2]}/{sha}":
                data = b"changed"
            if item.filename == "tables/users.jsonl":
                data += b'{"id": 99}\n'
            if item.filename == f"files/{_sha(b'housing')[:2]}/{_sha(b'housing')}":
                continue
            dst.writestr(item, data)
    r = export.verify(damaged)
    assert not r["ok"]
    text = "\n".join(r["problems"])
    assert f"Stored file {sha} does not match its fingerprint" in text
    assert "Table users: checksum does not match" in text
    assert f"Stored file {_sha(b'housing')} is listed but not in the export" in text
    assert export.main(["--verify", str(damaged)]) == 1
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    assert not export.verify(tmp_path / "junk.zip")["ok"]


def test_export_reports_files_missing_on_the_server(data, tmp_path):
    sha = _sha(b"housing")
    store.path_for(sha).unlink()
    assert export.main([str(tmp_path / "k.zip")]) == 1
    r = export.verify(tmp_path / "k.zip")
    assert not r["ok"] and any(sha in p and "missing on disk" in p for p in r["problems"])
    assert any(f"File version {sha} is in the database but not among the exported files" in p for p in r["problems"])


def test_export_needs_a_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "nothing.db")
    with pytest.raises(export.ExportError, match="No Kosha database"):
        export.export(tmp_path / "k.zip")
    assert not (tmp_path / "nothing.db").exists()
