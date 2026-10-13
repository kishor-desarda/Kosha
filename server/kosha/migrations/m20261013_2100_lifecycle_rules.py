# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lifecycle rules (specification 7.1): a part made *spares only* names the part that replaces it in new machines
(items.replaced_by_item_id), and a change order line that makes a part spares only carries that replacement
(co_items.replacement, a part number).

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    if "replaced_by_item_id" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN replaced_by_item_id INTEGER REFERENCES items(id)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(co_items)").fetchall()}
    if "replacement" not in cols:
        conn.execute("ALTER TABLE co_items ADD COLUMN replacement TEXT NOT NULL DEFAULT ''")
