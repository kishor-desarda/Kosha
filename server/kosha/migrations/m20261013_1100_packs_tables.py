# SPDX-License-Identifier: AGPL-3.0-or-later
"""Industry packs (what each pack installed, and what the company changed) and the part-number ledger.

* packs           one row per pack ever installed; status 'installed' or 'removed' (rows are never deleted)
* pack_records    exactly what the installed version of each pack put in (replaced on update)
* pack_local      the company's own changes and additions; a pack update or removal never touches them
* pack_history    install, update and remove, with a summary of what changed
* number_allocations  every part number Kosha handed out; unique, so a number is never issued twice
"""
SQL = """
CREATE TABLE IF NOT EXISTS packs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  licence TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'installed',
  seq INTEGER NOT NULL DEFAULT 0,
  installed_by INTEGER,
  installed_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pack_records (
  pack_id TEXT NOT NULL REFERENCES packs(id),
  kind TEXT NOT NULL,
  rkey TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  value_json TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  pos INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (pack_id, kind, rkey)
);
CREATE INDEX IF NOT EXISTS ix_pack_records_kind ON pack_records(kind, scope);

CREATE TABLE IF NOT EXISTS pack_local (
  kind TEXT NOT NULL,
  rkey TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  value_json TEXT,
  hidden INTEGER NOT NULL DEFAULT 0,
  base_json TEXT,
  updated_by INTEGER,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (kind, rkey)
);

CREATE TABLE IF NOT EXISTS pack_history (
  id INTEGER PRIMARY KEY,
  pack_id TEXT NOT NULL,
  action TEXT NOT NULL,
  from_version TEXT,
  to_version TEXT,
  report_json TEXT NOT NULL DEFAULT '{}',
  user_id INTEGER,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pack_history_pack ON pack_history(pack_id);

CREATE TABLE IF NOT EXISTS number_allocations (
  id INTEGER PRIMARY KEY,
  part_number TEXT NOT NULL UNIQUE,
  scheme TEXT NOT NULL,
  counter_key TEXT,
  seq INTEGER,
  spec_code TEXT,
  values_json TEXT NOT NULL DEFAULT '{}',
  item_id INTEGER REFERENCES items(id),
  allocated_by INTEGER,
  allocated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alloc_spec ON number_allocations(spec_code);
CREATE INDEX IF NOT EXISTS ix_alloc_item ON number_allocations(item_id);
CREATE INDEX IF NOT EXISTS ix_alloc_scheme ON number_allocations(scheme);
"""
