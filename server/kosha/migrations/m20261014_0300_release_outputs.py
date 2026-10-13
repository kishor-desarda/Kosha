# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release outputs (specification 7.3 and 8.3): the stamped PDF and the release sheet made at each release, stored
as files linked to the revision and version they came from.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS release_outputs (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  revision TEXT NOT NULL,
  version INTEGER NOT NULL,
  kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  blob_sha TEXT,
  size INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ok',
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(document_id, revision, version, kind)
);
CREATE INDEX IF NOT EXISTS ix_release_outputs_doc ON release_outputs(document_id);
"""
