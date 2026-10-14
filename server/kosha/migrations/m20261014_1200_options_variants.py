# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configurable products: options and their choices, rules on BOM lines, constraints, and saved variants.

config_options      a product's options (MOTOR, VOLTAGE …), in order
config_choices      each option's choices (5K5, 7K5 …)
config_rules        when a line is used: the product, the assembly and the part of the line, and the rule text;
                    kept apart from bom_lines so rules survive BOM edits, imports and revisions
config_constraints  combinations that are not allowed: if <rule> then <rule>, with the reason
variants            a saved configuration (<product>-V01 …) for an order, with its resolved BOM kept as it was
machine_builds      + variant_id: the machine was built as that variant

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS config_options (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  code TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  UNIQUE (item_id, code)
);
CREATE TABLE IF NOT EXISTS config_choices (
  id INTEGER PRIMARY KEY,
  option_id INTEGER NOT NULL REFERENCES config_options(id),
  code TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  UNIQUE (option_id, code)
);
CREATE TABLE IF NOT EXISTS config_rules (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  parent_item_id INTEGER NOT NULL REFERENCES items(id),
  child_item_id INTEGER NOT NULL REFERENCES items(id),
  rule TEXT NOT NULL,
  updated_by INTEGER,
  updated_at TEXT NOT NULL,
  UNIQUE (item_id, parent_item_id, child_item_id)
);
CREATE TABLE IF NOT EXISTS config_constraints (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  if_rule TEXT NOT NULL,
  then_rule TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS variants (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  number TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  choice_json TEXT NOT NULL DEFAULT '{}',
  summary TEXT NOT NULL DEFAULT '',
  bom_id INTEGER REFERENCES boms(id),
  revision TEXT NOT NULL DEFAULT '',
  lines_json TEXT NOT NULL DEFAULT '[]',
  customer TEXT NOT NULL DEFAULT '',
  order_ref TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  withdrawn_at TEXT,
  withdrawn_by INTEGER,
  withdrawn_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_variants_item ON variants(item_id);
CREATE INDEX IF NOT EXISTS ix_config_rules_item ON config_rules(item_id);
"""


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(machine_builds)").fetchall()}
    if "variant_id" not in cols:
        conn.execute("ALTER TABLE machine_builds ADD COLUMN variant_id INTEGER")
