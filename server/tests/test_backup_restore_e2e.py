# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backup, restore test, restore and export of a running Demo Engineering installation, end to end.

The restored data folder is started as a second Kosha server, and its files are compared with the original's.
"""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

from harness import SERVER_DIR, Client, KoshaServer


def _run(kosha, *args, expect=0):
    env = dict(os.environ, KOSHA_DATA_DIR=str(kosha.data_dir / "data"))
    p = subprocess.run([sys.executable, "-m", *args], cwd=SERVER_DIR, env=env, capture_output=True, text=True,
                       timeout=300)
    assert p.returncode == expect, f"{args}: exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    return p.stdout + p.stderr


def _file(client, doc_id):
    data, _ = client.call("GET", f"/api/documents/{doc_id}/file", raw=True)
    return hashlib.sha256(data).hexdigest()


def test_backup_restore_test_restore_and_export(demo_kosha, tmp_path):
    admin = demo_kosha.client()
    asha = demo_kosha.client("asha")
    diag = admin.call("GET", "/api/admin/diagnostics")
    assert diag["backup"]["last"] is None and "No backup" in diag["backup"]["warning"]
    demo_kosha.client("tom").call("GET", "/api/admin/diagnostics", expect=403)

    bk = tmp_path / "backup"
    assert "Database saved and checked" in _run(demo_kosha, "kosha.backup", str(bk))
    out = _run(demo_kosha, "kosha.backup", "--verify", str(bk), "--work", str(tmp_path / "work"))
    assert "RESTORE TEST PASSED" in out and "Restore complete and checked" in out
    diag = admin.call("GET", "/api/admin/diagnostics")
    assert diag["backup"]["last"]["ok"] and diag["backup"]["warning"] == ""
    test = diag["restore_test"]["last"]
    assert test["ok"] and test["files_checked"] > 10 and test["missing"] == 0 and diag["restore_test"]["warning"] == ""
    assert diag["versions"]["latest_database_change"].startswith("m2026") and diag["disk"]["free_bytes"] > 0
    assert diag["addins"].get("enabled", 0) >= 1 and isinstance(diag["queues"]["erp"], dict)
    assert diag["virus_scan"]["configured"] is False and diag["https"] is False

    # restore into a second installation and start it
    second = KoshaServer()
    try:
        out = _run(demo_kosha, "kosha.restore", str(bk), str(second.data_dir / "data"))
        assert "Restore complete and checked" in out
        _run(demo_kosha, "kosha.restore", str(bk), str(second.data_dir / "data"), expect=2)   # never overwrites
        second.start()
        asha2 = second.client("asha")
        docs = asha.call("GET", "/api/search?q=MX-100")["documents"]
        docs2 = {d["id"]: d for d in asha2.call("GET", "/api/search?q=MX-100")["documents"]}
        assert docs and {d["id"] for d in docs} == set(docs2)
        for d in docs[:5]:
            assert _file(asha, d["id"]) == _file(asha2, d["id"])
        Client(second.base, asha.token).call("GET", "/api/auth/me")      # same installation, same sessions
    finally:
        second.stop()

    # full export of the running installation
    zpath = tmp_path / "export.zip"
    assert "Exported" in _run(demo_kosha, "kosha.export", str(zpath))
    assert "EXPORT OK" in _run(demo_kosha, "kosha.export", "--verify", str(zpath))
    with zipfile.ZipFile(zpath) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        index = [json.loads(x) for x in zf.read("index/versions.jsonl").splitlines()]
    shaft = [i for i in index if i["filename"] == "MX-100-020 Agitator Shaft.dxf"]
    assert len(shaft) == 3 and shaft[0]["path"].endswith("/MX-100-020 Agitator Shaft.dxf")
    assert not manifest["problems"] and manifest["files"]["count"] >= test["files_checked"]
    _run(demo_kosha, "kosha.export", str(zpath), expect=2)                                # never overwrites
