# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server-side sessions: each sign-in token names one row here, so sessions can be ended at once.

``id`` is the SHA-256 of the session id carried in the token (the token itself is never stored).
"""
SQL = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  ended_at TEXT,
  ended_reason TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
"""
