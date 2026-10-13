# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite -> PostgreSQL translation: one test per rule (no database needed)."""
import pytest

from kosha import db, migrations, pgsql
from kosha.pgsql import translate


def sql(s: str) -> str:
    return translate(s).sql


# ------------------------------------------------------------------------------ place holders, quoting --

def test_question_marks_become_psycopg_placeholders():
    assert sql("SELECT * FROM items WHERE id = ? AND state = ?") == "SELECT * FROM items WHERE id = %s AND state = %s"


def test_question_marks_in_strings_comments_and_names_stay():
    assert sql("SELECT '?', \"what?\" FROM t -- really?\nWHERE a = ?") == "SELECT '?', \"what?\" FROM t WHERE a = %s"


def test_literal_percent_is_doubled_for_psycopg():
    assert sql("SELECT * FROM v WHERE j LIKE '%\"mesh\"%' AND 5 % 2 = 1") == \
        "SELECT * FROM v WHERE j ILIKE '%%\"mesh\"%%' COLLATE \"default\" AND 5 %% 2 = 1"


def test_named_placeholders():
    assert sql("SELECT * FROM t WHERE a = :name") == "SELECT * FROM t WHERE a = %(name)s"


def test_numbered_placeholders_are_refused():
    with pytest.raises(ValueError):
        translate("SELECT ?1")


def test_backticks_become_double_quotes():
    assert sql("SELECT `order` FROM t") == 'SELECT "order" FROM t'


def test_string_with_doubled_quote_is_one_token():
    assert sql("SELECT 'it''s ?' , ?") == "SELECT 'it''s ?', %s"


# ---------------------------------------------------------------------------------------- COLLATE NOCASE --

def test_collate_nocase_in_comparisons_and_order_by():
    assert sql("SELECT * FROM items WHERE part_number = ? COLLATE NOCASE") == \
        "SELECT * FROM items WHERE part_number = %s COLLATE nocase"
    assert sql("SELECT * FROM folders f ORDER BY f.name COLLATE NOCASE") == \
        "SELECT * FROM folders f ORDER BY f.name COLLATE nocase NULLS FIRST"


def test_collate_nocase_moves_next_to_the_type_in_column_definitions():
    out = sql("CREATE TABLE u (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE)")
    assert "username TEXT COLLATE nocase NOT NULL UNIQUE" in out


def test_collate_nocase_in_unique_index():
    assert sql("CREATE UNIQUE INDEX IF NOT EXISTS ux ON documents(folder_id, filename COLLATE NOCASE) "
               "WHERE deleted = 0") == \
        "CREATE UNIQUE INDEX IF NOT EXISTS ux ON documents(folder_id, filename COLLATE nocase) WHERE deleted = 0"


def test_collate_binary_is_c():
    assert sql("SELECT * FROM t ORDER BY a COLLATE BINARY DESC") == 'SELECT * FROM t ORDER BY a COLLATE "C" DESC NULLS LAST'


# ----------------------------------------------------------------------------------------------- LIKE --

def test_like_is_case_insensitive_and_works_on_nocase_columns():
    assert sql("SELECT * FROM items WHERE part_number LIKE ? OR description NOT LIKE ?") == \
        'SELECT * FROM items WHERE part_number ILIKE %s COLLATE "default" OR description NOT ILIKE %s COLLATE "default"'


def test_like_pattern_expression_gets_the_collation_on_its_first_operand():
    assert sql("SELECT 1 FROM t WHERE a LIKE ? || '%'") == "SELECT 1 FROM t WHERE a ILIKE %s COLLATE \"default\" || '%%'"
    assert sql("SELECT 1 FROM t WHERE a LIKE lower(b.c) ESCAPE '\\'") == \
        "SELECT 1 FROM t WHERE a ILIKE lower(b.c) COLLATE \"default\" ESCAPE '\\'"
    assert sql("SELECT 1 FROM t WHERE a LIKE x.y") == 'SELECT 1 FROM t WHERE a ILIKE x.y COLLATE "default"'


