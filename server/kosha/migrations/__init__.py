# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned database changes.

Each change is one module in this package named ``m<YYYYMMDD>_<HHMM>_<area>_<what>.py`` with either
``SQL`` (a string of statements) or ``def up(conn)``, or both (SQL runs first). Changes run once, in
file-name order, each in its own transaction, and are recorded in ``schema_migrations``. A change that
fails is rolled back completely and the server refuses to start, so a database is never left half-changed.

Rules: never edit a change that has been released; add a new one. Only add tables, columns and indexes;
business records are never deleted by a change.

Write SQL the SQLite way; on PostgreSQL every statement (also those run by ``up(conn)``) is translated
(``kosha/pgsql.py``). For what has no translation, give ``SQL_POSTGRESQL`` (run as it is instead of ``SQL``
there) or check ``getattr(conn, "dialect", "sqlite")`` in ``up``.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

log = logging.getLogger("kosha.migrations")


def _statements(sql: str) -> list[str]:
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ValueError(f"incomplete SQL statement at the end of a migration: {buf.strip()[:80]}")
    return out


def pending(conn) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r[0] for r in conn.execute("SELECT id FROM schema_migrations").fetchall()}
    names = sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name.startswith("m"))
    return [n for n in names if n not in done]


def apply(conn, only: Iterable[str] | None = None) -> list[str]:
    """Apply the pending changes (or only those named in `only`, for copying a database)."""
    applied = []
    dialect = getattr(conn, "dialect", "sqlite")
    wanted = None if only is None else set(only)
    for name in pending(conn):
        if wanted is not None and name not in wanted:
            continue
        mod = importlib.import_module(f"{__name__}.{name}")
        native = getattr(mod, f"SQL_{dialect.upper()}", None) if dialect != "sqlite" else None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if native is not None:
                conn.execute_native(native)          # written for this engine: run as it is
            else:
                for stmt in _statements(getattr(mod, "SQL", "") or ""):
                    conn.execute(stmt)
            if hasattr(mod, "up"):
                mod.up(conn)
            conn.execute("INSERT INTO schema_migrations(id, applied_at) VALUES (?, ?)",
                         (name, datetime.now(timezone.utc).replace(microsecond=0).isoformat()))
        except BaseException:
            conn.execute("ROLLBACK")
            log.exception("Database change %s failed and was rolled back; nothing was changed", name)
            raise
        conn.execute("COMMIT")
        applied.append(name)
        log.info("Applied database change %s", name)
    return applied
