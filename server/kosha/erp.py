# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP integration: the queue, retries, dry run and unit mapping.

Released items and BOMs are queued in `erp_jobs` and handed to the ERP connector add-in
chosen in the ERP settings (the official one talks to ERPNext; others plug into the same
`kosha_sdk.erp.ErpConnector` contract). Kosha never calls an ERP directly: every call goes
through the add-in guard, so a slow or broken connector cannot harm Kosha's own data.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, db
from .addins import registry

from kosha_sdk import AddinError  # noqa: E402
from kosha_sdk.erp import ErpBom, ErpBomLine, ErpItem, ErpNotSupported  # noqa: E402

log = logging.getLogger("kosha.erp")
MAX_ATTEMPTS = 5
RETRY_FIRST_SECONDS = 60          # a failed job is tried again after 1, 4, 16 and 64 minutes, then waits for a person
GENERIC_KEYS = ("enabled", "dry_run", "auto_push_on_release", "connector", "default_uom", "uom_map", "category_map",
                "attach_pdfs", "nightly_reconcile")
GENERIC_DEFAULTS = {"enabled": False, "dry_run": True, "auto_push_on_release": True, "connector": "",
                    "default_uom": "Nos",
                    "uom_map": {"EA": "Nos", "PCS": "Nos", "NOS": "Nos", "KG": "Kg", "M": "Meter", "MM": "Millimeter"},
                    "category_map": {}, "attach_pdfs": True, "nightly_reconcile": True}
ERPNEXT_ID = "org.kosha.erpnext"


class ErpError(AddinError):
    """A problem Kosha found before calling the ERP (missing item, empty BOM, switched off)."""


def settings() -> dict:
    """Kosha's own ERP settings. Connector settings (address, keys) belong to the connector add-in."""
    raw = db.get_setting("erp") or {}
    _migrate_old_settings(raw)
    cfg = dict(GENERIC_DEFAULTS)
    cfg.update({k: v for k, v in raw.items() if k in GENERIC_KEYS})
    return cfg


def _migrate_old_settings(raw: dict) -> None:
    """Early versions kept ERPNext's address and keys in the core settings. Move them, losing nothing."""
    moved = {k: v for k, v in raw.items() if k not in GENERIC_KEYS}
    if not moved:
        return
    current = db.get_setting(f"addin:{ERPNEXT_ID}") or {}
    db.set_setting(f"addin:{ERPNEXT_ID}", {**moved, **current})
    db.set_setting("erp", {k: v for k, v in raw.items() if k in GENERIC_KEYS})
    log.info("Moved ERPNext connection settings to the ERPNext add-in")


def save_settings(changes: dict) -> dict:
    cfg = settings()
    for k, v in changes.items():
        if k in GENERIC_KEYS:
            cfg[k] = v
    db.set_setting("erp", cfg)
    return cfg


def connector_id() -> str:
    """The connector chosen in settings, else the first enabled ERP connector.

    Installations that never chose one were made when ERPNext was the only connector, so it stays the default
    then, whatever other connectors are added (folders are found in alphabetical order)."""
    chosen = settings().get("connector")
    if chosen:
        return chosen
    available = registry.by_kind("erp-connector")
    if not available:
        available = registry.by_kind("erp-connector", enabled_only=False)
    if not available:
        raise ErpError("No ERP connector add-in is installed. Install one under Admin › Add-ins.")
    return min(available, key=lambda a: a.id != ERPNEXT_ID).id


def call(method: str, *args):
    return registry.call(connector_id(), method, *args)


def uom(cfg: dict, value: str) -> str:
    value = (value or "").strip()
    m = {k.upper(): v for k, v in (cfg.get("uom_map") or {}).items()}
    return m.get(value.upper(), value or cfg.get("default_uom") or "Nos")


def category_mapping(cfg: dict, part_number: str) -> dict:
    """Item group and HSN code for a part's category: the admin's mapping first, else the industry pack's."""
    from .release_rules import category_of
    cat = category_of(part_number) or {}
    code = str(cat.get("code") or (part_number or "").split("-", 1)[0]).upper()
    mine = (cfg.get("category_map") or {}).get(code) or {}
    return {"item_group": mine.get("item_group") or cat.get("erp_item_group") or "",
            "hsn": str(mine.get("hsn") or cat.get("hsn") or "")}