def test_like_after_explicit_nocase_drops_the_nocase():
    assert sql("SELECT 1 FROM t WHERE a COLLATE NOCASE LIKE ?") == 'SELECT 1 FROM t WHERE a ILIKE %s COLLATE "default"'


# ------------------------------------------------------------------------------------------ functions --

def test_ifnull_becomes_coalesce():
    assert sql("SELECT id FROM folders WHERE IFNULL(parent_id,0) = ?") == \
        "SELECT id FROM folders WHERE COALESCE(parent_id, 0) = %s"


def test_ifnull_in_expression_index():
    assert sql("CREATE UNIQUE INDEX ux ON folders(IFNULL(parent_id, 0), name COLLATE NOCASE)") == \
        "CREATE UNIQUE INDEX ux ON folders(COALESCE(parent_id, 0), name COLLATE nocase)"


def test_iif_instr_and_two_argument_max_min():
    assert sql("SELECT IIF(a > 1, 'big', 'small'), INSTR(name, '-'), MAX(a, b), MIN(a, b, c), MAX(a) FROM t") == \
        "SELECT CASE WHEN a > 1 THEN 'big' ELSE 'small' END, strpos((name)::text COLLATE \"default\", '-'), " \
        "GREATEST(a, b), LEAST(a, b, c), MAX(a) FROM t"


def test_last_insert_rowid():
    assert sql("SELECT last_insert_rowid()") == "SELECT lastval()"


def test_group_concat():
    assert sql("SELECT GROUP_CONCAT(name) FROM t") == "SELECT string_agg((name)::text, ',') FROM t"
    assert sql("SELECT group_concat(DISTINCT name, '; ') FROM t") == "SELECT string_agg(DISTINCT(name)::text, '; ') FROM t"


def test_round_with_digits_uses_numeric():
    assert sql("SELECT ROUND(qty, 2), ROUND(qty) FROM bom_lines") == \
        "SELECT ROUND((qty)::numeric, 2), ROUND(qty) FROM bom_lines"


def test_replace_function_works_on_nocase_columns():
    assert sql("SELECT REPLACE(part_number, '-', '') FROM items") == \
        "SELECT replace((part_number)::text COLLATE \"default\", '-', '') FROM items"


def test_total():
    assert sql("SELECT TOTAL(qty) FROM bom_lines") == "SELECT COALESCE(SUM(qty), 0.0) FROM bom_lines"


def test_cast_types():
    assert sql("SELECT CAST(a AS INTEGER), CAST(b AS REAL), CAST(c AS TEXT) FROM t") == \
        "SELECT CAST(a AS BIGINT), CAST(b AS DOUBLE PRECISION), CAST(c AS TEXT) FROM t"


def test_function_names_used_as_table_names_are_not_calls():
    assert sql("INSERT INTO replace (a) VALUES (?)").startswith("INSERT INTO replace(a)")


# -------------------------------------------------------------------------------------- dates and times --

def test_datetime_now_and_current_timestamp():
    now = "to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')"
    assert sql("SELECT datetime('now')") == f"SELECT {now}"
    assert sql("SELECT CURRENT_TIMESTAMP") == f"SELECT ({now})"
    assert sql("SELECT date('now'), CURRENT_DATE") == \
        "SELECT to_char(timezone('UTC', now()), 'YYYY-MM-DD'), (to_char(timezone('UTC', now()), 'YYYY-MM-DD'))"


def test_datetime_modifiers_and_columns():
    assert sql("SELECT 1 FROM audit WHERE at < datetime('now', '-30 days')") == \
        "SELECT 1 FROM audit WHERE at < to_char((timezone('UTC', now()) + interval '-30 days'), " \
        "'YYYY-MM-DD HH24:MI:SS')"
    assert sql("SELECT date(created_at, '+1 month') FROM t") == \
        "SELECT to_char((timezone('UTC', (created_at)::timestamptz) + interval '+1 months'), 'YYYY-MM-DD') FROM t"
    with pytest.raises(ValueError):
        translate("SELECT datetime('now', 'weekday 0')")


