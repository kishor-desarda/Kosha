# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small but real Kosha data folder for unit tests of backup, restore and export (no server needed)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kosha import config, db, store


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make(tmp_path: Path, monkeypatch) -> Path:
    """Point Kosha at a new data folder with two documents, three versions and their previews."""
    d = tmp_path / "live"
    monkeypatch.setattr(config, "DATA_DIR", d)
    monkeypatch.setattr(config, "BLOB_DIR", d / "blobs")
    monkeypatch.setattr(config, "WORK_DIR", d / "work")
    monkeypatch.setattr(config, "DB_PATH", d / "kosha.db")
    monkeypatch.setenv("KOSHA_SECRET", "")
    db.close_thread_connection()
    config.ensure_dirs()
    db.init()
    (d / "secret.key").write_text("s" * 64)
    now = db.now()
    top = db.insert("folders", {"parent_id": None, "name": "Kosha", "created_at": now})
    sub = db.insert("folders", {"parent_id": top, "name": "Mixers", "created_at": now})
    for name, contents in (("MX-100-020 Shaft.dxf", [b"shaft v1", b"shaft v2"]), ("MX-100-030 Housing.dxf", [b"housing"])):
        doc = db.insert("documents", {"folder_id": sub, "filename": name, "ext": "dxf", "doc_type": "drawing",
                                      "latest_version": len(contents), "created_at": now, "updated_at": now})
        for i, content in enumerate(contents, 1):
            blob, size = store.put_bytes(content)
            preview = store.put_bytes(b"<svg>" + content + b"</svg>")[0]
            db.insert("versions", {"document_id": doc, "version": i, "revision": "A", "blob_sha": blob, "size": size,
                                   "created_at": now, "preview_kind": "svg", "preview_sha": preview})
    db.insert("settings", {"key": "blob_test", "value_json": json.dumps({"bin": True})})
    db.close_thread_connection()
    return d
