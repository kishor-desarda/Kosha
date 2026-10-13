# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-app notifications: one generic table that every feature writes to through ``notifications.notify``."""
SQL = """
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  link TEXT NOT NULL DEFAULT '',
  actor_id INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  read_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, read_at);
"""