def to_erp_item(cfg: dict, row: dict, revision: str | None = None) -> ErpItem:
    mapped = category_mapping(cfg, row["part_number"])
    refs: tuple[str, ...] = ()
    if row.get("id"):
        refs = tuple(" ".join(x for x in (r["oem"], r["oem_part_number"],
                                          f"(model {r['oem_model']})" if r["oem_model"] else "") if x)
                     for r in db.rows("SELECT oem, oem_part_number, oem_model FROM item_oem_refs WHERE item_id = ? "
                                      "AND deleted = 0 ORDER BY id", (row["id"],)))
    state = row.get("state") or ""
    return ErpItem(code=row["part_number"], description=row.get("description") or row["part_number"],
                   revision=revision if revision is not None else (row.get("revision") or ""),
                   uom=uom(cfg, row.get("uom")), material=row.get("material") or "",
                   weight_kg=row.get("weight_kg"), item_group=row.get("item_group") or mapped["item_group"],
                   make_or_buy=row.get("make_buy") or row.get("make_or_buy") or "",
                   hsn=row.get("hsn") or mapped["hsn"],
                   status=state if state in ("spares_only", "obsolete") else "",
                   oem_refs=refs, customer=(row.get("customer") or "") if row.get("restricted") else "")


# ----------------------------------------------------------------- queue --

def enqueue(kind: str, entity_id: int, user_id: int | None, ref: str = "") -> int:
    with db.tx():
        existing = db.one("SELECT id FROM erp_jobs WHERE kind = ? AND entity_id = ? AND status IN ('pending','failed')",
                          (kind, entity_id))
        now = db.now()
        if existing:
            db.update("erp_jobs", existing["id"], {"status": "pending", "attempts": 0, "trigger_ref": ref,
                                                   "updated_at": now, "error": None, "next_try_at": None})
            return existing["id"]
        return db.insert("erp_jobs", {"kind": kind, "entity_id": entity_id, "status": "pending", "attempts": 0,
                                      "trigger_ref": ref, "created_by": user_id, "created_at": now, "updated_at": now})


def on_release(item_id: int, user_id: int | None, ref: str) -> None:
    cfg = settings()
    if not cfg.get("auto_push_on_release", True):
        return
    item = db.one("SELECT * FROM items WHERE id = ?", (item_id,))
    if not item:
        return
    enqueue("item", item_id, user_id, ref)
    bom = db.one("SELECT id FROM boms WHERE item_id = ? AND revision = ? AND state = 'released'",
                 (item_id, item["revision"]))
    if bom:
        enqueue("bom", bom["id"], user_id, ref)


def on_status(item_id: int, user_id: int | None, ref: str) -> None:
    """A part became spares only or obsolete: the ERP item is updated (flagged, or disabled)."""
    if settings().get("auto_push_on_release", True) and db.one("SELECT 1 FROM items WHERE id = ?", (item_id,)):
        enqueue("item", item_id, user_id, ref)


def on_outputs(doc_id: int, user_id: int | None, ref: str) -> None:
    """The stamped PDF of a released drawing is ready: attach it to the ERP item (earlier revisions come off)."""
    cfg = settings()
    if not (cfg.get("auto_push_on_release", True) and cfg.get("attach_pdfs", True)):
        return
    if db.one("SELECT 1 FROM release_outputs o JOIN documents d ON d.id = o.document_id AND d.revision = o.revision "
              "WHERE o.document_id = ? AND o.kind = 'released_pdf' AND o.status = 'ok' AND d.item_id IS NOT NULL",
              (doc_id,)):
        enqueue("attach", doc_id, user_id, ref)


def _released_pdf(doc_id: int) -> tuple[dict, dict, bytes]:
    from . import store
    d = db.one("SELECT d.*, i.part_number AS item_code FROM documents d JOIN items i ON i.id = d.item_id "
               "WHERE d.id = ?", (doc_id,))
    if not d:
        raise ErpError("The file no longer exists in Kosha, or it is not linked to a part.")
    o = db.one("SELECT * FROM release_outputs WHERE document_id = ? AND revision = ? AND kind = 'released_pdf' "
               "AND status = 'ok' ORDER BY version DESC LIMIT 1", (doc_id, d["revision"]))
    if not o or not o["blob_sha"] or not store.exists(o["blob_sha"]):
        raise ErpError(f"There is no stamped PDF of {d['filename']} rev {d['revision']}. Make it again on the file's "
                       "Release outputs tab, then retry.")
    return d, o, store.read_bytes(o["blob_sha"])


