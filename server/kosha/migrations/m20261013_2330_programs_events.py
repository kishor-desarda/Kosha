# SPDX-License-Identifier: AGPL-3.0-or-later
"""Programs and events (specification 13.2 and 13.4).

* ``api_keys``: keys for service accounts, with scopes (``items:read``, ``files:write`` ...), optional allowed
  addresses and an end date. Only a fingerprint of each key is kept; a revoked key stays listed.
* ``webhooks``: outside addresses that want Kosha's events, each with its signing secret.
* ``webhook_deliveries``: one row per event and address, sent in the background with retries, kept as a log that
  an admin can replay from.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  key_prefix TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  scopes_json TEXT NOT NULL DEFAULT '[]',
  allowed_ips TEXT NOT NULL DEFAULT '',
  expires_on TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  last_used_ip TEXT,
  revoked_at TEXT,
  revoked_by INTEGER
);
CREATE INDEX IF NOT EXISTS ix_api_keys_user ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS webhooks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  events_json TEXT NOT NULL DEFAULT '[]',
  secret TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  removed_at TEXT
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id INTEGER PRIMARY KEY,
  webhook_id INTEGER NOT NULL REFERENCES webhooks(id),
  event TEXT NOT NULL,
  event_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_at TEXT NOT NULL,
  last_status INTEGER,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT,
  replay_of INTEGER
);
CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_due ON webhook_deliveries(status, next_at);
CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_hook ON webhook_deliveries(webhook_id);
"""
