# SPDX-License-Identifier: AGPL-3.0-or-later
"""Health checks that run by themselves (specification 16.5 and 16.6).

* **File fingerprints.** Every stored file is read back once a month and checked against its SHA-256 fingerprint.
  The work is spread out: each run checks the files whose last check is oldest, up to a size budget, so a large
  store is covered over the month without slowing Kosha down. A file whose content no longer matches (silent disk
  damage) is repaired from the backup folder when the copy there is good: the damaged file is kept aside in
  ``damaged/``, never deleted. Either way the admins are told.
* **Nightly consistency check.** Every file version points to a stored file that exists, every BOM line to an
  existing item and BOM. Files whose content was purged on purpose (two admins) are not counted.
* **Alerts.** The admins are told, under the bell and on the channels they chose for *Server health*, when the disk
  is 80% full, the backup failed or is old, the restore test failed or is old, the ERP or notification queues are
  stuck, a file fingerprint does not match, the consistency check found problems, or the HTTPS certificate
  (``KOSHA_TLS_CERT``) ends within 21 days. The same alert is sent at most once a day.

``run_due()`` is called by the background worker; the admin can start each check under Admin › Backups.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, db, notify_outbox, store

log = logging.getLogger("kosha.health")

FINGERPRINT_DAYS = 30                       # every file is read back at least this often
FINGERPRINT_RUN_BYTES = 2 * 1024 ** 3       # read at most this much per run (runs hourly)
DISK_ALERT_USED_PERCENT = 80
QUEUE_STUCK_HOURS = 2
CERT_WARN_DAYS = 21
CONSISTENCY_LAST = "consistency_last"
FINGERPRINT_LAST = "fingerprint_last"
RUNS = "health_runs"
_busy = threading.Lock()

notify_outbox.register_kind("server_health", "Server health (disk, backups, damaged files)", "admins", ["email"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def _admins() -> list[int]:
    return [r["id"] for r in db.rows("SELECT id FROM users WHERE active = 1 AND role = 'admin' ORDER BY id")]


# ------------------------------------------------------------ fingerprints --

def stored_files() -> dict[str, int]:
    """Fingerprint -> size of every file in the store."""
    out: dict[str, int] = {}
    if not config.BLOB_DIR.is_dir():
        return out
    for sub in config.BLOB_DIR.iterdir():
        if not sub.is_dir() or len(sub.name) != 2:
            continue
        for f in sub.iterdir():
            if f.is_file() and len(f.name) == 64 and f.name.startswith(sub.name):
                try:
                    out[f.name] = f.stat().st_size
                except OSError:
                    pass
    return out


def _backup_copy(sha: str) -> Path | None:
    last = db.get_setting("backup_last") or {}
    folder = last.get("folder")
    if not folder:
        return None
    p = Path(folder) / "blobs" / sha[:2] / sha
    return p if p.is_file() else None


def _sha_of(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def repair(sha: str) -> str:
    """Put a good copy back in place of a damaged stored file. Returns '' when repaired, else why not."""
    src = _backup_copy(sha)
    why = "" if src and _sha_of(src) == sha else \
        "the copy in the backup folder is damaged too" if src else "no copy in the backup folder"
    data = None
    if why:                    # a storage add-in may still have a good copy
        from . import replicas
        data = replicas.fetch(sha)
        if data is None:
            return why
    dest = store.path_for(sha)
    damaged = config.DATA_DIR / "damaged"
    damaged.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".repair")
    if data is not None:
        tmp.write_bytes(data)
    else:
        shutil.copy2(src, tmp)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    if _sha_of(tmp) != sha:
        tmp.unlink(missing_ok=True)
        return "the copy did not arrive intact"
    with store.LOCK:
        if dest.exists():       # keep the damaged file aside: nothing is ever thrown away
            os.replace(dest, damaged / f"{sha}.{_now().strftime('%Y%m%d-%H%M%S')}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, dest)
    return ""


def check_fingerprints(budget_bytes: int = FINGERPRINT_RUN_BYTES, everything: bool = False) -> dict:
    """Read back the files checked longest ago (never-checked first), up to ``budget_bytes``.
    ``everything`` checks every file now, whatever the budget."""
    if not _busy.acquire(blocking=False):
        return {"busy": True}
    try:
        files = stored_files()
        last = {r["sha"]: r["checked_at"] for r in db.rows("SELECT sha, checked_at FROM blob_checks")}
        order = sorted(files, key=lambda s: last.get(s, ""))
        due_before = _iso(_now() - timedelta(days=FINGERPRINT_DAYS))
        checked, read, bad, repaired = 0, 0, [], []
        t0 = time.monotonic()
        for sha in order:
            if not everything and (read >= budget_bytes or last.get(sha, "") > due_before):
                break
            ok = store.verify(sha)
            note, fixed_at = "", None
            if not ok:
                why = repair(sha)
                if why:
                    note = f"Damaged; not repaired: {why}."
                    bad.append({"sha": sha, "why": why})
                else:
                    ok, fixed_at, note = True, db.now(), "Damaged; repaired from a good copy (backup folder or storage add-in)."
                    repaired.append(sha)
            with db.tx():
                db.execute("INSERT INTO blob_checks(sha, checked_at, ok, size, repaired_at, note) "
                           "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(sha) DO UPDATE SET checked_at = excluded.checked_at, "
                           "ok = excluded.ok, size = excluded.size, "
                           "repaired_at = COALESCE(excluded.repaired_at, blob_checks.repaired_at), note = excluded.note",
                           (sha, db.now(), 1 if ok else 0, files[sha], fixed_at, note))
            checked += 1
            read += files[sha]
        result = {"at": db.now(), "checked": checked, "bytes": read, "damaged": len(bad) + len(repaired),
                  "repaired": len(repaired), "not_repaired": bad[:50], "seconds": round(time.monotonic() - t0, 1)}
        db.set_setting(FINGERPRINT_LAST, result)
        for sha in repaired:
            db.audit(None, "file_repaired", "blob", None, sha)
            _alert(f"repaired:{sha}", "A damaged stored file was repaired",
                   f"The stored file {sha[:12]}… no longer matched its fingerprint (disk damage). A good copy from the "
                   "backup folder or a storage add-in was put back; the damaged file is kept in the damaged folder. Check the disk.",
                   severity="warning")
        for b in bad:
            db.audit(None, "file_damaged", "blob", None, f"{b['sha']}: {b['why']}")
        return result
    finally:
        _busy.release()


def fingerprint_status() -> dict:
    files = stored_files()
    since = _iso(_now() - timedelta(days=FINGERPRINT_DAYS))
    rows = {r["sha"]: r for r in db.rows("SELECT * FROM blob_checks")}
    recent = sum(1 for s in files if s in rows and rows[s]["checked_at"] >= since)
    damaged = [{"sha": s, "checked_at": r["checked_at"], "note": r["note"]}
               for s, r in rows.items() if not r["ok"] and s in files]
    repaired = [{"sha": r["sha"], "repaired_at": r["repaired_at"]}
                for r in db.rows("SELECT sha, repaired_at FROM blob_checks WHERE repaired_at IS NOT NULL "
                                 "ORDER BY repaired_at DESC LIMIT 10")]
    return {"files": len(files), "bytes": sum(files.values()), "checked_last_30_days": recent,
            "damaged": damaged[:50], "repaired": repaired, "last": db.get_setting(FINGERPRINT_LAST)}


# ------------------------------------------------------------- consistency --

def check_consistency() -> dict:
    """Every version points to a stored file that exists; every BOM line to an existing item and BOM."""
    problems: list[dict] = []
    files = stored_files()
    for v in db.rows("SELECT v.document_id, v.version, v.blob_sha, d.filename FROM versions v "
                     "LEFT JOIN documents d ON d.id = v.document_id "
                     "WHERE d.id IS NULL OR d.purged_at IS NULL ORDER BY v.document_id, v.version"):
        if not v["filename"]:
            problems.append({"kind": "version_without_file_record", "link": "",
                             "text": f"Version {v['version']} belongs to file record {v['document_id']}, which "
                                     "does not exist."})
        elif v["blob_sha"] not in files:
            problems.append({"kind": "missing_content", "link": f"#/doc/{v['document_id']}",
                             "text": f"{v['filename']} version {v['version']}: the stored file is missing."})
    for r in db.rows("SELECT l.id, l.bom_id, l.child_item_id, b.id AS bom, i.id AS item, p.part_number "
                     "FROM bom_lines l LEFT JOIN boms b ON b.id = l.bom_id "
                     "LEFT JOIN items i ON i.id = l.child_item_id LEFT JOIN items p ON p.id = b.item_id "
                     "WHERE b.id IS NULL OR i.id IS NULL ORDER BY l.id"):
        if not r["bom"]:
            problems.append({"kind": "bom_line_without_bom", "link": "",
                             "text": f"BOM line {r['id']} belongs to BOM {r['bom_id']}, which does not exist."})
        else:
            problems.append({"kind": "bom_line_without_item", "link": f"#/bom/{r['bom_id']}",
                             "text": f"A line of the BOM of {r['part_number']} points to item {r['child_item_id']}, "
                                     "which does not exist."})
    result = {"at": db.now(), "ok": not problems, "problems": problems[:100], "count": len(problems),
              "versions": db.scalar("SELECT COUNT(*) FROM versions"),
              "bom_lines": db.scalar("SELECT COUNT(*) FROM bom_lines")}
    db.set_setting(CONSISTENCY_LAST, result)
    return result


# ------------------------------------------------------------------ alerts --

def _alert(key: str, title: str, body: str, *, severity: str = "warning") -> int:
    day = _now().strftime("%Y-%m-%d")
    return notify_outbox.enqueue(_admins(), title, body, "#/admin?tab=backups", "server_health", severity=severity,
                                 key=f"health:{key}:{day}", source="health")


def _cert_days_left() -> int | None:
    path = os.environ.get("KOSHA_TLS_CERT", "").strip()
    if not path or not Path(path).is_file():
        return None
    try:
        import ssl
        info = ssl._ssl._test_decode_cert(path)       # standard library; reads a PEM certificate file
        end = datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return (end - _now()).days


def problems() -> list[dict]:
    """What is wrong right now, each with a key, a title and what to do."""
    from .api.diagnostics import backup_status, restore_test_status
    out = []
    try:
        u = shutil.disk_usage(config.DATA_DIR)
        used = 100 - 100 * u.free / u.total if u.total else 0
        if used >= DISK_ALERT_USED_PERCENT:
            out.append({"key": "disk", "severity": "warning" if used < 95 else "error",
                        "title": f"The data disk is {used:.0f}% full",
                        "text": "Add space or move old backups away. With a full disk, check-ins and backups stop."})
    except OSError:
        pass
    b = backup_status()
    if b["warning"]:
        out.append({"key": "backup", "severity": "error", "title": "Backups need attention", "text": b["warning"]})
    t = restore_test_status()
    if t["warning"]:
        out.append({"key": "restore_test", "severity": "warning", "title": "Restore test needs attention",
                    "text": t["warning"]})
    stuck = _iso(_now() - timedelta(hours=QUEUE_STUCK_HOURS))
    erp = db.scalar("SELECT COUNT(*) FROM erp_jobs WHERE status IN ('pending', 'failed') AND created_at < ?", (stuck,))
    if erp:
        out.append({"key": "erp_queue", "severity": "warning", "title": f"{erp} ERP transfer(s) waiting for over "
                    f"{QUEUE_STUCK_HOURS} hours", "text": "Releases are safe in Kosha; they reach the ERP once it "
                    "answers. Open ERP › Queue to see the reason and send them again."})
    recon = db.one("SELECT * FROM erp_recon_runs WHERE started_by IS NULL AND status = 'done' ORDER BY id DESC LIMIT 1")
    if recon and recon["differences"]:
        out.append({"key": "erp_reconcile", "severity": "warning", "title": f"The nightly ERP check found "
                    f"{recon['differences']} difference(s)", "text": "Open ERP sync › Reconciliation to see them; "
                    f"{recon['fixable']} can be fixed by sending again."})
    notes = db.scalar("SELECT COUNT(*) FROM notify_outbox WHERE status = 'failed' AND updated_at >= ?",
                      (_iso(_now() - timedelta(days=1)),))
    if notes:
        out.append({"key": "notify_queue", "severity": "warning", "title": f"{notes} notification(s) could not be "
                    "delivered today", "text": "Check the mail server or chat add-in under Admin › Notifications."})
    damaged = db.scalar("SELECT COUNT(*) FROM blob_checks WHERE ok = 0")
    if damaged:
        out.append({"key": "fingerprints", "severity": "error", "title": f"{damaged} stored file(s) are damaged",
                    "text": "Their content no longer matches the fingerprint and no good copy was found in the "
                            "backup folder. Restore them from an older backup; see Admin › Backups."})
    c = db.get_setting(CONSISTENCY_LAST)
    if c and not c.get("ok"):
        out.append({"key": "consistency", "severity": "error",
                    "title": f"The nightly check found {c['count']} problem(s)",
                    "text": "Some records point to files or items that are missing. See the list under Admin › Backups."})
    days = _cert_days_left()
    if days is not None and days <= CERT_WARN_DAYS:
        out.append({"key": "certificate", "severity": "error" if days <= 7 else "warning",
                    "title": "The HTTPS certificate has ended" if days < 0 else f"The HTTPS certificate ends in {days} days",
                    "text": "Renew it before browsers refuse to open Kosha."})
    return out


def send_alerts() -> int:
    n = 0
    for p in problems():
        n += _alert(p["key"], p["title"], p["text"], severity=p["severity"])
    return n


# ------------------------------------------------------------------ timing --

PERIODS = {"fingerprints": 3600, "consistency": 24 * 3600, "erp_reconcile": 24 * 3600, "alerts": 3600,
           "practice": 24 * 3600, "storage_copies": 3600}


def run_due() -> dict:
    """Run every check whose time has come (called by the background worker). Never raises."""
    runs = db.get_setting(RUNS) or {}
    done = {}
    now = _now()
    for name, seconds in PERIODS.items():
        last = runs.get(name)
        if last and (now - datetime.fromisoformat(last)).total_seconds() < seconds:
            continue
        try:
            if name == "fingerprints":
                done[name] = check_fingerprints()
            elif name == "consistency":
                done[name] = check_consistency()
            elif name == "erp_reconcile":
                from . import erp_reconcile
                done[name] = erp_reconcile.nightly()
            elif name == "practice":
                from . import practice
                done[name] = practice.clear()
            elif name == "storage_copies":
                from . import replicas
                done[name] = {**replicas.copy_due(), "restore": replicas.restore_missing()}
            else:
                done[name] = send_alerts()
        except Exception:
            log.exception("Health check %s failed", name)
        runs[name] = _iso(now)
        db.set_setting(RUNS, runs)
    return done


def status() -> dict:
    from . import replicas
    return {"fingerprints": fingerprint_status(), "consistency": db.get_setting(CONSISTENCY_LAST),
            "problems": problems(), "runs": db.get_setting(RUNS) or {}, "storage_copies": replicas.status()}


def start_worker() -> threading.Thread:
    """Checks in their own thread, so reading files back never delays notifications or ERP transfers."""
    first = float(os.environ.get("KOSHA_HEALTH_FIRST_SECONDS", "600"))      # let a fresh start settle first

    def loop():
        time.sleep(first)
        while True:
            try:
                run_due()
            except Exception:  # pragma: no cover
                log.exception("Health worker error")
            finally:
                db.close_thread_connection()
            time.sleep(300)

    t = threading.Thread(target=loop, name="health-worker", daemon=True)
    t.start()
    return t
