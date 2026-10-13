# SPDX-License-Identifier: AGPL-3.0-or-later
"""The item master of the specification (sections 3 and 4):

* items get their product family, the spare / wear-part / recommended-spare flags, the customer with the
  customer's own part number, the restricted flag and the do-not-reuse lock, and the tax (HSN) code;
* oems: the short code the data steward gives each other maker whose machines the company makes spares for;
* item_oem_refs: OEM cross-references, so an OEM part number typed anywhere finds the company's part;
* item_reuse_approvals: a do-not-reuse part allowed into one other assembly, with who allowed it and why.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

COLUMNS = [
    ("family", "TEXT NOT NULL DEFAULT ''"),
    ("spare", "INTEGER NOT NULL DEFAULT 0"),
    ("wear_part", "INTEGER NOT NULL DEFAULT 0"),
    ("recommended_spare", "INTEGER NOT NULL DEFAULT 0"),
    ("customer", "TEXT NOT NULL DEFAULT ''"),
    ("customer_part_number", "TEXT NOT NULL DEFAULT ''"),
    ("restricted", "INTEGER NOT NULL DEFAULT 0"),
    ("no_reuse", "INTEGER NOT NULL DEFAULT 0"),
    ("hsn", "TEXT NOT NULL DEFAULT ''"),
]

SQL = """
CREATE TABLE IF NOT EXISTS oems (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_oem_refs (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  oem TEXT NOT NULL,
  oem_model TEXT NOT NULL DEFAULT '',
  oem_part_number TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  fit TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  deleted INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_oem_refs_item ON item_oem_refs(item_id);
CREATE INDEX IF NOT EXISTS ix_oem_refs_pn ON item_oem_refs(oem_part_number);
CREATE TABLE IF NOT EXISTS item_reuse_approvals (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  parent_item_id INTEGER NOT NULL REFERENCES items(id),
  reason TEXT NOT NULL,
  approved_by INTEGER,
  approved_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reuse_pair ON item_reuse_approvals(item_id, parent_item_id);
"""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    for name, decl in COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {decl}")
