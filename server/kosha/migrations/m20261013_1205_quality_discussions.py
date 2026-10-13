# SPDX-License-Identifier: AGPL-3.0-or-later
"""Discussions: comment threads on items, document revisions, change orders, deviations and BOMs.

Comments are never deleted: removing one only marks it, and every edit keeps the earlier text.
"""
SQL = """
CREATE TABLE IF NOT EXISTS discussion_threads (
  id INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  revision TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_by INTEGER REFERENCES users(id),
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_discussion_threads_entity ON discussion_threads(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS discussion_comments (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES discussion_threads(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  edited_at TEXT,
  deleted_at TEXT,
  deleted_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS ix_discussion_comments_thread ON discussion_comments(thread_id);

CREATE TABLE IF NOT EXISTS discussion_comment_edits (
  id INTEGER PRIMARY KEY,
  comment_id INTEGER NOT NULL REFERENCES discussion_comments(id),
  body TEXT NOT NULL,
  replaced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_discussion_edits_comment ON discussion_comment_edits(comment_id);

CREATE TABLE IF NOT EXISTS discussion_mentions (
  comment_id INTEGER NOT NULL REFERENCES discussion_comments(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  PRIMARY KEY (comment_id, user_id)
);
"""
