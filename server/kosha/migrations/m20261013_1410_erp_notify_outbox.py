# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notification outbox: queued deliveries (one per channel and person), every delivery attempt, and each
user's choice of channels per kind of notice."""
SQL = """
CREATE TABLE IF NOT EXISTS notify_outbox (
  id INTEGER PRIMARY KEY,
  dedupe_key TEXT NOT NULL UNIQUE,
  notice_key TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'general',
  channel TEXT NOT NULL,
  user_id INTEGER,
  address TEXT NOT NULL DEFAULT '',
  recipients_json TEXT NOT NULL DEFAULT '[]',
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  link TEXT NOT NULL DEFAULT '',
  severity TEXT NOT NULL DEFAULT 'info',
  source TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_notify_outbox_due ON notify_outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS notify_attempts (
  id INTEGER PRIMARY KEY,
  outbox_id INTEGER NOT NULL REFERENCES notify_outbox(id),
  at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS ix_notify_attempts_outbox ON notify_attempts(outbox_id);

CREATE TABLE IF NOT EXISTS notify_prefs (
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,
  channels_json TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, kind)
);
"""
