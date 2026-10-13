# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP extended (specification 12): automatic retries, and what the ERP tells Kosha about its orders and parties.

* erp_jobs.next_try_at: when a failed job is tried again by itself (the wait grows after each failure);
* erp_orders and erp_order_lines: purchase orders, RFQs and subcontracting orders the ERP announced, with the
  revision of each part that was released when the order came in, so Kosha knows which revision went to which order;
* erp_parties: customers and suppliers as the ERP names them, for pickers, restrictions and vendor packages.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS erp_orders (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  number TEXT NOT NULL,
  party TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  transmittal_id INTEGER,
  received_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(kind, number)
);
CREATE TABLE IF NOT EXISTS erp_order_lines (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES erp_orders(id),
  code TEXT NOT NULL,
  item_id INTEGER REFERENCES items(id),
  revision TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_erp_order_lines_order ON erp_order_lines(order_id);
CREATE TABLE IF NOT EXISTS erp_parties (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  UNIQUE(kind, name)
);
"""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(erp_jobs)").fetchall()}
    if "next_try_at" not in cols:
        conn.execute("ALTER TABLE erp_jobs ADD COLUMN next_try_at TEXT")
