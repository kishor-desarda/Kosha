# SPDX-License-Identifier: AGPL-3.0-or-later
"""Health checks (specification 16.5 and 16.6): when each stored file was last read back and checked against its
fingerprint, and what was found or repaired.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS blob_checks (
  sha TEXT PRIMARY KEY,
  checked_at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  size INTEGER,
  repaired_at TEXT,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_blob_checks_at ON blob_checks(checked_at);
"""
