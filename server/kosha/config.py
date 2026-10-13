# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime configuration.

Everything can be set with environment variables (or a `kosha.env` file next to
the data folder).  Defaults are chosen so `python -m kosha` just works.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import sys

BASE_DIR = Path(__file__).resolve().parent.parent  # .../server
REPO_DIR = BASE_DIR.parent
STATIC_DIR = REPO_DIR / "web"

# In a source checkout the add-in SDK sits next to the server; installed copies use the package.
_SDK_DIR = REPO_DIR / "sdk" / "python"
if _SDK_DIR.is_dir() and str(_SDK_DIR) not in sys.path:
    try:
        import kosha_sdk  # noqa: F401
    except ImportError:
        sys.path.append(str(_SDK_DIR))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_env_file(BASE_DIR / "kosha.env")

DATA_DIR = Path(os.environ.get("KOSHA_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = Path(os.environ.get("KOSHA_DB_PATH", DATA_DIR / "kosha.db"))
# Empty: SQLite in DB_PATH (no database server needed). postgresql://user:password@host:5432/kosha: PostgreSQL.
DATABASE_URL = os.environ.get("KOSHA_DATABASE_URL", "").strip()
if DATABASE_URL and not DATABASE_URL.startswith(("postgresql://", "postgres://")):
    raise SystemExit("KOSHA_DATABASE_URL must start with postgresql:// (leave it empty to use SQLite).")
BLOB_DIR = DATA_DIR / "blobs"
WORK_DIR = DATA_DIR / "work"

HOST = os.environ.get("KOSHA_HOST", "0.0.0.0")
PORT = int(os.environ.get("KOSHA_PORT", "8080"))

# DWG support: path to the free ODA File Converter (closed freeware, never bundled)
ODA_CONVERTER = os.environ.get("KOSHA_ODA_CONVERTER", "")
# Alternative: LibreDWG's dwg2dxf executable
DWG2DXF = os.environ.get("KOSHA_DWG2DXF", "")

TOKEN_HOURS = int(os.environ.get("KOSHA_TOKEN_HOURS", "12"))
MAX_UPLOAD_MB = int(os.environ.get("KOSHA_MAX_UPLOAD_MB", "1024"))
ERP_WORKER_SECONDS = int(os.environ.get("KOSHA_ERP_WORKER_SECONDS", "20"))

# Add-ins: the official ones shipped in this repository, plus any folders the admin adds.
BUNDLED_ADDIN_DIR = REPO_DIR / "addins"
ADDIN_DIRS = [Path(p) for p in os.environ.get("KOSHA_ADDIN_DIR", "").split(os.pathsep) if p.strip()]
ADDIN_TIMEOUT_SECONDS = float(os.environ.get("KOSHA_ADDIN_TIMEOUT_SECONDS", "60"))
ADDIN_MAX_FAILURES = int(os.environ.get("KOSHA_ADDIN_MAX_FAILURES", "5"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, BLOB_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def secret_key() -> str:
    env = os.environ.get("KOSHA_SECRET")
    if env:
        return env
    ensure_dirs()
    f = DATA_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32), encoding="utf-8")
    return f.read_text(encoding="utf-8").strip()
