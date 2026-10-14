# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quality: nonconformance reports, corrective and preventive actions, FMEA.

ncrs                 a part that does not match its drawing (NCR-YY-NNNN), its disposition and how it was closed
capas                a corrective or preventive action (CAPA-YY-NNNN): problem, containment, root cause, effectiveness
capa_actions         the actions of a CAPA, each with an owner and a due date; removing one only marks it
fmeas, fmea_rows     design or process FMEA of a part, one row per failure mode with scores before and after
quality_attachments  photos and files on NCRs and CAPAs (content in the file store); removing one only marks it
quality_numbers      the last number used per kind and year, so numbers are never reused

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS ncrs (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  item_id INTEGER REFERENCES items(id),
  revision TEXT NOT NULL DEFAULT '',
  found_at TEXT NOT NULL DEFAULT 'production',
  batch TEXT NOT NULL DEFAULT '',
  qty REAL,
  supplier TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'open',
  disposition TEXT,
  disposition_note TEXT NOT NULL DEFAULT '',
  deviation_id INTEGER REFERENCES deviations(id),
  dispositioned_by INTEGER REFERENCES users(id),
  dispositioned_at TEXT,
  close_note TEXT NOT NULL DEFAULT '',
  closed_by INTEGER REFERENCES users(id),
  closed_at TEXT,
  capa_id INTEGER,
  raised_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ncrs_item ON ncrs(item_id);
CREATE INDEX IF NOT EXISTS ix_ncrs_state ON ncrs(state);

CREATE TABLE IF NOT EXISTS capas (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'corrective',
  problem TEXT NOT NULL DEFAULT '',
  containment TEXT NOT NULL DEFAULT '',
  root_cause TEXT NOT NULL DEFAULT '',
  item_id INTEGER REFERENCES items(id),
  owner_id INTEGER NOT NULL REFERENCES users(id),
  due_date TEXT,
  state TEXT NOT NULL DEFAULT 'open',
  effectiveness TEXT NOT NULL DEFAULT '',
  verified_by INTEGER REFERENCES users(id),
  closed_at TEXT,
  ecr_id INTEGER REFERENCES change_requests(id),
  raised_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_capas_state ON capas(state);
CREATE INDEX IF NOT EXISTS ix_ncrs_capa ON ncrs(capa_id);

CREATE TABLE IF NOT EXISTS capa_actions (
  id INTEGER PRIMARY KEY,
  capa_id INTEGER NOT NULL REFERENCES capas(id),
  text TEXT NOT NULL,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  due_date TEXT,
  done_at TEXT,
  done_by INTEGER REFERENCES users(id),
  note TEXT NOT NULL DEFAULT '',
  deleted INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_capa_actions_capa ON capa_actions(capa_id);
CREATE INDEX IF NOT EXISTS ix_capa_actions_owner ON capa_actions(owner_id);

CREATE TABLE IF NOT EXISTS fmeas (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  kind TEXT NOT NULL DEFAULT 'design',
  title TEXT NOT NULL,
  team TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fmeas_item ON fmeas(item_id);

CREATE TABLE IF NOT EXISTS fmea_rows (
  id INTEGER PRIMARY KEY,
  fmea_id INTEGER NOT NULL REFERENCES fmeas(id),
  position INTEGER NOT NULL DEFAULT 0,
  function TEXT NOT NULL DEFAULT '',
  failure_mode TEXT NOT NULL,
  effect TEXT NOT NULL DEFAULT '',
  severity INTEGER NOT NULL,
  cause TEXT NOT NULL DEFAULT '',
  occurrence INTEGER NOT NULL,
  prevention TEXT NOT NULL DEFAULT '',
  detection_control TEXT NOT NULL DEFAULT '',
  detection INTEGER NOT NULL,
  action TEXT NOT NULL DEFAULT '',
  owner_id INTEGER REFERENCES users(id),
  due_date TEXT,
  action_taken TEXT NOT NULL DEFAULT '',
  new_severity INTEGER,
  new_occurrence INTEGER,
  new_detection INTEGER
);
CREATE INDEX IF NOT EXISTS ix_fmea_rows_fmea ON fmea_rows(fmea_id);

CREATE TABLE IF NOT EXISTS quality_attachments (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  record_id INTEGER NOT NULL,
  filename TEXT NOT NULL,
  blob_sha TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  uploaded_by INTEGER NOT NULL REFERENCES users(id),
  uploaded_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  deleted_by INTEGER REFERENCES users(id),
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_quality_attachments_record ON quality_attachments(kind, record_id);

CREATE TABLE IF NOT EXISTS quality_numbers (
  kind TEXT NOT NULL,
  year INTEGER NOT NULL,
  last_value INTEGER NOT NULL DEFAULT 0,
  UNIQUE (kind, year)
);
"""
