# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy a Kosha installation from SQLite to PostgreSQL.

    python -m kosha.dbcopy --from /var/lib/kosha/kosha.db --to postgresql://kosha:secret@localhost:5432/kosha

Stop Kosha first. The tool copies every table of the SQLite database into an empty PostgreSQL
database: it creates the same tables and indexes (translated like all of Kosha's SQL), copies the rows
of tables linked by foreign keys together in one transaction per group, moves the id counters past
the copied ids, and then checks every table: the same number of rows and the same checksum on both
sides. It refuses to run if the target already holds data, and if anything fails it empties the
tables it filled, so the target is left as it was. The SQLite file is only read. Stored files (the
``blobs`` folder) are not in the database and stay where they are: keep ``KOSHA_DATA_DIR`` as it is
and add ``KOSHA_DATABASE_URL``.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path
from typing import Callable

from . import pgsql

BATCH = 2000
_MOD = 1 << 256


class CopyRefused(RuntimeError):
    """Nothing was changed: the source or the target is not suitable."""


class CopyFailed(RuntimeError):
    """The copy did not complete; the target was emptied again."""


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _canon(v) -> bytes:
    if v is None:
        return b"N"
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        v = int(v)
    if isinstance(v, int):
        return b"i" + str(v).encode()
    if isinstance(v, float):
        return b"f" + repr(v).encode()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return b"b" + bytes(v)
    return b"s" + str(v).encode("utf-8", "surrogatepass")


def row_hash(row) -> int:
    h = hashlib.sha256()
    for v in row:
        c = _canon(v)
        h.update(len(c).to_bytes(8, "big"))
        h.update(c)
    return int.from_bytes(h.digest(), "big")


def _checksum(rows_iter) -> tuple[int, str]:
    """Row count and an order-independent checksum (sum of row hashes)."""
    n, total = 0, 0
    for batch in rows_iter:
        for row in batch:
            n += 1
            total = (total + row_hash(row)) % _MOD
    return n, f"{total:064x}"


def _if_not_exists(sql: str) -> str:
    sql = re.sub(r"^\s*CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)", "CREATE TABLE IF NOT EXISTS ", sql, flags=re.I)
    return re.sub(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+(?!IF\s+NOT\s+EXISTS\b)",
                  lambda m: f"CREATE {m.group(1) or ''}INDEX IF NOT EXISTS ", sql, flags=re.I)


def _groups(tables: list[str], links: dict[str, set[str]]) -> list[list[str]]:
    """Tables joined by foreign keys (in either direction) form one group; parents come first."""
    parent = {t: t for t in tables}

    def find(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t
    for t, refs in links.items():
        for r in refs:
            if r in parent:
                parent[find(t)] = find(r)
    groups: dict[str, list[str]] = {}
    for t in tables:
        groups.setdefault(find(t), []).append(t)
    out = []
    for members in groups.values():
        ordered: list[str] = []
        pending = list(members)
        while pending:      # parents first; a loop of references is copied in the original order
            ready = [t for t in pending if not (links.get(t, set()) - {t}) - set(ordered)] or pending[:1]
            for t in ready:
                ordered.append(t)
                pending.remove(t)
        out.append(ordered)
    return out


def _open_source(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise CopyRefused(f"{path} does not exist. Give the path of Kosha's SQLite database (kosha.db in "
                          "the data folder).")
    try:
        src = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5, isolation_level=None)
        src.execute("BEGIN IMMEDIATE")      # nobody can change the source while it is copied
    except sqlite3.OperationalError as e:
        raise CopyRefused(f"The SQLite database is in use ({e}). Stop Kosha and run the copy again.") from e
    try:
        names = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError as e:
        src.close()
        raise CopyRefused(f"{path} is not a SQLite database ({e}).") from e
    if not {"users", "documents", "settings"} <= names:
        src.close()
        raise CopyRefused(f"{path} is not a Kosha database (it has no users, documents and settings tables).")
    ok = src.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        src.close()
        raise CopyRefused(f"The SQLite database failed its integrity check ({ok}). Restore it from a backup "
                          "first.")
    if src.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone()[0]:
        src.close()
        raise CopyRefused("The SQLite database has triggers or views, which this tool does not copy.")
    return src


def _target_tables(dst: pgsql.Connection) -> list[str]:
    return [r[0] for r in dst.raw.execute(
        "SELECT tablename::text FROM pg_tables WHERE schemaname = current_schema() ORDER BY tablename").fetchall()]


def _empty_target(dst: pgsql.Connection) -> None:
    full = [t for t in _target_tables(dst) if dst.raw.execute(f"SELECT EXISTS (SELECT 1 FROM {_q(t)})").fetchone()[0]]
    if full:
        raise CopyRefused("The PostgreSQL database already holds data (in " + ", ".join(full[:8]) +
                          ("..." if len(full) > 8 else "") + "). Copy only into a new, empty database; "
                          "nothing was changed.")


def copy_database(source: Path, target_url: str, log: Callable[[str], None] = print) -> list[dict]:
    """Copy and verify; returns one entry per table: name, rows, checksum."""
    src = _open_source(Path(source))
    try:
        try:
            dst = pgsql.connect(target_url, application_name="kosha-dbcopy")
        except sqlite3.OperationalError as e:
            raise CopyRefused(str(e)) from e
        try:
            return _copy(src, dst, log)
        finally:
            dst.close()
    finally:
        src.execute("ROLLBACK")
        src.close()


def _copy(src: sqlite3.Connection, dst: pgsql.Connection, log) -> list[dict]:
    # Kosha's write lock for the whole run: a Kosha server pointed at the target cannot write meanwhile
    try:
        dst.raw.execute(f"SELECT pg_advisory_lock({pgsql.WRITE_LOCK_KEY})")
    except Exception as e:
        raise CopyRefused(f"The PostgreSQL database is busy ({str(e).strip()}). Stop every Kosha server that uses "
                          "it and try again.") from e
    try:
        return _copy_locked(src, dst, log)
    finally:
        try:
            dst.raw.execute(f"SELECT pg_advisory_unlock({pgsql.WRITE_LOCK_KEY})")
        except Exception:
            pass


def _known_migrations() -> set[str]:
    import pkgutil

    from . import migrations
    return {m.name for m in pkgutil.iter_modules(migrations.__path__) if m.name.startswith("m")}


def _create_tables(src: sqlite3.Connection, dst: pgsql.Connection, schema: list, tables: list[str]) -> None:
    """The tables as Kosha creates them on PostgreSQL (with the database changes the source has had), plus
    any other table or index of the source, translated from its SQLite definition."""
    from . import db
    applied = [r[0] for r in src.execute("SELECT id FROM schema_migrations")] if "schema_migrations" in tables else []
    unknown = sorted(set(applied) - _known_migrations())
    if unknown:
        raise CopyRefused("The SQLite database was changed by a newer Kosha version (" + ", ".join(unknown[:3]) +
                          "). Copy it with that version.")
    try:
        db.create_schema(dst, defaults=False, only_migrations=applied)
        have_tables = set(_target_tables(dst))
        have_indexes = {r[0] for r in dst.raw.execute(
            "SELECT indexname::text FROM pg_indexes WHERE schemaname = current_schema()").fetchall()}
        dst.execute("BEGIN IMMEDIATE")
        for kind, name, _t, sql in schema:
            if (kind == "table" and name not in have_tables) or (kind == "index" and name not in have_indexes):
                dst.execute(_if_not_exists(sql))
        dst.execute("COMMIT")
    except Exception as e:
        if dst.in_transaction:
            dst.execute("ROLLBACK")
        raise CopyRefused(f"The tables could not be created in PostgreSQL: {e}. Nothing was copied.") from e


def _copy_locked(src: sqlite3.Connection, dst: pgsql.Connection, log) -> list[dict]:
    _empty_target(dst)
    schema = src.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL AND "
                         "name NOT LIKE 'sqlite_%' AND type IN ('table', 'index') ORDER BY rowid").fetchall()
    tables = [name for kind, name, _t, _s in schema if kind == "table"]
    columns = {t: [r[1] for r in src.execute(f"PRAGMA table_info({_q(t)})")] for t in tables}
    links = {t: {r[2] for r in src.execute(f"PRAGMA foreign_key_list({_q(t)})")} for t in tables}

    # 1. tables and indexes
    info = dst.raw.info
    log(f"Creating the tables in database {info.dbname} on {info.host}:{info.port} ...")
    _create_tables(src, dst, schema, tables)
    for t in tables:
        have = {r[1] for r in dst.execute(f"PRAGMA table_info({_q(t)})").fetchall()}
        missing = [c for c in columns[t] if c not in have]
        if missing:
            raise CopyRefused(f"Table {t} in PostgreSQL has no column {', '.join(missing)}; nothing was copied.")
    # rows that database changes put in while the tables were created give way to the source's own rows
    # (every table was empty when the copy started)
    dst.raw.execute("TRUNCATE " + ", ".join(_q(t) for t in _target_tables(dst)) + " RESTART IDENTITY")

    filled: list[str] = []
    fks = [(r[0], r[1]) for r in dst.raw.execute(
        "SELECT conrelid::regclass::text, conname::text FROM pg_constraint WHERE contype = 'f' AND NOT condeferrable "
        "AND connamespace = current_schema()::regnamespace").fetchall()]
    try:
        # foreign keys are checked when each group commits, so rows can arrive in any order
        for t, c in fks:
            dst.raw.execute(f"ALTER TABLE {t} ALTER CONSTRAINT {_q(c)} DEFERRABLE INITIALLY IMMEDIATE")
        # 2. rows, one transaction per group of linked tables
        for group in _groups(tables, links):
            with dst.raw.transaction():
                dst.raw.execute(f"SELECT pg_advisory_xact_lock({pgsql.WRITE_LOCK_KEY})")
                dst.raw.execute("SET CONSTRAINTS ALL DEFERRED")
                for t in group:
                    n = _copy_table(src, dst, t, columns[t])
                    filled.append(t)
                    log(f"  {t}: {n} rows")
        # 3. id counters continue after the highest copied id (and after ids SQLite already handed out)
        seqs = {}
        if "sqlite_sequence" in {r[0] for r in src.execute("SELECT name FROM sqlite_master")}:
            seqs = dict(src.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
        for t in tables:
            col = dst._identity(t)
            if col:
                top = dst.raw.execute(f"SELECT COALESCE(MAX({_q(col)}), 0) FROM {_q(t)}").fetchone()[0]
                start = max(int(top), int(seqs.get(t) or 0)) + 1
                dst.raw.execute("SELECT setval(pg_get_serial_sequence(%s, %s), %s, false)", (_q(t), col, start))
        # 4. verify
        log("Checking every table ...")
        report = []
        for t in tables:
            cols = ", ".join(_q(c) for c in columns[t])
            s = _checksum(_batches(src.execute(f"SELECT {cols} FROM {_q(t)}")))
            with dst.raw.transaction():
                cur = dst.raw.cursor(name="kosha_verify")
                cur.execute(f"SELECT {cols} FROM {_q(t)}")
                d = _checksum(_batches(cur))
                cur.close()
            if s != d:
                raise CopyFailed(f"Table {t} differs after copying: SQLite has {s[0]} rows (checksum {s[1][:12]}), "
                                 f"PostgreSQL {d[0]} rows (checksum {d[1][:12]}).")
            report.append({"table": t, "rows": s[0], "checksum": s[1]})
        return report
    except BaseException as e:
        _undo(dst, filled)
        if isinstance(e, (CopyFailed, KeyboardInterrupt)):
            raise
        raise CopyFailed(f"{e}") from e
    finally:
        for t, c in fks:
            try:
                dst.raw.execute(f"ALTER TABLE {t} ALTER CONSTRAINT {_q(c)} NOT DEFERRABLE")
            except Exception:
                pass


def _batches(cur):
    while True:
        rows = cur.fetchmany(BATCH)
        if not rows:
            return
        yield rows


def _copy_table(src: sqlite3.Connection, dst: pgsql.Connection, table: str, cols: list[str]) -> int:
    n = 0
    names = ", ".join(_q(c) for c in cols)
    cur = src.execute(f"SELECT {names} FROM {_q(table)}")
    with dst.raw.cursor() as pc, pc.copy(f"COPY {_q(table)} ({names}) FROM STDIN") as cp:
        for rows in _batches(cur):
            for row in rows:
                cp.write_row(row)
                n += 1
    return n


def _undo(dst: pgsql.Connection, filled: list[str]) -> None:
    """Empty the tables this run filled (they were empty before), so the target is as it was."""
    try:
        if dst.in_transaction:
            dst.raw.execute("ROLLBACK")
        if filled:
            dst.raw.execute("TRUNCATE " + ", ".join(_q(t) for t in filled) + " RESTART IDENTITY")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m kosha.dbcopy",
                                description="Copy a Kosha SQLite database into an empty PostgreSQL database.")
    p.add_argument("--from", dest="source", required=True, help="the SQLite database, for example "
                                                                 "/var/lib/kosha/kosha.db")
    p.add_argument("--to", dest="target", required=True,
                   help="the empty PostgreSQL database, for example postgresql://kosha:secret@localhost:5432/kosha")
    a = p.parse_args(argv)
    if not a.target.startswith(("postgresql://", "postgres://")):
        print("--to must be a PostgreSQL URL (postgresql://user:password@host:5432/database).", file=sys.stderr)
        return 2
    try:
        report = copy_database(Path(a.source), a.target)
    except CopyRefused as e:
        print(f"NOT COPIED: {e}", file=sys.stderr)
        return 2
    except CopyFailed as e:
        print(f"COPY FAILED: {e}\nThe PostgreSQL tables were emptied again; the SQLite database was not changed.",
              file=sys.stderr)
        return 1
    total = sum(r["rows"] for r in report)
    print(f"Copied and checked {len(report)} tables, {total} rows: every table has the same rows and checksum.")
    print("Next: set KOSHA_DATABASE_URL in kosha.env, keep KOSHA_DATA_DIR as it is (the stored files stay there), "
          "and start Kosha. Keep the SQLite file until you have checked Kosha on PostgreSQL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
