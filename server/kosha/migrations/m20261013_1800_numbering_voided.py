# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reserved numbers that nothing was made from within 30 days are voided: they stay in the ledger with the date
they were voided, so they are never handed out again.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(number_allocations)").fetchall()}
    if "voided_at" not in cols:
        conn.execute("ALTER TABLE number_allocations ADD COLUMN voided_at TEXT")