def test_strftime_julianday():
    assert sql("SELECT strftime('%Y-%m', at) FROM audit") == \
        "SELECT to_char(timezone('UTC', (at)::timestamptz), 'YYYY-MM') FROM audit"
    assert "extract(epoch from" in sql("SELECT julianday('now') - julianday(at) FROM audit")
    assert sql("SELECT strftime('%s', 'now')") == "SELECT extract(epoch from timezone('UTC', now()))::bigint"


def test_current_timestamp_default_in_column_definition():
    assert "at TEXT NOT NULL DEFAULT(to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))" in \
        sql("CREATE TABLE t (id INTEGER PRIMARY KEY, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")


# --------------------------------------------------------------------------------- booleans, operators --

def test_true_false_are_integers_and_double_equals():
    assert sql("SELECT * FROM users WHERE active = TRUE AND x == 1 AND y IS NOT TRUE") == \
        "SELECT * FROM users WHERE active = 1 AND x = 1 AND y IS NOT TRUE"


# ------------------------------------------------------------------------------------------- INSERT OR --

def test_insert_or_ignore():
    t = translate("INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)")
    assert t.sql == "INSERT INTO settings(key, value_json) VALUES(%s, %s)  ON CONFLICT DO NOTHING"
    assert t.insert_table == "settings" and t.insert_columns == ("key", "value_json") and t.write


def test_insert_or_ignore_before_returning():
    assert sql("INSERT OR IGNORE INTO t (a) VALUES (?) RETURNING id") == \
        "INSERT INTO t(a) VALUES(%s)  ON CONFLICT DO NOTHING RETURNING id"


def test_insert_or_replace_and_replace_into_are_upserts():
    t = translate("INSERT OR REPLACE INTO co_items (co_id, document_id, action) VALUES (?, ?, ?)")
    assert t.upsert and t.sql.endswith(pgsql.UPSERT_MARK) and t.insert_columns == ("co_id", "document_id", "action")
    t = translate("REPLACE INTO numbering (prefix, next_value) VALUES (?, ?)")
    assert t.upsert and t.sql.startswith("INSERT INTO numbering(prefix, next_value)")


def test_insert_or_abort_is_plain_insert_and_main_schema_is_dropped():
    assert sql("INSERT OR ABORT INTO main.t (a) VALUES (1)") == "INSERT INTO t(a) VALUES(1)"


def test_sqlite_upsert_syntax_passes():
    assert sql("INSERT INTO settings(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
               "value_json = excluded.value_json") == \
        "INSERT INTO settings(key, value_json) VALUES(%s, %s) ON CONFLICT(key) DO UPDATE SET " \
        "value_json = excluded.value_json"


# ------------------------------------------------------------------------------------------- DDL --

def test_integer_primary_key_becomes_identity():
    assert sql("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, n INTEGER)") == \
        "CREATE TABLE t(id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, n BIGINT)"
    # only INTEGER PRIMARY KEY is SQLite's row id; a text key stays a text key
    assert sql("CREATE TABLE s (key TEXT PRIMARY KEY, v TEXT)") == "CREATE TABLE s(key TEXT PRIMARY KEY, v TEXT)"


def test_column_types_keep_sqlite_storage():
    out = sql("CREATE TABLE t (a REAL, b BLOB, c BOOLEAN, d DATETIME, e VARCHAR(20), f DOUBLE, g NUMERIC(10,2), h)")
    assert out == "CREATE TABLE t(a DOUBLE PRECISION, b BYTEA, c BIGINT, d TEXT, e TEXT, f DOUBLE PRECISION, " \
                  "g NUMERIC(10, 2), h TEXT)"