def _bom_lines(bom_id: int) -> list[dict]:
    return db.rows(
        "SELECT i.part_number, i.description, i.uom AS item_uom, bl.qty, bl.uom FROM bom_lines bl "
        "JOIN items i ON i.id = bl.child_item_id WHERE bl.bom_id = ? ORDER BY bl.line_no", (bom_id,))


def run_job(job_id: int) -> dict:
    job = db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))
    if not job:
        return {}
    from . import practice
    if practice.erp_job_is_practice(job["kind"], job["entity_id"]):
        _finish(job_id, "skipped", {}, "A practice part (Training — safe to try): never sent to the ERP.")
        return db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))
    cfg = settings()
    db.update("erp_jobs", job_id, {"status": "running", "attempts": job["attempts"] + 1, "updated_at": db.now()})
    try:
        live = cfg.get("enabled") and not cfg.get("dry_run")
        if job["kind"] == "item":
            row = db.one("SELECT * FROM items WHERE id = ?", (job["entity_id"],))
            if not row:
                raise ErpError("Item no longer exists in Kosha")
            item = to_erp_item(cfg, row)
            if not live:
                _finish(job_id, "dry_run", _item_preview(item), "Dry run: ERP push is switched off, nothing was sent.")
                return db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))
            result = call("upsert_item", item)
            db.update("items", row["id"], {"erp_item_code": result.external_id or row["part_number"],
                                           "erp_synced_at": db.now()})
            _finish(job_id, "sent", result.payload, f"Item {result.message}")
        elif job["kind"] == "bom":
            bom = db.one("SELECT b.*, i.part_number, i.description, i.revision AS item_revision, i.uom, i.material, "
                         "i.weight_kg, i.item_group FROM boms b JOIN items i ON i.id = b.item_id WHERE b.id = ?",
                         (job["entity_id"],))
            if not bom:
                raise ErpError("BOM no longer exists in Kosha")
            lines = _bom_lines(bom["id"])
            if not lines:
                raise ErpError("The BOM has no lines")
            parent = to_erp_item(cfg, bom, revision=bom["revision"])
            erp_bom = ErpBom(item=parent, revision=bom["revision"],
                             lines=tuple(ErpBomLine(ln["part_number"], float(ln["qty"]), uom(cfg, ln["uom"] or ln["item_uom"]))
                                         for ln in lines))
            if not live:
                _finish(job_id, "dry_run", _bom_preview(erp_bom), "Dry run: ERP push is switched off, nothing was sent.")
                return db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))
            # the ERP needs every component, and the parent, to exist first
            for ln in lines:
                child = db.one("SELECT * FROM items WHERE part_number = ? COLLATE NOCASE", (ln["part_number"],))
                if child and not child["erp_synced_at"]:
                    call("upsert_item", to_erp_item(cfg, child))
                    db.update("items", child["id"], {"erp_item_code": child["part_number"], "erp_synced_at": db.now()})
            if not db.scalar("SELECT erp_synced_at FROM items WHERE id = ?", (bom["item_id"],)):
                call("upsert_item", to_erp_item(cfg, db.one("SELECT * FROM items WHERE id = ?", (bom["item_id"],))))
                db.update("items", bom["item_id"], {"erp_item_code": bom["part_number"], "erp_synced_at": db.now()})
            result = call("upsert_bom", erp_bom)
            _finish(job_id, "sent", result.payload, f"BOM {result.message}")
        elif job["kind"] == "refresh":
            row = db.one("SELECT * FROM items WHERE id = ?", (job["entity_id"],))
            if not row:
                raise ErpError("Item no longer exists in Kosha")
            if not cfg.get("enabled"):
                raise ErpError("ERP connection is switched off")
            info = call("stock_and_cost", row["part_number"])
            db.update("items", row["id"], {"erp_stock": info.stock, "erp_cost": info.cost,
                                           "erp_item_code": row["part_number"] if info.exists else row["erp_item_code"]})
            _finish(job_id, "sent", {}, json.dumps({"exists": info.exists, "stock": info.stock, "cost": info.cost,
                                                    "warehouses": list(info.warehouses)})[:4000])
        elif job["kind"] == "attach":
            d, o, data = _released_pdf(job["entity_id"])
            name = f"{d['item_code']} Rev {d['revision']}.pdf"
            preview = {"item_code": d["item_code"], "revision": d["revision"], "file_name": name, "size": len(data)}
            if not live:
                _finish(job_id, "dry_run", preview, "Dry run: ERP push is switched off, nothing was sent.")
                return db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))
            try:
                result = call("attach_file", d["item_code"], d["revision"], name, data)
            except ErpNotSupported as e:
                _finish(job_id, "skipped", preview, str(e))
                return db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))
            _finish(job_id, "sent", preview, f"Drawing {result.message}")
        else:
            raise ErpError(f"Unknown job kind {job['kind']}")
    except AddinError as e:     # includes ErpError, the connector's own errors and AddinUnavailable
        _failed(job, str(e), (getattr(e, "detail", "") or "")[:4000])
    except Exception as e:  # pragma: no cover - a bug in Kosha itself; logged and kept for retry
        log.exception("ERP job %s crashed", job_id)
        _failed(job, f"{e.__class__.__name__}: {e}", "")
    return db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))


