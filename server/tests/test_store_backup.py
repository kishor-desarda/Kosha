# SPDX-License-Identifier: AGPL-3.0-or-later
"""The file store and the backup never keep a partial or wrong file."""
import hashlib
import io
import sqlite3

import pytest

from kosha import backup, config, store


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BLOB_DIR", tmp_path / "blobs")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kosha.db")
    con = sqlite3.connect(tmp_path / "kosha.db")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    return tmp_path


def test_store_is_content_addressed_and_verifiable(data):
    sha, size = store.put_stream(io.BytesIO(b"drawing v1"))
    assert sha == hashlib.sha256(b"drawing v1").hexdigest() and size == 10
    assert store.put_bytes(b"drawing v1") == (sha, 10)          # stored once
    assert store.verify(sha)
    store.path_for(sha).write_bytes(b"tampered")
    assert not store.verify(sha)
    assert not list((data / "work").iterdir())                 # no temporary files left behind


def test_store_rejects_odd_fingerprints(data):
    with pytest.raises(ValueError):
        store.path_for("../../etc/passwd")


def test_backup_copies_and_checks_everything(data, tmp_path_factory):
    shas = [store.put_bytes(f"file {i}".encode())[0] for i in range(5)]
    dest = tmp_path_factory.mktemp("backup")
    db_copy = backup.backup(dest)
    assert sqlite3.connect(db_copy).execute("SELECT x FROM t").fetchone() == (1,)
    for sha in shas:
        assert (dest / "blobs" / sha[:2] / sha).read_bytes() == store.read_bytes(sha)
    assert backup.backup(dest).exists()                          # second run: only new files, still fine


def test_backup_refuses_a_corrupt_source_file(data, tmp_path_factory):
    sha = store.put_bytes(b"good content")[0]
    store.path_for(sha).write_bytes(b"bit rot")
    with pytest.raises(backup.BackupError, match="did not copy correctly"):
        backup.backup(tmp_path_factory.mktemp("backup"))