def test_table_constraints_and_options():
    out = sql("CREATE TABLE m (g INTEGER NOT NULL REFERENCES cp_groups(id) ON DELETE CASCADE, d INTEGER, "
              "PRIMARY KEY (g, d), UNIQUE(g) ON CONFLICT REPLACE) WITHOUT ROWID, STRICT")
    assert out == "CREATE TABLE m(g BIGINT NOT NULL REFERENCES cp_groups(id) ON DELETE CASCADE, d BIGINT, " \
                  "PRIMARY KEY(g, d), UNIQUE(g))"


def test_alter_table_add_column():
    assert sql("ALTER TABLE cp_groups ADD COLUMN standard_item_id INTEGER") == \
        "ALTER TABLE cp_groups ADD COLUMN standard_item_id BIGINT"
    assert sql("ALTER TABLE t ADD c TEXT NOT NULL DEFAULT '' COLLATE NOCASE") == \
        "ALTER TABLE t ADD c TEXT COLLATE nocase NOT NULL DEFAULT ''"
    assert translate("ALTER TABLE t ADD COLUMN c TEXT").ddl


def test_whole_schema_translates():
    stmts = pgsql.split_statements(db.SCHEMA)
    assert len(stmts) > 25
    for s in stmts:
        out = translate(s).sql
        assert "INTEGER" not in out and " REAL" not in out and "IFNULL" not in out and "NOCASE" not in out


def test_triggers_and_virtual_tables_are_refused():
    with pytest.raises(ValueError):
        translate("CREATE VIRTUAL TABLE f USING fts5(a)")
    with pytest.raises(ValueError):
        translate("CREATE TRIGGER x AFTER INSERT ON t BEGIN SELECT 1; END")


# ----------------------------------------------------------------------------------- ORDER BY, LIMIT --

def test_order_by_sorts_nulls_like_sqlite():
    assert sql("SELECT * FROM d ORDER BY checked_out_at DESC, name") == \
        "SELECT * FROM d ORDER BY checked_out_at DESC NULLS LAST, name NULLS FIRST"
    assert sql("SELECT * FROM d ORDER BY a NULLS LAST") == "SELECT * FROM d ORDER BY a NULLS LAST"


def test_order_by_id_is_left_alone_for_index_scans():
    assert sql("SELECT * FROM audit ORDER BY id DESC LIMIT ?") == "SELECT * FROM audit ORDER BY id DESC LIMIT %s"
    assert sql("SELECT * FROM audit a ORDER BY a.id") == "SELECT * FROM audit a ORDER BY a.id"


def test_order_by_inside_brackets_and_case():
    assert sql("SELECT (SELECT x FROM t ORDER BY y LIMIT 1), z FROM u ORDER BY CASE k WHEN 'a' THEN 0 END, id") == \
        "SELECT(SELECT x FROM t ORDER BY y NULLS FIRST LIMIT 1), z FROM u ORDER BY CASE k WHEN 'a' THEN 0 END " \
        "NULLS FIRST, id"


def test_limit_forms():
    assert sql("SELECT * FROM t LIMIT 5, 10") == "SELECT * FROM t LIMIT 10 OFFSET 5"
    assert sql("SELECT * FROM t LIMIT -1 OFFSET ?") == "SELECT * FROM t LIMIT ALL OFFSET %s"


def test_mixed_case_alias_keeps_its_case():
    assert sql("SELECT part_number AS partNumber, COUNT(*) AS n, x AS NAME FROM items") == \
        'SELECT part_number AS "partNumber", COUNT(*) AS n, x AS NAME FROM items'


# ------------------------------------------------------------------------------------ PRAGMA, catalogue --

def test_pragma_table_info_reads_the_catalogue():
    t = translate("PRAGMA table_info(cp_groups)")
    assert "pg_attribute" in t.sql and "to_regclass('cp_groups')" in t.sql and not t.write


