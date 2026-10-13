# SPDX-License-Identifier: AGPL-3.0-or-later
"""Legacy migration batches (specification 11): one record per source file with its quality score and work queue,
cross-references that keep old paths and numbers searchable, and on each import job the count check, the sample
audit, the sign-off and the undo.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS legacy_records (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES import_jobs(id),
  source_path TEXT NOT NULL,
  status TEXT NOT NULL,
  sha TEXT,
  document_id INTEGER REFERENCES documents(id),
  part_number TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  material TEXT NOT NULL DEFAULT '',
  revision TEXT NOT NULL DEFAULT '',
  score INTEGER,
  queue TEXT,
  reasons_json TEXT NOT NULL DEFAULT '[]',
  message TEXT NOT NULL DEFAULT '',
  confirmed_by INTEGER REFERENCES users(id),
  confirmed_at TEXT,
  sample_result TEXT,
  sample_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_legacy_records_batch ON legacy_records(batch_id, queue);
CREATE INDEX IF NOT EXISTS ix_legacy_records_doc ON legacy_records(document_id);
CREATE TABLE IF NOT EXISTS legacy_refs (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER REFERENCES import_jobs(id),
  document_id INTEGER REFERENCES documents(id),
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(document_id, kind, value)
);
CREATE INDEX IF NOT EXISTS ix_legacy_refs_value ON legacy_refs(value);
"""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(import_jobs)").fetchall()}
    for name, decl in (("found", "INTEGER NOT NULL DEFAULT 0"), ("junk", "INTEGER NOT NULL DEFAULT 0"),
                       ("unpacked", "INTEGER NOT NULL DEFAULT 0"), ("signed_off_by", "INTEGER"),
                       ("signed_off_at", "TEXT"), ("undone_by", "INTEGER"), ("undone_at", "TEXT"),
                       ("verify_json", "TEXT")):
        if name not in cols:
            conn.execute(f"ALTER TABLE import_jobs ADD COLUMN {name} {decl}")
