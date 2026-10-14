# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manufacturing BOMs and process plans: how the workshop builds an assembly, next to how it was designed.

mboms        one per assembly and engineering revision: draft or released, the engineering BOM it follows and
             the sub-assemblies built in one go with their parent (flattened)
mbom_lines   the parts the workshop issues, each at the operation that uses it
mbom_ops     the process plan (routing): numbered operations at a work centre with set-up and run times

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS mboms (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  revision TEXT NOT NULL,
  ebom_id INTEGER REFERENCES boms(id),
  state TEXT NOT NULL DEFAULT 'draft',
  note TEXT NOT NULL DEFAULT '',
  flatten_json TEXT NOT NULL DEFAULT '[]',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  released_at TEXT,
  released_by INTEGER,
  UNIQUE (item_id, revision)
);
CREATE TABLE IF NOT EXISTS mbom_ops (
  id INTEGER PRIMARY KEY,
  mbom_id INTEGER NOT NULL REFERENCES mboms(id),
  op_no INTEGER NOT NULL,
  work_centre TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  setup_min REAL NOT NULL DEFAULT 0,
  run_min REAL NOT NULL DEFAULT 0,
  instructions TEXT NOT NULL DEFAULT '',
  UNIQUE (mbom_id, op_no)
);
CREATE TABLE IF NOT EXISTS mbom_lines (
  id INTEGER PRIMARY KEY,
  mbom_id INTEGER NOT NULL REFERENCES mboms(id),
  line_no INTEGER NOT NULL,
  child_item_id INTEGER NOT NULL REFERENCES items(id),
  qty REAL NOT NULL DEFAULT 1,
  uom TEXT NOT NULL DEFAULT '',
  op_no INTEGER,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_mbom_lines_mbom ON mbom_lines(mbom_id);
CREATE INDEX IF NOT EXISTS ix_mbom_lines_child ON mbom_lines(child_item_id);
"""
