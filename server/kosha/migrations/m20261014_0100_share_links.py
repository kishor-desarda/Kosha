# SPDX-License-Identifier: AGPL-3.0-or-later
"""Share links for people outside the company (specification 15.3): released files, a recipient, an end date, an
optional password, view-only or download, withdrawn at once. Only a fingerprint of each link's secret is kept.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS share_links (
  id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  token_prefix TEXT NOT NULL,
  recipient TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  files_json TEXT NOT NULL,
  expires_on TEXT NOT NULL,
  password_hash TEXT,
  view_only INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  opened_count INTEGER NOT NULL DEFAULT 0,
  download_count INTEGER NOT NULL DEFAULT 0,
  last_opened_at TEXT,
  withdrawn_at TEXT,
  withdrawn_by INTEGER
);
CREATE INDEX IF NOT EXISTS ix_share_links_creator ON share_links(created_by);
"""