def test_pragma_index_list_and_integrity_check():
    assert "pg_index" in sql("PRAGMA index_list('documents')")
    assert sql("PRAGMA integrity_check") == "SELECT 'ok' AS integrity_check"


def test_setting_pragmas_and_vacuum_do_nothing():
    for s in ("PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=30000", "VACUUM"):
        assert translate(s).action == "noop"


def test_sqlite_master_is_a_catalogue_view():
    out = sql("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?")
    assert "pg_tables" in out and "AS sqlite_master WHERE" in out
    out = sql("SELECT m.name FROM sqlite_master m WHERE m.type = 'index'")
    assert "pg_indexes" in out and ") m WHERE" in out


# --------------------------------------------------------------------------------- statement classes --

def test_transaction_control():
    assert translate("BEGIN IMMEDIATE").action == "begin_immediate"
    assert translate("BEGIN EXCLUSIVE TRANSACTION").action == "begin_immediate"
    assert translate("BEGIN").action == "begin"
    assert translate("BEGIN DEFERRED").action == "begin"
    assert translate("COMMIT").action == "commit"
    assert translate("END TRANSACTION").action == "commit"
    assert translate("ROLLBACK").action == "rollback"
    assert translate("ROLLBACK TO SAVEPOINT a").action == ""


def test_reads_writes_and_ddl_are_told_apart():
    assert not translate("SELECT 1").write
    assert translate("UPDATE t SET a = 1").write
    assert translate("DELETE FROM t").write
    assert translate("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x").write
    assert translate("CREATE INDEX i ON t(a)").ddl
    assert translate("SELECT * FROM t FOR UPDATE").write


def test_returning_is_detected():
    assert translate("INSERT INTO t (a) VALUES (1) RETURNING id").returning
    assert not translate("INSERT INTO t (a) VALUES (1)").returning


def test_trailing_semicolon_and_several_statements():
    assert sql("SELECT 1;") == "SELECT 1"
    with pytest.raises(ValueError):
        translate("SELECT 1; SELECT 2")
    assert pgsql.split_statements("CREATE TABLE a (x TEXT DEFAULT ';');\n-- c;\nSELECT 2;") == \
        ["CREATE TABLE a (x TEXT DEFAULT ';')", "-- c;\nSELECT 2"]


def test_unbalanced_brackets_are_refused():
    with pytest.raises(ValueError):
        translate("SELECT (1")


# -------------------------------------------------------------------------------------- rows, helpers --

def test_rows_read_by_position_and_name():
    cls = pgsql._row_class(("id", "Part_Number", "id"))
    r = cls((1, "MX-1", 2))
    assert r[0] == 1 and r["Part_Number"] == "MX-1" and r["part_number"] == "MX-1" and r["id"] == 1
    assert dict(r) == {"id": 1, "Part_Number": "MX-1"} and tuple(r) == (1, "MX-1", 2) and r == (1, "MX-1", 2)
    with pytest.raises(IndexError):
        r["nope"]


def test_redact_hides_the_password():
    assert pgsql.redact("postgresql://kosha:s3cret@db:5432/kosha") == "postgresql://kosha:***@db:5432/kosha"
    assert pgsql.redact("postgresql://db/kosha") == "postgresql://db/kosha"


def test_every_migration_translates():
    import importlib
    import pkgutil
    for m in pkgutil.iter_modules(migrations.__path__):
        mod = importlib.import_module(f"kosha.migrations.{m.name}")
        for stmt in migrations._statements(getattr(mod, "SQL", "") or ""):
            if not hasattr(mod, "SQL_POSTGRESQL"):
                translate(stmt)


def test_sqlite_stays_the_default(monkeypatch):
    from kosha import config
    monkeypatch.setattr(config, "DATABASE_URL", "")
    assert db.engine() == "sqlite"
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://x/y")
    assert db.engine() == "postgresql"
