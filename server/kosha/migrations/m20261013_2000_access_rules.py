# SPDX-License-Identifier: AGPL-3.0-or-later
"""Roles and access (specification sections 2 and 15.2):

* users get a kind (person, vendor or service account), a view-only flag (preview but no download), an end date
  after which the account stops working, and the company an outside person works for;
* user_roles: the extra roles a person holds besides the main one (data steward, spares and service, quality and
  purchase); roles stack;
* user_projects: the projects a vendor account may see (nothing else);
* access_rules and access_rule_members: a folder, project, customer or category limited to the people and roles
  listed. A folder or project rule hides what is in it from everyone else; a customer rule lists who may see that
  customer's restricted parts; a category rule hides parts of that category.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

COLUMNS = [
    ("kind", "TEXT NOT NULL DEFAULT 'person'"),
    ("view_only", "INTEGER NOT NULL DEFAULT 0"),
    ("expires_on", "TEXT"),
    ("company", "TEXT NOT NULL DEFAULT ''"),
]

SQL = """
CREATE TABLE IF NOT EXISTS user_roles (
  user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL,
  PRIMARY KEY (user_id, role)
);
CREATE TABLE IF NOT EXISTS user_projects (
  user_id INTEGER NOT NULL REFERENCES users(id),
  project_id INTEGER NOT NULL REFERENCES projects(id),
  PRIMARY KEY (user_id, project_id)
);
CREATE TABLE IF NOT EXISTS access_rules (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL,
  value TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  deleted INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_access_rules_scope ON access_rules(scope, value);
CREATE TABLE IF NOT EXISTS access_rule_members (
  rule_id INTEGER NOT NULL REFERENCES access_rules(id),
  member TEXT NOT NULL,
  PRIMARY KEY (rule_id, member)
);
"""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for name, decl in COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