def retry_delay(attempts: int) -> int | None:
    """Seconds to wait before trying a job again by itself after its n-th failure; None after the last one."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return RETRY_FIRST_SECONDS * 4 ** (attempts - 1)


def _failed(job: dict, error: str, detail: str) -> None:
    attempts = job["attempts"] + 1
    wait = retry_delay(attempts)
    next_try = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=wait)).isoformat() if wait else None
    db.update("erp_jobs", job["id"], {"status": "failed", "error": error, "response_text": detail,
                                      "next_try_at": next_try, "updated_at": db.now()})
    if wait is None:
        log.warning("ERP job %s failed %s times: %s", job["id"], attempts, error)
    if attempts == 1 or wait is None:       # people hear about the first failure and the last, not every retry
        _failed_event(job, attempts, error)


def _failed_event(job: dict, attempts: int, error: str) -> None:
    """Tell listeners (notices to approvers and admins) and add-ins; runs in the background, never fails the job."""
    registry.emit("erp.job_failed", {"job_id": job["id"], "kind": job["kind"], "entity_id": job["entity_id"],
                                     "attempts": attempts, "error": error[:1000]})


def _item_preview(item: ErpItem) -> dict:
    return {"item_code": item.code, "item_name": item.description[:140], "stock_uom": item.uom, "revision": item.revision}


def _bom_preview(bom: ErpBom) -> dict:
    return {"item": bom.item.code, "revision": bom.revision, "quantity": 1,
            "items": [{"item_code": ln.code, "qty": ln.qty, "uom": ln.uom} for ln in bom.lines]}


def _finish(job_id: int, status: str, payload, response: str) -> None:
    db.update("erp_jobs", job_id, {"status": status, "payload_json": json.dumps(payload)[:20000],
                                   "response_text": response[:4000], "error": None, "next_try_at": None,
                                   "updated_at": db.now()})


_lock = threading.Lock()


def process_pending(limit: int = 100) -> int:
    if not _lock.acquire(blocking=False):
        return 0
    try:
        # items first so BOM components exist in ERP; failed jobs come back by themselves once their wait is over
        jobs = db.rows("SELECT id FROM erp_jobs WHERE status = 'pending' OR (status = 'failed' AND attempts < ? AND "
                       "next_try_at IS NOT NULL AND next_try_at <= ?) ORDER BY CASE kind WHEN 'item' THEN 0 "
                       "WHEN 'bom' THEN 1 ELSE 2 END, id LIMIT ?", (MAX_ATTEMPTS, db.now(), limit))
        for j in jobs:
            run_job(j["id"])
        return len(jobs)
    finally:
        _lock.release()


def start_worker() -> threading.Thread:
    def loop():
        while True:
            time.sleep(config.ERP_WORKER_SECONDS)
            try:
                process_pending()
            except Exception:  # pragma: no cover
                log.exception("ERP worker error")

    t = threading.Thread(target=loop, name="erp-worker", daemon=True)
    t.start()
    return t
