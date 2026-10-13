# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security (specification 15.1, 15.3, 15.5 and 15.7).

* Two-step sign-in with an authenticator app: ``users.totp_secret`` (on), ``totp_pending`` (being set up),
  ``totp_last_step`` (a code is used once), ``totp_recovery_json`` (fingerprints of the one-time recovery codes).
* ``users.downloads_paused_at``: the bulk-download alarm paused this person's downloads until an admin lets them
  continue.
* ``sign_in_places``: where each person signs in from, so admins hear about a sign-in from a new place.
* ``audit.prev_hash`` and ``audit.hash``: each entry carries a fingerprint of the one before it, so changing or
  removing an entry breaks the chain. Existing entries are chained in their order.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""
import hashlib
import json

SQL = """
CREATE TABLE IF NOT EXISTS sign_in_places (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  place TEXT NOT NULL,
  first_at TEXT NOT NULL,
  last_at TEXT NOT NULL,
  last_ip TEXT NOT NULL DEFAULT '',
  user_agent TEXT NOT NULL DEFAULT '',
  UNIQUE(user_id, place)
);
"""

USER_COLUMNS = (("totp_secret", "TEXT"), ("totp_pending", "TEXT"), ("totp_enabled_at", "TEXT"),
                ("totp_last_step", "INTEGER"), ("totp_recovery_json", "TEXT"), ("downloads_paused_at", "TEXT"))
GENESIS = "0" * 64


def entry_hash(prev: str, at, user_id, action, entity, entity_id, details) -> str:
    """The fingerprint of one audit entry (kept equal to kosha.db.audit_hash)."""
    body = json.dumps([at, user_id, action, entity, entity_id, details], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev + "|" + body).encode("utf-8")).hexdigest()


def up(conn) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for name, kind in USER_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {kind}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(audit)").fetchall()}
    for name in ("prev_hash", "hash"):
        if name not in have:
            conn.execute(f"ALTER TABLE audit ADD COLUMN {name} TEXT")
    prev = GENESIS
    rows = conn.execute("SELECT id, at, user_id, action, entity, entity_id, details, hash FROM audit "
                        "ORDER BY id").fetchall()
    for r in rows:
        if r[7]:
            prev = r[7]
            continue
        h = entry_hash(prev, r[1], r[2], r[3], r[4], r[5], r[6])
        conn.execute("UPDATE audit SET prev_hash = ?, hash = ? WHERE id = ?", (prev, h, r[0]))
        prev = h
