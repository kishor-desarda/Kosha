# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy design (spec 6.8): one row per finished copy, keyed by the client's idempotency key.

A copy is written in one transaction together with its copy_jobs row, so after a crash either
everything exists (and re-running with the same key returns the stored result) or nothing does.
copy_links records which item, document and BOM each copy came from.
"""
SQL = """
CREATE TABLE IF NOT EXISTS copy_jobs (
  id INTEGER PRIMARY KEY,
  idem_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'done',
  root_item_id INTEGER,
  folder_id INTEGER,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_copy_jobs_key ON copy_jobs(idem_key);

CREATE TABLE IF NOT EXISTS copy_links (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES copy_jobs(id),
  kind TEXT NOT NULL,
  source_id INTEGER NOT NULL,
  source_revision TEXT NOT NULL DEFAULT '',
  copy_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_copy_links_copy ON copy_links(kind, copy_id);
CREATE INDEX IF NOT EXISTS ix_copy_links_source ON copy_links(kind, source_id);
"""
