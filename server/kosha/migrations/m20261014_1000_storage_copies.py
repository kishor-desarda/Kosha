# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage add-ins (specification 20): which stored file has a confirmed copy in which storage add-in.

storage_copies    one row per file fingerprint and storage add-in: copied (confirmed) or failed (with the
                  number of tries and the reason). The copies are made in the background and used to bring
                  back a stored file that went missing or was damaged.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS storage_copies (
  sha TEXT NOT NULL,
  addin_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'copied',
  tries INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  copied_at TEXT,
  PRIMARY KEY (sha, addin_id)
);
CREATE INDEX IF NOT EXISTS ix_storage_copies_status ON storage_copies(addin_id, status);
"""
