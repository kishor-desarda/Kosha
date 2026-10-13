# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP reconciliation: one row per check run, one row per difference found (with what was done about it)."""
SQL = """
CREATE TABLE IF NOT EXISTS erp_recon_runs (
  id INTEGER PRIMARY KEY,
  connector TEXT NOT NULL DEFAULT '',
  connector_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'running',
  phase TEXT NOT NULL DEFAULT '',
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  items_checked INTEGER NOT NULL DEFAULT 0,
  boms_checked INTEGER NOT NULL DEFAULT 0,
  differences INTEGER NOT NULL DEFAULT 0,
  fixable INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  cancel INTEGER NOT NULL DEFAULT 0,
  started_by INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS erp_recon_rows (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES erp_recon_runs(id),
  part_number TEXT NOT NULL,
  item_id INTEGER,
  bom_id INTEGER,
  scope TEXT NOT NULL,
  check_code TEXT NOT NULL,
  kosha_value TEXT NOT NULL DEFAULT '',
  erp_value TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL,
  advice TEXT NOT NULL DEFAULT '',
  fix_kind TEXT NOT NULL DEFAULT '',
  fix_entity_id INTEGER,
  resend_job_id INTEGER,
  resent_by INTEGER,
  resent_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_erp_recon_rows_run ON erp_recon_rows(run_id);
"""
