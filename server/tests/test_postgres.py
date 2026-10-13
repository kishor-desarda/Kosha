# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kosha's data layer on a real PostgreSQL database, and the SQLite -> PostgreSQL copy tool.

Runs when KOSHA_TEST_DATABASE_URL is set (see tests/harness.py); each test module gets new databases.
"""
import os
import sqlite3
import threading
import time
import types

import pytest

from kosha import config, db, dbcopy, migrations, pgsql

URL = os.environ.get("KOSHA_TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not URL, reason="set KOSHA_TEST_DATABASE_URL to run the PostgreSQL tests")
if URL:
    pytest.importorskip("psycopg")
    from harness import create_test_database, drop_test_database


@pytest.fixture(scope="module")
def pg():
    url = create_test_database("kosha_unit")
    old = config.DATABASE_URL
    db.close_thread_connection()
    config.DATABASE_URL = url
    try:
        db.init()
        yield url
    finally:
        db.close_thread_connection()
        config.DATABASE_URL = old
        drop_test_database(url)


def _user(name: str) -> int:
    return db.insert("users", {"username": name, "full_name": name.title(), "role": "engineer",
                               "password_hash": "x", "created_at": db.now()})


def test_engine_rows_and_new_ids(pg):
    assert db.engine() == "postgresql" and db.dialect() == "postgresql"
    uid = _user("asha")
    assert isinstance(uid, int) and uid > 0
    u = db.one("SELECT * FROM users WHERE id = ?", (uid,))
    assert u["username"] == "asha" and u["active"] == 1 and isinstance(u["active"], int)
    assert db.scalar("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)) == 1
    assert _user("daniel") > uid


def test_case_insensitive_names_like_sqlite(pg):
    _user("Sofia")
    with pytest.raises(sqlite3.IntegrityError):
        _user("SOFIA")
    assert db.one("SELECT id FROM users WHERE username = ?", ("sofia",))           # the column is NOCASE
    assert db.one("SELECT id FROM users WHERE username = ? COLLATE NOCASE", ("sOfIa",))
    assert [r["username"] for r in db.rows("SELECT username FROM users WHERE username LIKE ?", ("so%",))] == ["Sofia"]
    root = db.insert("folders", {"parent_id": None, "name": "Root", "created_at": db.now()})
    db.insert("folders", {"parent_id": root, "name": "Mixers", "created_at": db.now()})
    with pytest.raises(db.IntegrityError):
        db.insert("folders", {"parent_id": root, "name": "MIXERS", "created_at": db.now()})
    assert db.one("SELECT id FROM folders WHERE IFNULL(parent_id,0) = ? AND name = ? COLLATE NOCASE", (root, "mixers"))


def test_transactions_commit_roll_back_and_nest(pg):
    with db.tx():
        a = _user("tx-a")
        with db.tx():                                   # joins the outer transaction
            b = _user("tx-b")
    assert db.scalar("SELECT COUNT(*) FROM users WHERE id IN (?, ?)", (a, b)) == 2
    with pytest.raises(RuntimeError):
        with db.tx():
            c = _user("tx-c")
            raise RuntimeError("stop")
    assert not db.one("SELECT id FROM users WHERE id = ?", (c,))
    assert not db.connect().in_transaction


def test_a_failed_statement_is_undone_alone_as_in_sqlite(pg):
    with db.tx():
        a = _user("sp-a")
        with pytest.raises(sqlite3.IntegrityError):
            _user("SP-A")
        b = _user("sp-b")
    assert db.scalar("SELECT COUNT(*) FROM users WHERE id IN (?, ?)", (a, b)) == 2


def test_an_aborted_transaction_is_never_reported_as_committed(pg):
    with pytest.raises(sqlite3.OperationalError):
        with db.tx():
            uid = _user("lost")
            try:
                db.scalar("SELECT 1 / 0")                # a failing read aborts the PostgreSQL transaction
            except sqlite3.DatabaseError:
                pass
    assert not db.one("SELECT id FROM users WHERE id = ?", (uid,))
    assert not db.connect().in_transaction
    assert db.scalar("SELECT 1") == 1


def test_insert_or_replace_and_insert_or_ignore(pg):
    db.execute("CREATE TABLE IF NOT EXISTS t_upsert (id INTEGER PRIMARY KEY, a INTEGER NOT NULL, b INTEGER NOT NULL, "
               "note TEXT NOT NULL DEFAULT 'new', val TEXT, UNIQUE(a, b))")
    db.execute("INSERT OR REPLACE INTO t_upsert (a, b, val) VALUES (?, ?, ?)", (1, 2, "first"))
    db.execute("UPDATE t_upsert SET note = 'edited'")
    db.execute("INSERT OR REPLACE INTO t_upsert (a, b, val) VALUES (?, ?, ?)", (1, 2, "second"))
    rows = db.rows("SELECT a, b, note, val FROM t_upsert")
    assert rows == [{"a": 1, "b": 2, "note": "new", "val": "second"}]       # replaced, unset columns back to default
    db.execute("INSERT OR IGNORE INTO t_upsert (a, b, val) VALUES (?, ?, ?)", (1, 2, "third"))
    assert db.scalar("SELECT val FROM t_upsert") == "second"
    db.execute("INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)", ("company_name", '"x"'))
    assert db.get_setting("company_name") == ""
    db.set_setting("company_name", "Demo Engineering")
    assert db.get_setting("company_name") == "Demo Engineering"


def test_an_explicit_id_moves_the_counter_on(pg):
    db.execute("INSERT INTO audit (id, at, action, entity) VALUES (?, ?, ?, ?)", (5000, db.now(), "x", "y"))
    db.audit(None, "next", "test", None)
    assert db.scalar("SELECT MAX(id) FROM audit") > 5000


def test_numbers_and_booleans_come_back_as_in_sqlite(pg):
    r = db.one("SELECT SUM(id) AS s, AVG(id) AS a, COUNT(*) AS n, 1 = 1 AS t, 1 = 2 AS f, ROUND(2.345, 2) AS r, "
               "MAX(1, 3) AS m, IFNULL(NULL, 7) AS i FROM users")
    assert isinstance(r["s"], int) and isinstance(r["a"], float) and isinstance(r["n"], int)
    assert (r["t"], r["f"], r["r"], r["m"], r["i"]) == (1, 0, 2.35, 3, 7)
    assert db.scalar("SELECT ?", (True,)) in (1, "1")


def test_order_by_puts_nulls_where_sqlite_does(pg):
    db.execute("CREATE TABLE IF NOT EXISTS t_nulls (id INTEGER PRIMARY KEY, v TEXT)")
    for v in ("b", None, "a"):
        db.execute("INSERT INTO t_nulls (v) VALUES (?)", (v,))
    lite = sqlite3.connect(":memory:")
    lite.execute("CREATE TABLE t_nulls (id INTEGER PRIMARY KEY, v TEXT)")
    lite.executemany("INSERT INTO t_nulls (v) VALUES (?)", [("b",), (None,), ("a",)])
    for q in ("SELECT v FROM t_nulls ORDER BY v", "SELECT v FROM t_nulls ORDER BY v DESC"):
        assert [r["v"] for r in db.rows(q)] == [r[0] for r in lite.execute(q)]


def test_pragma_table_info_and_sqlite_master(pg):
    cols = {r[1]: r for r in db.connect().execute("PRAGMA table_info(cp_groups)").fetchall()}
    assert "kind" in cols and "standard_item_id" in cols and cols["id"][5] == 1 and cols["label"][3] == 1
    assert db.one("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", ("documents",))
    assert db.one("SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?", ("ux_doc_name",))


def test_migrations_translate_and_native_sql_runs_as_is(pg, monkeypatch):
    conn = db.connect()
    mods = {
        "m1_sqlite": types.SimpleNamespace(SQL="CREATE TABLE IF NOT EXISTS m_widgets (id INTEGER PRIMARY KEY, "
                                               "name TEXT NOT NULL COLLATE NOCASE UNIQUE);",
                                           up=lambda c: c.execute("INSERT OR IGNORE INTO m_widgets (name) VALUES (?)",
                                                                  ("Gear",))),
        "m2_native": types.SimpleNamespace(SQL="CREATE VIRTUAL TABLE nope USING fts5(x);",
                                           SQL_POSTGRESQL="CREATE TABLE m_native (id bigint GENERATED ALWAYS AS "
                                                          "IDENTITY PRIMARY KEY, t text DEFAULT '100%');"),
        "m3_bad": types.SimpleNamespace(SQL="CREATE TABLE m_gadgets (id INTEGER);\nINSERT INTO nowhere VALUES (1);"),
    }
    done = lambda c: {r[0] for r in c.execute("SELECT id FROM schema_migrations").fetchall()}   # noqa: E731
    monkeypatch.setattr(migrations, "pending", lambda c: [n for n in mods if n not in done(c)])
    monkeypatch.setattr(migrations.importlib, "import_module", lambda name: mods[name.rsplit(".", 1)[1]])
    with pytest.raises(sqlite3.OperationalError):
        migrations.apply(conn)
    assert db.scalar("SELECT name FROM m_widgets WHERE name = ?", ("GEAR",)) == "Gear"
    assert db.one("SELECT name FROM sqlite_master WHERE name = 'm_native'")
    assert not db.one("SELECT name FROM sqlite_master WHERE name = 'm_gadgets'")    # the failed change left nothing
    assert {"m1_sqlite", "m2_native"} <= done(conn) and "m3_bad" not in done(conn)


def test_writers_take_turns_like_sqlite(pg):
    order = []
    started = threading.Event()

    def slow_writer():
        with db.tx():
            _user("lock-a")
            started.set()
            time.sleep(0.5)
            order.append("a")
        db.close_thread_connection()

    t = threading.Thread(target=slow_writer)
    t.start()
    started.wait(5)
    with db.tx():                                     # waits for the other writer, as BEGIN IMMEDIATE does
        order.append("b")
        _user("lock-b")
    t.join()
    assert order == ["a", "b"]
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username IN ('lock-a', 'lock-b')") == 2


def test_a_lost_connection_is_replaced(pg):
    pid = db.scalar("SELECT pg_backend_pid()")
    other = pgsql.connect(pg)
    other.execute("SELECT pg_terminate_backend(?)", (pid,))
    other.close()
    with pytest.raises(sqlite3.DatabaseError):
        db.scalar("SELECT 1")
    assert db.scalar("SELECT 1") == 1                 # the next call reconnects


def test_backup_uses_pg_dump_and_reads_the_copy_back(pg, tmp_path, monkeypatch):
    import shutil

    from kosha import backup
    if not (shutil.which("pg_dump") and shutil.which("pg_restore")):
        pytest.skip("pg_dump is not installed")
    monkeypatch.setattr(config, "BLOB_DIR", tmp_path / "blobs")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "blobs").mkdir()
    out = backup.backup(tmp_path / "backups")
    assert out.suffix == ".dump" and out.stat().st_size > 0 and not list(out.parent.glob("*.partial"))


# ------------------------------------------------------------------------------------------- dbcopy --

def _sqlite_installation(path) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    db.create_schema(conn)
    now = db.now()
    conn.execute("INSERT INTO users (id, username, full_name, role, password_hash, created_at) VALUES "
                 "(1, 'admin', 'Kosha Administrator', 'admin', 'h', ?), (2, 'asha', 'Asha', 'engineer', 'h', ?)",
                 (now, now))
    # a folder whose parent was created later (higher id): foreign keys checked per group, not per row
    conn.execute("INSERT INTO folders (id, parent_id, name, created_at) VALUES (5, NULL, 'Kosha', ?)", (now,))
    conn.execute("INSERT INTO folders (id, parent_id, name, created_at) VALUES (3, 9, 'Pumps – Ø80', ?)", (now,))
    conn.execute("INSERT INTO folders (id, parent_id, name, created_at) VALUES (9, 5, 'Products', ?)", (now,))
    conn.execute("INSERT INTO items (id, part_number, description, weight_kg, created_at, updated_at) VALUES "
                 "(7, 'MX-100-020', 'MAIN SHAFT', 186.25, ?, ?), (8, 'STD-BLT-M16X60', 'HEX BOLT', NULL, ?, ?)",
                 (now, now, now, now))
    conn.execute("INSERT INTO audit (at, user_id, action, entity, details) VALUES (?, 1, 'login', 'user', "
                 "'50% done, it''s fine')", (now,))
    conn.close()


def test_dbcopy_copies_checks_and_continues_ids(pg, tmp_path):
    src = tmp_path / "kosha.db"
    _sqlite_installation(src)
    url = create_test_database("kosha_copy")
    try:
        report = dbcopy.copy_database(src, url, log=lambda m: None)
        counts = {r["table"]: r["rows"] for r in report}
        assert counts["folders"] == 3 and counts["users"] == 2 and counts["items"] == 2 and counts["settings"] > 0
        conn = pgsql.connect(url)
        assert conn.execute("SELECT name FROM folders WHERE id = 3").fetchone()[0] == "Pumps – Ø80"
        assert conn.execute("SELECT weight_kg FROM items WHERE part_number = ?", ("mx-100-020",)).fetchone()[0] == 186.25
        new = conn.execute("INSERT INTO folders (parent_id, name, created_at) VALUES (5, 'New', ?)", (db.now(),))
        assert new.lastrowid == 10
        assert not conn.execute("SELECT 1 FROM pg_constraint WHERE contype = 'f' AND condeferrable").fetchall()
        conn.close()
        with pytest.raises(dbcopy.CopyRefused):                     # never into a database that holds data
            dbcopy.copy_database(src, url, log=lambda m: None)
    finally:
        drop_test_database(url)


def test_a_demo_installation_moves_to_postgresql_and_runs(pg, tmp_path):
    import subprocess
    import sys

    from harness import ADMIN_PASSWORD, SERVER_DIR, KoshaServer
    env = {k: v for k, v in os.environ.items() if k != "KOSHA_DATABASE_URL"}
    env.update(KOSHA_DATA_DIR=str(tmp_path / "data"), KOSHA_DISABLE_WORKER="1", KOSHA_ADMIN_PASSWORD=ADMIN_PASSWORD)
    subprocess.run([sys.executable, "-c", "from kosha.app import bootstrap; from kosha.seed import seed; "
                    "bootstrap(); seed()"], cwd=SERVER_DIR, env=env, check=True, capture_output=True)
    url = create_test_database("kosha_copy")
    server = None
    try:
        done = subprocess.run([sys.executable, "-m", "kosha.dbcopy", "--from", str(tmp_path / "data" / "kosha.db"),
                               "--to", url], cwd=SERVER_DIR, env=env, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "every table has the same rows and checksum" in done.stdout
        server = KoshaServer(env={"KOSHA_DATABASE_URL": url}).start()
        asha = server.client("asha")
        found = asha.call("GET", "/api/search?q=mx-100-020")
        assert found["items"] and found["items"][0]["part_number"] == "MX-100-020"
        new = asha.call("POST", "/api/items", {"part_number": "MX-900-001", "description": "NEW AFTER THE MOVE"})
        assert new["item"]["id"] > max(i["id"] for i in asha.call("GET", "/api/items?q=MX")["items"]
                                       if i["part_number"] != "MX-900-001")
    finally:
        if server:
            server.stop()
        drop_test_database(url)


def test_dbcopy_builds_tables_with_the_same_database_changes(pg, tmp_path, monkeypatch):
    real = migrations.pending
    seed = types.SimpleNamespace(SQL="CREATE TABLE IF NOT EXISTS t_seeded (id INTEGER PRIMARY KEY, name TEXT NOT NULL "
                                     "UNIQUE COLLATE NOCASE);",
                                 up=lambda c: c.execute("INSERT INTO t_seeded (name) VALUES (?)", ("default",)))
    monkeypatch.setattr(migrations, "pending", lambda c: real(c) + (
        [] if "m29990101_0000_test_seed" in {r[0] for r in c.execute("SELECT id FROM schema_migrations")}
        else ["m29990101_0000_test_seed"]))
    import importlib
    original = importlib.import_module
    monkeypatch.setattr(migrations.importlib, "import_module",
                        lambda name: seed if name.endswith("test_seed") else original(name))
    monkeypatch.setattr(dbcopy, "_known_migrations", lambda: {"m29990101_0000_test_seed", *_real_known()})
    src = tmp_path / "kosha.db"
    _sqlite_installation(src)
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO t_seeded (name) VALUES ('mine')")
    conn.commit()
    conn.close()
    url = create_test_database("kosha_copy")
    try:
        report = {r["table"]: r["rows"] for r in dbcopy.copy_database(src, url, log=lambda m: None)}
        assert report["t_seeded"] == 2           # the change's own default row was not copied twice
        c = pgsql.connect(url)
        assert c.execute("SELECT name FROM t_seeded WHERE name = ?", ("MINE",)).fetchone()[0] == "mine"
        c.close()
    finally:
        drop_test_database(url)


def _real_known():
    import pkgutil
    return {m.name for m in pkgutil.iter_modules(migrations.__path__) if m.name.startswith("m")}


def test_dbcopy_refuses_unsuitable_sources(pg, tmp_path):
    with pytest.raises(dbcopy.CopyRefused):
        dbcopy.copy_database(tmp_path / "missing.db", pg, log=lambda m: None)
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE x (a)")
    with pytest.raises(dbcopy.CopyRefused):
        dbcopy.copy_database(other, pg, log=lambda m: None)
    assert not other.with_name("missing.db").exists()


def test_dbcopy_failure_leaves_the_target_empty(pg, tmp_path):
    src = tmp_path / "kosha.db"
    _sqlite_installation(src)
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO lifecycle_events (document_id, revision, to_state, at) VALUES ('not a number', 'A', "
                 "'released', 'x')")                              # SQLite stores it; PostgreSQL cannot
    conn.commit()
    conn.close()
    url = create_test_database("kosha_copy")
    try:
        with pytest.raises(dbcopy.CopyFailed):
            dbcopy.copy_database(src, url, log=lambda m: None)
        conn = pgsql.connect(url)
        tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")]
        assert tables and all(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0 for t in tables)
        conn.close()
        assert dbcopy.main(["--from", str(src), "--to", url]) == 1
    finally:
        drop_test_database(url)
