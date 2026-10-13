# SPDX-License-Identifier: AGPL-3.0-or-later
"""A safe API for programs (specification 13.1).

* ``row_version`` and ``updated_by`` on the records people and programs edit (items, files, change orders,
  deviations, BOMs, projects): an update that sends the version it read (If-Match) is refused when someone else
  changed the record in between.
* ``api_idempotency``: the answers to create calls sent with an Idempotency-Key, so a request sent twice (after a
  network drop) does its work once. Kept for a day.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS api_idempotency (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  idem_key TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  status INTEGER NOT NULL DEFAULT 0,
  response_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, idem_key)
);
CREATE INDEX IF NOT EXISTS ix_api_idempotency_created ON api_idempotency(created_at);
"""

VERSIONED = ("items", "documents", "change_orders", "deviations", "boms", "projects")


def up(conn) -> None:
    for table in VERSIONED:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "row_version" not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN row_version INTEGER NOT NULL DEFAULT 1")
        if "updated_by" not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_by INTEGER")
        if "updated_at" not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")
