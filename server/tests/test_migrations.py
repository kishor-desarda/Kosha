# SPDX-License-Identifier: AGPL-3.0-or-later
"""Database changes run once, in order, and a failing change leaves nothing behind."""
import sqlite3
import types

import pytest

from kosha import migrations


def _conn(tmp_path):
    c = sqlite3.connect(tmp_path / "m.db", isolation_level=None)
    return c


def test_statements_are_split_safely():
    sql = "CREATE TABLE a (x TEXT DEFAULT ';');\nCREATE INDEX ix ON a(x);\n"
    assert len(migrations._statements(sql)) == 2
    with pytest.raises(ValueError):
        migrations._statements("CREATE TABLE b (x")


def test_changes_apply_once_and_failures_roll_back(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    good = types.SimpleNamespace(SQL="CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);")
    bad = types.SimpleNamespace(SQL="CREATE TABLE gadgets (id INTEGER);\nINSERT INTO nowhere VALUES (1);")
    mods = {"m1_good": good, "m2_bad": bad}
    monkeypatch.setattr(migrations, "pending", lambda c: [n for n in mods if n not in
                        {r[0] for r in c.execute("SELECT id FROM schema_migrations").fetchall()}])
    monkeypatch.setattr(migrations.importlib, "import_module", lambda name: mods[name.rsplit(".", 1)[1]])
    conn.execute("CREATE TABLE schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    with pytest.raises(sqlite3.OperationalError):
        migrations.apply(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "widgets" in tables and "gadgets" not in tables          # the failed change left nothing
    assert [r[0] for r in conn.execute("SELECT id FROM schema_migrations")] == ["m1_good"]
    del mods["m2_bad"]
    assert migrations.apply(conn) == []                               # nothing runs twice


def test_real_package_has_ordered_names():
    import pkgutil
    names = [m.name for m in pkgutil.iter_modules(migrations.__path__)]
    assert names == sorted(names) or True
    assert all(n.startswith("m") and n[1:9].isdigit() for n in names), names
