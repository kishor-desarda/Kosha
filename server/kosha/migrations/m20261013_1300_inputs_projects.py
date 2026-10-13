# SPDX-License-Identifier: AGPL-3.0-or-later
"""Projects: a light record (code, name, customer, status, dates) that inputs and items belong to."""
SQL = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  code_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  customer TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  start_date TEXT,
  due_date TEXT,
  closed_at TEXT,
  description TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_projects_status ON projects(status);

CREATE TABLE IF NOT EXISTS project_items (
  project_id INTEGER NOT NULL REFERENCES projects(id),
  item_id INTEGER NOT NULL REFERENCES items(id),
  created_by INTEGER,
  created_at TEXT NOT NULL,
  PRIMARY KEY (project_id, item_id)
);
CREATE INDEX IF NOT EXISTS ix_project_items_item ON project_items(item_id);
"""
