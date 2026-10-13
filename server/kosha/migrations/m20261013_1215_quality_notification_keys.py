# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-app notifications remember the notice they came from, so a notice queued twice shows once under the bell.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(notifications)").fetchall()}
    if "notice_key" not in cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN notice_key TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_notifications_key ON notifications(user_id, notice_key)")
