# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change requests and the fuller change order (specification 7.4 to 7.6).

* ``change_requests``: *please change this* (ECR-YY-NNNN), raised by anyone from a part or file; accepted into a
  change order or declined with a reason.
* ``change_orders``: the request it came from, the three interchangeability answers, effectivity, the approval
  round and whom to tell when approvals wait too long.
* ``co_approvers``: the step in the route, when the person was asked, and when they were reminded or escalated.
* ``co_decisions``: every approval or rejection, kept for good (never updated or deleted).
* ``approval_routes``: who approves, per category, per project, or by default; one after another or all at once.
* ``users``: a delegate for leave, until a date.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS change_requests (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  document_id INTEGER REFERENCES documents(id),
  item_id INTEGER REFERENCES items(id),
  state TEXT NOT NULL DEFAULT 'open',
  raised_by INTEGER,
  raised_at TEXT NOT NULL,
  decided_by INTEGER,
  decided_at TEXT,
  decision_note TEXT NOT NULL DEFAULT '',
  co_id INTEGER REFERENCES change_orders(id)
);
CREATE INDEX IF NOT EXISTS ix_change_requests_state ON change_requests(state);

CREATE TABLE IF NOT EXISTS co_decisions (
  id INTEGER PRIMARY KEY,
  co_id INTEGER NOT NULL REFERENCES change_orders(id),
  round INTEGER NOT NULL DEFAULT 1,
  user_id INTEGER NOT NULL,
  on_behalf_of INTEGER,
  decision TEXT NOT NULL,
  comment TEXT NOT NULL DEFAULT '',
  revisions TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_co_decisions_co ON co_decisions(co_id);

CREATE TABLE IF NOT EXISTS approval_routes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  value TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT 'parallel',
  approvers_json TEXT NOT NULL DEFAULT '[]',
  lead_id INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
"""

_COLUMNS = {
    "change_orders": [("ecr_id", "INTEGER"), ("ic_fit", "TEXT NOT NULL DEFAULT ''"),
                      ("ic_mix", "TEXT NOT NULL DEFAULT ''"), ("ic_function", "TEXT NOT NULL DEFAULT ''"),
                      ("customer_only", "INTEGER NOT NULL DEFAULT 0"),
                      ("effectivity", "TEXT NOT NULL DEFAULT 'implemented'"), ("effective_date", "TEXT"),
                      ("effective_serial", "TEXT NOT NULL DEFAULT ''"), ("round", "INTEGER NOT NULL DEFAULT 1"),
                      ("route_mode", "TEXT NOT NULL DEFAULT 'parallel'"), ("lead_id", "INTEGER")],
    "co_approvers": [("step", "INTEGER NOT NULL DEFAULT 1"), ("asked_at", "TEXT"), ("reminded_at", "TEXT"),
                     ("escalated_at", "TEXT"), ("decided_by", "INTEGER")],
    "users": [("delegate_id", "INTEGER"), ("delegate_until", "TEXT")],
}


def up(conn) -> None:
    for table, cols in _COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, kind in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
