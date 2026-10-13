# SPDX-License-Identifier: AGPL-3.0-or-later
"""Failed sign-ins of the last day, used to pause sign-in after too many failures (per user name and address)."""
SQL = """
CREATE TABLE IF NOT EXISTS sign_in_failures (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  username TEXT NOT NULL,
  ip TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sign_in_failures_user ON sign_in_failures(username, at);
CREATE INDEX IF NOT EXISTS ix_sign_in_failures_ip ON sign_in_failures(ip, at);
CREATE INDEX IF NOT EXISTS ix_sign_in_failures_at ON sign_in_failures(at);
"""
