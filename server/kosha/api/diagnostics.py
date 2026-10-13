# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin diagnostics: versions, add-ins, queues, disk space, the last backup and the last restore test."""
from __future__ import annotations

import platform
import shutil
import sqlite3
from datetime import datetime, timezone

from .. import __version__, addins, backup, config, db, scan
from ..web import Req, api

BACKUP_MAX_HOURS = 48          # nightly backups: warn when the last good one is older than two days
RESTORE_TEST_MAX_DAYS = 35     # monthly restore tests
DISK_WARN_PERCENT = 10


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return ""


def _age(at: str | None) -> float | None:
    if not at:
        return None
    try:
        t = datetime.fromisoformat(at)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def _counts(sql: str) -> dict:
    try:
        return {r["status"]: r["n"] for r in db.rows(sql)}
    except Exception:
        return {}


def backup_status() -> dict:
    last = db.get_setting(backup.LAST_BACKUP)
    age = _age(last.get("at")) if last else None
    if not last:
        warning = ("No backup has been recorded. Run python -m kosha.backup <folder> every night "
                   "(the Linux installer sets up a timer for it).")
    elif not last.get("ok"):
        warning = f"The last backup failed: {last.get('message', '')}"
    elif age is not None and age > BACKUP_MAX_HOURS:
        warning = f"The last backup is {age / 24:.0f} days old. Check the nightly backup timer."
    else:
        warning = ""
    return {"last": last, "age_hours": round(age, 1) if age is not None else None, "warning": warning}


def restore_test_status() -> dict:
    last = db.get_setting(backup.LAST_RESTORE_TEST)
    age = _age(last.get("at")) if last else None
    if not last:
        warning = ("No restore test has been recorded. Run python -m kosha.backup --verify <backup folder> "
                   "once a month (the Linux installer sets up a timer for it).")
    elif not last.get("ok"):
        warning = f"The last restore test failed: {last.get('message', '')}"
    elif age is not None and age > RESTORE_TEST_MAX_DAYS * 24:
        warning = f"The last restore test is {age / 24:.0f} days old. Test a restore every month."
    else:
        warning = ""
    return {"last": last, "age_days": round(age / 24, 1) if age is not None else None, "warning": warning}


def disk_status() -> dict:
    try:
        u = shutil.disk_usage(config.DATA_DIR)
    except OSError as e:
        return {"path": str(config.DATA_DIR), "error": str(e), "warning": f"Can't read the disk space: {e}"}
    pct = 100 * u.free / u.total if u.total else 0
    return {"path": str(config.DATA_DIR), "free_bytes": u.free, "total_bytes": u.total, "free_percent": round(pct, 1),
            "warning": f"Only {pct:.0f}% of the disk with the data folder is free. Add space before it runs out: "
                       "with a full disk, check-ins and backups stop." if pct < DISK_WARN_PERCENT else ""}


@api("GET", "/api/admin/diagnostics", role="admin")
def diagnostics(req: Req):
    """Versions, add-ins, queues, disk space, last backup and last restore test, virus scanner."""
    changes = db.rows("SELECT id FROM schema_migrations ORDER BY id")
    addin_counts: dict[str, int] = {}
    for a in addins.registry.all():
        addin_counts[a.status] = addin_counts.get(a.status, 0) + 1
    return {
        "versions": {"kosha": __version__, "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                     "starlette": _package_version("starlette"), "uvicorn": _package_version("uvicorn"),
                     "database_changes": len(changes), "latest_database_change": changes[-1]["id"] if changes else "",
                     "platform": platform.platform(terse=True)},
        "addins": addin_counts,
        "queues": {"erp": _counts("SELECT status, COUNT(*) AS n FROM erp_jobs GROUP BY status"),
                   "imports": _counts("SELECT status, COUNT(*) AS n FROM import_jobs GROUP BY status")},
        "disk": disk_status(),
        "backup": backup_status(),
        "restore_test": restore_test_status(),
        "virus_scan": scan.status(),
        "https": config.HTTPS,
        "limits": {"max_upload_mb": config.MAX_UPLOAD_MB},
    }
