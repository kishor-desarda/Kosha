# SPDX-License-Identifier: AGPL-3.0-or-later
"""Restore and the restore test on a real (small) Kosha data folder, without a server."""
import sqlite3
import zipfile

import pytest

import datafolder
from datafolder import sha as _sha
from kosha import backup, config, db, export, restore, store


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A Kosha data folder with two documents, three versions and their previews."""
    yield datafolder.make(tmp_path, monkeypatch)
    db.close_thread_connection()


def test_restore_into_an_empty_folder_checks_everything(data, tmp_path):
    bk = tmp_path / "backup"
    backup.backup(bk)
    r = restore.restore(bk, tmp_path / "restored")
    assert r["ok"] and r["integrity"] == "ok" and r["file_versions"] == 3 and r["files_checked"] == 6
    assert "all 6 stored files" in r["message"]
    out = tmp_path / "restored"
    con = sqlite3.connect(out / "kosha.db")
    assert con.execute("SELECT COUNT(*) FROM versions").fetchone() == (3,)
    con.close()
    assert (out / "blobs" / _sha(b"shaft v2")[:2] / _sha(b"shaft v2")).read_bytes() == b"shaft v2"
    assert (out / "secret.key").read_text() == "s" * 64
    assert not list(out.rglob("*.partial"))


def test_restore_never_overwrites(data, tmp_path):
    bk = tmp_path / "backup"
    backup.backup(bk)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "kosha.db").write_text("precious")
    with pytest.raises(restore.RestoreError, match="not an empty folder"):
        restore.restore(bk, busy)
    assert (busy / "kosha.db").read_text() == "precious"
    with pytest.raises(restore.RestoreError, match="outside the backup folder"):
        restore.restore(bk, bk / "inside")
    with pytest.raises(restore.RestoreError, match="no database copy"):
        restore.restore(tmp_path / "busy", tmp_path / "new")
    assert restore.main([str(bk), str(busy)]) == 2


def test_restore_finds_missing_and_damaged_files(data, tmp_path, capsys):
    bk = tmp_path / "backup"
    backup.backup(bk)
    gone, bad = _sha(b"shaft v1"), _sha(b"housing")
    (bk / "blobs" / gone[:2] / gone).unlink()
    (bk / "blobs" / bad[:2] / bad).write_bytes(b"bit rot")
    r = restore.restore(bk, tmp_path / "restored")
    assert not r["ok"]
    assert [m["sha256"] for m in r["missing"]] == [gone] and r["missing"][0]["what"] == "file version"
    assert [m["sha256"] for m in r["damaged"]] == [bad]
    assert restore.main([str(bk), str(tmp_path / "again")]) == 1
    out = capsys.readouterr()
    assert "Restore incomplete: 1 stored files are missing and 1 do not match" in out.out
    assert f"missing file version: {gone}" in out.out


def test_restore_a_chosen_database_copy(data, tmp_path):
    bk = tmp_path / "backup"
    first = backup.backup(bk)
    older = first.with_name("kosha-20000101-000000.db")
    first.rename(older)
    db.execute("INSERT INTO folders (parent_id, name, created_at) VALUES (1, 'Pumps', ?)", (db.now(),))
    db.close_thread_connection()
    backup.backup(bk)
    newest = restore.restore(bk, tmp_path / "newest")
    chosen = restore.restore(bk, tmp_path / "chosen", db_name=older.name)
    assert chosen["database_copy"] == older.name and chosen["ok"]
    count = lambda p: sqlite3.connect(p / "kosha.db").execute("SELECT COUNT(*) FROM folders").fetchone()[0]  # noqa: E731
    assert count(tmp_path / "newest") == count(tmp_path / "chosen") + 1 and newest["ok"]
    with pytest.raises(restore.RestoreError, match="was not found"):
        restore.restore(bk, tmp_path / "x", db_name="kosha-19990101-000000.db")


def test_unreadable_database_copy_fails_the_check(data, tmp_path):
    bk = tmp_path / "backup"
    copy = backup.backup(bk)
    copy.write_bytes(b"this is not a database" * 100)
    r = restore.restore(bk, tmp_path / "restored")
    assert not r["ok"] and "integrity check" in r["message"]


def test_backup_and_restore_test_are_recorded(data, tmp_path):
    bk = tmp_path / "backup"
    assert backup.main([str(bk)]) == 0
    last = db.get_setting(backup.LAST_BACKUP)
    assert last["ok"] and last["new_files"] == 6 and last["folder"] == str(bk.resolve())
    work = tmp_path / "work"
    r = backup.verify_backup(bk, work)
    assert r["ok"]
    db.close_thread_connection()
    rec = db.get_setting(backup.LAST_RESTORE_TEST)
    assert rec["ok"] and rec["files_checked"] == 6 and rec["file_versions"] == 3 and rec["integrity"] == "ok"
    assert list(work.iterdir()) == []                                  # the temporary restore is gone
    assert backup.main(["--verify", str(bk), "--work", str(work)]) == 0

    empty = tmp_path / "empty-backup"
    empty.mkdir()
    assert backup.main(["--verify", str(empty), "--work", str(work)]) == 1
    db.close_thread_connection()
    rec = db.get_setting(backup.LAST_RESTORE_TEST)
    assert not rec["ok"] and "no database copy" in rec["message"]
    assert list(work.iterdir()) == []


def test_a_failed_backup_is_recorded(data, tmp_path):
    sha = _sha(b"housing")
    store.path_for(sha).write_bytes(b"bit rot")
    assert backup.main([str(tmp_path / "backup")]) == 1
    db.close_thread_connection()
    last = db.get_setting(backup.LAST_BACKUP)
    assert not last["ok"] and "did not copy correctly" in last["message"]


def test_recording_never_creates_a_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "none" / "kosha.db")
    assert backup.record(backup.LAST_BACKUP, {"ok": True}) is False
    assert not (tmp_path / "none").exists()


def test_restored_folder_of_an_export_round_trip(data, tmp_path):
    """The export holds everything the restore check holds: every referenced file, by fingerprint."""
    bk = tmp_path / "backup"
    backup.backup(bk)
    out = tmp_path / "k.zip"
    export.export(out)
    refs = restore.referenced(config.DB_PATH)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert all(f"files/{s[:2]}/{s}" in names for s in refs)
