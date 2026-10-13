# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each user's theme for the web client: '' (the installation's default), system, light, dark or contrast.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""


def up(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "ui_theme" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN ui_theme TEXT NOT NULL DEFAULT ''")
