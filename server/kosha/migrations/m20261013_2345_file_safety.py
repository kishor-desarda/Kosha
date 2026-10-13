# SPDX-License-Identifier: AGPL-3.0-or-later
"""File safety (specification 8.4, 13.5 and 16.2).

* ``upload_sessions``: large files arrive in pieces that can resume after a network break. The client names the
  SHA-256 of the whole file; Kosha checks it before the file is accepted.
* ``documents.deleted_at``, ``deleted_by``, ``deleted_path``: deleted files wait in the recycle bin, with the folder
  they came from (so they can go back even when the folder was deleted too). ``purged_at``: an admin's purge, which
  a second admin confirmed, removed the file's content for good; the record and its history stay.
* ``purge_requests``: one admin asks, another confirms.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS upload_sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  filename TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  received INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  document_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_upload_sessions_status ON upload_sessions(status, updated_at);

CREATE TABLE IF NOT EXISTS purge_requests (
  id INTEGER PRIMARY KEY,
  document_ids_json TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  requested_by INTEGER NOT NULL,
  requested_at TEXT NOT NULL,
  decided_by INTEGER,
  decided_at TEXT,
  status TEXT NOT NULL DEFAULT 'waiting'
);
"""

COLUMNS = (("deleted_at", "TEXT"), ("deleted_by", "INTEGER"), ("deleted_path", "TEXT"), ("purged_at", "TEXT"))


def up(conn) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    for name, kind in COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {kind}")
    # files deleted before the recycle bin existed: in the bin since their last change
    conn.execute("UPDATE documents SET deleted_at = updated_at WHERE deleted = 1 AND deleted_at IS NULL")
