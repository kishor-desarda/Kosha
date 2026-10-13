# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deviations: temporary, approved departures from the released design (DEV-YY-NNNN).

Numbers come from ``deviation_numbers`` (one running counter per year) and are never reused;
deviations are cancelled or closed, never deleted.
"""
SQL = """
CREATE TABLE IF NOT EXISTS deviation_numbers (
  year INTEGER PRIMARY KEY,
  last_value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS deviations (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  departure TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'draft',
  qty_limit REAL,
  qty_used REAL NOT NULL DEFAULT 0,
  serials TEXT NOT NULL DEFAULT '',
  valid_from TEXT,
  valid_until TEXT,
  requested_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  submitted_at TEXT,
  decided_at TEXT,
  activated_at TEXT,
  closed_at TEXT,
  closed_by INTEGER REFERENCES users(id),
  close_reason TEXT NOT NULL DEFAULT '',
  close_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_deviations_state ON deviations(state);

CREATE TABLE IF NOT EXISTS deviation_affected (
  id INTEGER PRIMARY KEY,
  deviation_id INTEGER NOT NULL REFERENCES deviations(id),
  item_id INTEGER REFERENCES items(id),
  document_id INTEGER REFERENCES documents(id),
  revision TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_deviation_affected_dev ON deviation_affected(deviation_id);
CREATE INDEX IF NOT EXISTS ix_deviation_affected_item ON deviation_affected(item_id);
CREATE INDEX IF NOT EXISTS ix_deviation_affected_doc ON deviation_affected(document_id);

CREATE TABLE IF NOT EXISTS deviation_approvers (
  id INTEGER PRIMARY KEY,
  deviation_id INTEGER NOT NULL REFERENCES deviations(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  decision TEXT NOT NULL DEFAULT 'pending',
  comment TEXT NOT NULL DEFAULT '',
  decided_at TEXT,
  UNIQUE(deviation_id, user_id)
);

CREATE TABLE IF NOT EXISTS deviation_usage (
  id INTEGER PRIMARY KEY,
  deviation_id INTEGER NOT NULL REFERENCES deviations(id),
  qty REAL NOT NULL,
  reference TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  user_id INTEGER REFERENCES users(id),
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_deviation_usage_dev ON deviation_usage(deviation_id);

CREATE TABLE IF NOT EXISTS deviation_attachments (
  id INTEGER PRIMARY KEY,
  deviation_id INTEGER NOT NULL REFERENCES deviations(id),
  filename TEXT NOT NULL,
  blob_sha TEXT NOT NULL,
  size INTEGER NOT NULL,
  uploaded_by INTEGER REFERENCES users(id),
  uploaded_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  deleted_by INTEGER REFERENCES users(id),
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_deviation_attachments_dev ON deviation_attachments(deviation_id);

CREATE TABLE IF NOT EXISTS deviation_changes (
  id INTEGER PRIMARY KEY,
  deviation_id INTEGER NOT NULL REFERENCES deviations(id),
  change_order_id INTEGER NOT NULL REFERENCES change_orders(id),
  relation TEXT NOT NULL DEFAULT 'related',
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(deviation_id, change_order_id)
);
"""
