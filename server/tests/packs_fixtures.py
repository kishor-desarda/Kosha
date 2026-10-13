# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the pack, numbering and library unit tests: a fresh database and a copy of the packs folder."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kosha import config, db
from kosha.packs import PackConfig, PackLoader, Rec

REPO_PACKS = Path(__file__).resolve().parents[2] / "packs"


@pytest.fixture
def kdb(tmp_path, monkeypatch):
    """An empty Kosha database in a temporary folder (each thread opens its own connection)."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BLOB_DIR", tmp_path / "blobs")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kosha.db")
    db.close_thread_connection()
    db.init()
    yield tmp_path
    db.close_thread_connection()


@pytest.fixture
def packs_copy(tmp_path):
    """A writable copy of the repository's packs folder."""
    dest = tmp_path / "packs"
    shutil.copytree(REPO_PACKS, dest)
    return dest


def config_of(*pack_ids: str, root: Path = REPO_PACKS) -> PackConfig:
    """An in-memory configuration built straight from pack files, without a database."""
    loader = PackLoader(root)
    recs: dict = {}
    for pid in pack_ids:
        for r in loader.load(pid).records:
            recs.setdefault((r.kind, r.key), Rec(r.kind, r.key, r.scope, r.value, "pack", pid, [pid], r.value))
    return PackConfig(list(recs.values()), [], 0)
