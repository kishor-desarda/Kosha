# SPDX-License-Identifier: AGPL-3.0-or-later
"""Directory sign-in (LDAP / Active Directory, OpenID Connect add-ins).

``user_identities`` links a Kosha user to an account in a directory, by the directory's own stable id
(``external_id``), never by name. A link an admin prepares by directory user name has no ``external_id``
until that person first signs in through the directory. Unlinking keeps the row (``unlinked_at``).

``sign_in_requests`` holds one round trip to a redirect provider: the ``state`` sent along (only its
SHA-256), the browser that started it (SHA-256 of a cookie), what the provider needs back, and the
one-time ticket the browser redeems for a session afterwards. Rows are forgotten after a day.
"""
SQL = """
CREATE TABLE IF NOT EXISTS user_identities (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL,
  external_id TEXT,
  directory_username TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  groups_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  created_by INTEGER,
  last_sign_in_at TEXT,
  unlinked_at TEXT,
  unlinked_by INTEGER
);
CREATE INDEX IF NOT EXISTS ix_user_identities_user ON user_identities(user_id);
CREATE INDEX IF NOT EXISTS ix_user_identities_external ON user_identities(provider, external_id);

CREATE TABLE IF NOT EXISTS sign_in_requests (
  id INTEGER PRIMARY KEY,
  state_key TEXT NOT NULL UNIQUE,
  browser_key TEXT NOT NULL,
  provider TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  saved_json TEXT NOT NULL DEFAULT '{}',
  ip TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  returned_at TEXT,
  ticket_key TEXT UNIQUE,
  user_id INTEGER,
  error TEXT NOT NULL DEFAULT '',
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_sign_in_requests_created ON sign_in_requests(created_at);
CREATE INDEX IF NOT EXISTS ix_sign_in_requests_ip ON sign_in_requests(ip, created_at);
"""
