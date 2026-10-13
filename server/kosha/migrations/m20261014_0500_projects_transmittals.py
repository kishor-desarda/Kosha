# SPDX-License-Identifier: AGPL-3.0-or-later
"""Projects from ERP sales orders with their folder set (specification 6.2), and transmittals / vendor packages: a
record of which revisions went to whom, when, why and for which order (6.9).

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS transmittals (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'vendor_package',
  recipient TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT '',
  order_ref TEXT NOT NULL DEFAULT '',
  project_id INTEGER REFERENCES projects(id),
  share_id INTEGER REFERENCES share_links(id),
  files_json TEXT NOT NULL,
  sheet_sha TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  superseded_json TEXT NOT NULL DEFAULT '[]',
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  closed_at TEXT,
  closed_by INTEGER
);
CREATE INDEX IF NOT EXISTS ix_transmittals_project ON transmittals(project_id);
"""


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    for name, kind in (("sales_order", "TEXT NOT NULL DEFAULT ''"), ("folder_id", "INTEGER")):
        if name not in cols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {kind}")
