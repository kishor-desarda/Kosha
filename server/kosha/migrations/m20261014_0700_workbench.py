# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workbench (specification 9.1): each person's favourites, saved views and screen preferences (accent colour,
text size, pinned pages). Personal settings only; nothing here changes a business record.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS user_favourites (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,
  ref_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, kind, ref_id)
);
CREATE TABLE IF NOT EXISTS saved_views (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS user_prefs (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  prefs_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
"""
