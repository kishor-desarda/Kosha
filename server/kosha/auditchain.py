# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checking the audit log's chain (specification 15.5).

Every entry carries the fingerprint of the one before it (``kosha.db.audit``). Walking the log and recomputing each
fingerprint shows whether any entry was changed, removed or slipped in afterwards, and where.
"""
from __future__ import annotations

from . import db

BATCH = 5000


def verify() -> dict:
    """{ok, checked, broken_at (the first entry that does not fit), reason}."""
    prev = db.AUDIT_GENESIS
    checked, last_id = 0, 0
    while True:
        rows = db.rows("SELECT id, at, user_id, action, entity, entity_id, details, prev_hash, hash FROM audit "
                       "WHERE id > ? ORDER BY id LIMIT ?", (last_id, BATCH))
        if not rows:
            break
        for r in rows:
            if r["prev_hash"] != prev:
                return {"ok": False, "checked": checked, "broken_at": r["id"], "at": r["at"],
                        "reason": "The entry before this one was changed or removed."}
            want = db.audit_hash(prev, r["at"], r["user_id"], r["action"], r["entity"], r["entity_id"], r["details"])
            if r["hash"] != want:
                return {"ok": False, "checked": checked, "broken_at": r["id"], "at": r["at"],
                        "reason": "This entry was changed after it was written."}
            prev = r["hash"]
            checked += 1
            last_id = r["id"]
    return {"ok": True, "checked": checked, "broken_at": None, "at": None, "reason": ""}
