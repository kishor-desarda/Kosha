# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inputs library: reference material (benchmarks, requirements, datasheets, photos, notes) with files,
tags, links to items and projects, restricted access, and text read from the files for search.

Files are kept in the content-addressed store (``input_files.blob_sha``); text and thumbnails are worked
out once per fingerprint (``input_content``), so identical files are read only once.
"""
SQL = """
CREATE TABLE IF NOT EXISTS inputs (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'other',
  source TEXT NOT NULL DEFAULT '',
  input_date TEXT,
  url TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  comment TEXT NOT NULL DEFAULT '',
  confidentiality TEXT NOT NULL DEFAULT 'normal',
  deleted INTEGER NOT NULL DEFAULT 0,
  deleted_by INTEGER,
  deleted_at TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_by INTEGER,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inputs_date ON inputs(input_date);
CREATE INDEX IF NOT EXISTS ix_inputs_kind ON inputs(kind);
CREATE INDEX IF NOT EXISTS ix_inputs_created_by ON inputs(created_by);

CREATE TABLE IF NOT EXISTS input_access (
  id INTEGER PRIMARY KEY,
  input_id INTEGER NOT NULL REFERENCES inputs(id),
  user_id INTEGER REFERENCES users(id),
  role TEXT
);
CREATE INDEX IF NOT EXISTS ix_input_access_input ON input_access(input_id);

CREATE TABLE IF NOT EXISTS input_files (
  id INTEGER PRIMARY KEY,
  input_id INTEGER NOT NULL REFERENCES inputs(id),
  position INTEGER NOT NULL,
  filename TEXT NOT NULL,
  ext TEXT NOT NULL DEFAULT '',
  blob_sha TEXT NOT NULL,
  size INTEGER NOT NULL,
  removed INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (input_id, position)
);
CREATE INDEX IF NOT EXISTS ix_input_files_sha ON input_files(blob_sha);

CREATE TABLE IF NOT EXISTS input_content (
  sha TEXT PRIMARY KEY,
  file_type TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  method TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  text_sha TEXT,
  excerpt TEXT NOT NULL DEFAULT '',
  chars INTEGER NOT NULL DEFAULT 0,
  pages INTEGER,
  thumb_sha TEXT,
  thumb_kind TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_input_content_status ON input_content(status);

CREATE TABLE IF NOT EXISTS input_tags (
  input_id INTEGER NOT NULL REFERENCES inputs(id),
  tag TEXT NOT NULL,
  tag_key TEXT NOT NULL,
  PRIMARY KEY (input_id, tag_key)
);
CREATE INDEX IF NOT EXISTS ix_input_tags_key ON input_tags(tag_key);

CREATE TABLE IF NOT EXISTS input_items (
  input_id INTEGER NOT NULL REFERENCES inputs(id),
  item_id INTEGER NOT NULL REFERENCES items(id),
  created_by INTEGER,
  created_at TEXT NOT NULL,
  PRIMARY KEY (input_id, item_id)
);
CREATE INDEX IF NOT EXISTS ix_input_items_item ON input_items(item_id);

CREATE TABLE IF NOT EXISTS input_projects (
  input_id INTEGER NOT NULL REFERENCES inputs(id),
  project_id INTEGER NOT NULL REFERENCES projects(id),
  created_by INTEGER,
  created_at TEXT NOT NULL,
  PRIMARY KEY (input_id, project_id)
);
CREATE INDEX IF NOT EXISTS ix_input_projects_project ON input_projects(project_id);
"""
