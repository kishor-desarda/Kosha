# SPDX-License-Identifier: AGPL-3.0-or-later
"""BOM views and the installed base (specification 6.10, 6.12 and 4.2): when each BOM was released (for the "as of a
date" view), and machine builds with their serial number and as-built BOM.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS machine_builds (
  id INTEGER PRIMARY KEY,
  serial TEXT NOT NULL UNIQUE,
  item_id INTEGER NOT NULL REFERENCES items(id),
  bom_id INTEGER REFERENCES boms(id),
  revision TEXT NOT NULL DEFAULT '',
  customer TEXT NOT NULL DEFAULT '',
  site TEXT NOT NULL DEFAULT '',
  order_ref TEXT NOT NULL DEFAULT '',
  built_on TEXT NOT NULL,
  commissioned_on TEXT,
  note TEXT NOT NULL DEFAULT '',
  lines_json TEXT NOT NULL DEFAULT '[]',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  deleted_by INTEGER
);
CREATE INDEX IF NOT EXISTS ix_machine_builds_item ON machine_builds(item_id);
"""


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(boms)").fetchall()}
    if "released_at" not in cols:
        conn.execute("ALTER TABLE boms ADD COLUMN released_at TEXT")
    conn.execute("UPDATE boms SET released_at = updated_at WHERE state = 'released' AND released_at IS NULL")
