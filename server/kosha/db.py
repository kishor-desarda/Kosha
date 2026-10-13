# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data layer: SQLite (WAL mode) by default, PostgreSQL when ``KOSHA_DATABASE_URL`` is set.

One connection per thread. Modules write SQL the SQLite way (``?`` place holders, ``COLLATE NOCASE``,
``INSERT OR IGNORE`` ...); on PostgreSQL the connection from :func:`connect` translates every statement
(see ``pgsql.py``) and behaves like a sqlite3 connection: rows by column name, ``lastrowid``,
``BEGIN IMMEDIATE`` (one writer at a time), and database errors raised as ``sqlite3`` exceptions.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

_local = threading.local()

IntegrityError = sqlite3.IntegrityError     # raised on both engines (unique keys, foreign keys, NOT NULL)
OperationalError = sqlite3.OperationalError


def engine() -> str:
    """"sqlite" or "postgresql"."""
    return "postgresql" if config.DATABASE_URL else "sqlite"


def dialect(conn=None) -> str:
    """The SQL dialect of a connection (default: this thread's)."""
    return getattr(conn if conn is not None else connect(), "dialect", "sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  full_name TEXT NOT NULL,
  email TEXT,
  role TEXT NOT NULL DEFAULT 'engineer',
  password_hash TEXT NOT NULL,
  ui_mode TEXT NOT NULL DEFAULT 'guided',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES folders(id),
  name TEXT NOT NULL,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_folder_name ON folders(IFNULL(parent_id, 0), name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  part_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
  description TEXT NOT NULL DEFAULT '',
  item_type TEXT NOT NULL DEFAULT 'part',
  material TEXT NOT NULL DEFAULT '',
  uom TEXT NOT NULL DEFAULT 'Nos',
  make_buy TEXT NOT NULL DEFAULT 'make',
  weight_kg REAL,
  item_group TEXT NOT NULL DEFAULT '',
  revision TEXT NOT NULL DEFAULT 'A',
  state TEXT NOT NULL DEFAULT 'in_work',
  standard_item_id INTEGER REFERENCES items(id),
  erp_item_code TEXT,
  erp_synced_at TEXT,
  erp_stock REAL,
  erp_cost REAL,
  props_json TEXT NOT NULL DEFAULT '{}',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  folder_id INTEGER NOT NULL REFERENCES folders(id),
  filename TEXT NOT NULL,
  ext TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  item_id INTEGER REFERENCES items(id),
  part_number TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  revision TEXT NOT NULL DEFAULT 'A',
  state TEXT NOT NULL DEFAULT 'in_work',
  checked_out_by INTEGER REFERENCES users(id),
  checked_out_at TEXT,
  latest_version INTEGER NOT NULL DEFAULT 0,
  deleted INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_name ON documents(folder_id, filename COLLATE NOCASE) WHERE deleted = 0;
CREATE INDEX IF NOT EXISTS ix_doc_item ON documents(item_id);

CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  version INTEGER NOT NULL,
  revision TEXT NOT NULL,
  blob_sha TEXT NOT NULL,
  size INTEGER NOT NULL,
  comment TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  preview_kind TEXT,
  preview_sha TEXT,
  viewer_kind TEXT,
  viewer_sha TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  signature_json TEXT,
  UNIQUE(document_id, version)
);

CREATE TABLE IF NOT EXISTS lifecycle_events (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  revision TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  user_id INTEGER,
  change_order_id INTEGER,
  comment TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_doc ON lifecycle_events(document_id);

CREATE TABLE IF NOT EXISTS boms (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  revision TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  state TEXT NOT NULL DEFAULT 'draft',
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(item_id, revision)
);

CREATE TABLE IF NOT EXISTS bom_lines (
  id INTEGER PRIMARY KEY,
  bom_id INTEGER NOT NULL REFERENCES boms(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL,
  child_item_id INTEGER NOT NULL REFERENCES items(id),
  qty REAL NOT NULL DEFAULT 1,
  uom TEXT NOT NULL DEFAULT '',
  find_number TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_bomlines_bom ON bom_lines(bom_id);
CREATE INDEX IF NOT EXISTS ix_bomlines_child ON bom_lines(child_item_id);

CREATE TABLE IF NOT EXISTS change_orders (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT 'normal',
  state TEXT NOT NULL DEFAULT 'draft',
  requested_by INTEGER,
  created_at TEXT NOT NULL,
  submitted_at TEXT,
  decided_at TEXT,
  implemented_at TEXT
);

CREATE TABLE IF NOT EXISTS co_items (
  id INTEGER PRIMARY KEY,
  co_id INTEGER NOT NULL REFERENCES change_orders(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  action TEXT NOT NULL DEFAULT 'release',
  from_revision TEXT,
  to_revision TEXT,
  disposition TEXT NOT NULL DEFAULT '',
  UNIQUE(co_id, document_id)
);

CREATE TABLE IF NOT EXISTS co_approvers (
  id INTEGER PRIMARY KEY,
  co_id INTEGER NOT NULL REFERENCES change_orders(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  decision TEXT NOT NULL DEFAULT 'pending',
  comment TEXT NOT NULL DEFAULT '',
  decided_at TEXT,
  UNIQUE(co_id, user_id)
);

CREATE TABLE IF NOT EXISTS co_comments (
  id INTEGER PRIMARY KEY,
  co_id INTEGER NOT NULL REFERENCES change_orders(id) ON DELETE CASCADE,
  user_id INTEGER,
  text TEXT NOT NULL,
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS erp_jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT,
  response_text TEXT,
  error TEXT,
  trigger_ref TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_erp_status ON erp_jobs(status);

CREATE TABLE IF NOT EXISTS cp_runs (
  id INTEGER PRIMARY KEY,
  folder_id INTEGER,
  threshold REAL NOT NULL,
  scanned INTEGER NOT NULL DEFAULT 0,
  groups_found INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'running',
  message TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS cp_groups (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES cp_runs(id),
  label TEXT NOT NULL DEFAULT '',
  score_min REAL NOT NULL,
  score_max REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  standard_document_id INTEGER,
  decided_by INTEGER,
  decided_at TEXT,
  note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cp_members (
  group_id INTEGER NOT NULL REFERENCES cp_groups(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  score REAL NOT NULL,
  is_reference INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, document_id)
);

CREATE TABLE IF NOT EXISTS cp_item_members (
  group_id INTEGER NOT NULL REFERENCES cp_groups(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES items(id),
  score REAL NOT NULL,
  is_reference INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, item_id)
);

CREATE TABLE IF NOT EXISTS import_jobs (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  target TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued',
  phase TEXT NOT NULL DEFAULT '',
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  imported INTEGER NOT NULL DEFAULT 0,
  duplicates INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  target_folder_id INTEGER,
  report_sha TEXT,
  message TEXT NOT NULL DEFAULT '',
  cancel INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS numbering (
  prefix TEXT PRIMARY KEY,
  next_value INTEGER NOT NULL DEFAULT 1,
  width INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  user_id INTEGER,
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id INTEGER,
  details TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit(entity, entity_id);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    # Regex applied to a file name (without extension). Group 1 = part number,
    # group 2 (optional) = description.  Matches "MX-100-020 Main Shaft".
    "part_number_regex": r"^([A-Z0-9]{1,6}(?:-[A-Z0-9]{1,8}){1,4})(?:[\s_]+(.*))?$",
    # ASME Y14.35 recommends skipping these letters.
    "revision_skip_letters": "I,O,Q,S,X,Z",
    "first_revision": "A",
    "require_eco_for_release": False,
    # letters (A, B, C ... skipping the letters above) or numeric (R0, R1, R2); chosen once, before go-live
    "revision_scheme": "letters",
    # four-eyes rule on release: empty = every category; otherwise only these category codes (e.g. ["ROT", "PRV"])
    "four_eyes_categories": [],
    # small companies: an approver may release their own work with a written reason (kept in the audit log)
    "allow_self_approval_with_reason": False,
    # spares only and obsolete are decided through a change order
    "obsolete_needs_change_order": True,
    "company_name": "",
    # Theme of the web client for people who have not chosen one: system (same as their computer), light, dark
    # or contrast.
    "default_theme": "system",
    # Kosha's own ERP settings; the connector's address and keys belong to the connector add-in.
    "erp": {
        "enabled": False,
        "dry_run": True,
        "auto_push_on_release": True,
        "connector": "",
        "default_uom": "Nos",
        "uom_map": {"EA": "Nos", "PCS": "Nos", "NOS": "Nos", "KG": "Kg", "M": "Meter", "MM": "Millimeter"},
    },
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect():
    """This thread's connection: a sqlite3.Connection, or a pgsql.Connection that behaves like one."""
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(conn, "broken", False):
        close_thread_connection()              # PostgreSQL restarted or the network dropped: start afresh
        conn = None
    if conn is None:
        if config.DATABASE_URL:
            from . import pgsql
            conn = pgsql.connect(config.DATABASE_URL)
        else:
            config.ensure_dirs()
            conn = sqlite3.connect(str(config.DB_PATH), timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=FULL")   # a confirmed write survives a power cut
        _local.conn = conn
    return conn


def close_thread_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        _local.conn = None
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except Exception:
            pass
        finally:
            conn.close()


def create_schema(conn, *, defaults: bool = True, only_migrations: Iterable[str] | None = None) -> None:
    """Tables, indexes, default settings and database changes on `conn` (either engine)."""
    if dialect(conn) == "postgresql":
        from . import pgsql
        pgsql.prepare_database(conn)
    conn.executescript(SCHEMA)
    if defaults:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
    # columns added in later versions
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cp_groups)").fetchall()}
    if "kind" not in cols:
        conn.execute("ALTER TABLE cp_groups ADD COLUMN kind TEXT NOT NULL DEFAULT 'geometry'")
    if "standard_item_id" not in cols:
        conn.execute("ALTER TABLE cp_groups ADD COLUMN standard_item_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_versions_sha ON versions(blob_sha)")
    from . import migrations
    migrations.apply(conn, only=only_migrations)


def init() -> None:
    create_schema(connect())
    # merge in new ERP keys added in later versions
    erp = get_setting("erp")
    merged = {**DEFAULT_SETTINGS["erp"], **(erp or {})}
    if merged != erp:
        set_setting("erp", merged)


def _rollback(conn) -> None:
    """Roll back; a connection that cannot is closed, so no transaction is ever left open."""
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except Exception:
        close_thread_connection()


@contextmanager
def tx():
    """Write transaction. Nested use joins the outer transaction.

    Starts like SQLite's BEGIN IMMEDIATE on both engines: one writer at a time, waiting up to 30 s.
    Any error rolls the whole transaction back, including an error while committing.
    """
    conn = connect()
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        _rollback(conn)
        raise
    else:
        try:
            conn.execute("COMMIT")
        except BaseException:
            _rollback(conn)
            raise


def rows(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in connect().execute(sql, tuple(params)).fetchall()]


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    r = connect().execute(sql, tuple(params)).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    r = connect().execute(sql, tuple(params)).fetchone()
    return r[0] if r else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    cur = connect().execute(sql, tuple(params))
    return cur.lastrowid


def insert(table: str, data: dict) -> int:
    cols = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", data.values())


def update(table: str, row_id: int, data: dict) -> None:
    if not data:
        return
    sets = ", ".join(f"{k} = ?" for k in data)
    execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), row_id])


def get_setting(key: str) -> Any:
    v = scalar("SELECT value_json FROM settings WHERE key = ?", (key,))
    if v is None:
        return DEFAULT_SETTINGS.get(key)
    return json.loads(v)


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value_json) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
        (key, json.dumps(value)),
    )


def audit(user_id: int | None, action: str, entity: str, entity_id: int | None, details: str = "") -> None:
    insert("audit", {
        "at": now(), "user_id": user_id, "action": action,
        "entity": entity, "entity_id": entity_id, "details": details,
    })
