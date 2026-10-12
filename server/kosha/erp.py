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

from . import config, db
from .addins import registry

from kosha_sdk import AddinError  # noqa: E402
from kosha_sdk.erp import ErpBom, ErpBomLine, ErpItem  # noqa: E402

log = logging.getLogger("kosha.erp")
MAX_ATTEMPTS = 5
GENERIC_KEYS = ("enabled", "dry_run", "auto_push_on_release", "connector", "default_uom", "uom_map")
GENERIC_DEFAULTS = {"enabled": False, "dry_run": True, "auto_push_on_release": True, "connector": "",
                    "default_uom": "Nos",
                    "uom_map": {"EA": "Nos", "PCS": "Nos", "NOS": "Nos", "KG": "Kg", "M": "Meter", "MM": "Millimeter"}}
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
    """The connector chosen in settings, else the only enabled ERP connector."""
    chosen = settings().get("connector")
    if chosen:
        return chosen
    available = registry.by_kind("erp-connector")
    if not available:
        available = registry.by_kind("erp-connector", enabled_only=False)
    if not available:
        raise ErpError("No ERP connector add-in is installed. Install one under Admin › Add-ins.")
    return available[0].id


def call(method: str, *args):
    return registry.call(connector_id(), method, *args)


def uom(cfg: dict, value: str) -> str:
    value = (value or "").strip()
    m = {k.upper(): v for k, v in (cfg.get("uom_map") or {}).items()}
    return m.get(value.upper(), value or cfg.get("default_uom") or "Nos")


def to_erp_item(cfg: dict, row: dict, revision: str | None = None) -> ErpItem:
    return ErpItem(code=row["part_number"], description=row.get("description") or row["part_number"],
                   revision=revision if revision is not None else (row.get("revision") or ""),
                   uom=uom(cfg, row.get("uom")), material=row.get("material") or "",
                   weight_kg=row.get("weight_kg"), item_group=row.get("item_group") or "",
                   make_or_buy=row.get("make_buy") or row.get("make_or_buy") or "")


# ----------------------------------------------------------------- queue --

def enqueue(kind: str, entity_id: int, user_id: int | None, ref: str = "") -> int:
    with db.tx():
        existing = db.one("SELECT id FROM erp_jobs WHERE kind = ? AND entity_id = ? AND status IN ('pending','failed')",
                          (kind, entity_id))
        now = db.now()
        if existing:
            db.update("erp_jobs", existing["id"], {"status": "pending", "attempts": 0, "trigger_ref": ref,
                                                   "updated_at": now, "error": None})
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


def _bom_lines(bom_id: int) -> list[dict]:
    return db.rows(
        "SELECT i.part_number, i.description, i.uom AS item_uom, bl.qty, bl.uom FROM bom_lines bl "
        "JOIN items i ON i.id = bl.child_item_id WHERE bl.bom_id = ? ORDER BY bl.line_no", (bom_id,))


def run_job(job_id: int) -> dict:
    job = db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))
    if not job:
        return {}
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
        else:
            raise ErpError(f"Unknown job kind {job['kind']}")
    except AddinError as e:     # includes ErpError, the connector's own errors and AddinUnavailable
        attempts = job["attempts"] + 1
        db.update("erp_jobs", job_id, {"status": "failed", "error": str(e),
                                       "response_text": (getattr(e, "detail", "") or "")[:4000], "updated_at": db.now()})
        if attempts >= MAX_ATTEMPTS:
            log.warning("ERP job %s failed %s times: %s", job_id, attempts, e)
    except Exception as e:  # pragma: no cover - a bug in Kosha itself; logged and kept for retry
        log.exception("ERP job %s crashed", job_id)
        db.update("erp_jobs", job_id, {"status": "failed", "error": f"{e.__class__.__name__}: {e}", "updated_at": db.now()})
    return db.one("SELECT * FROM erp_jobs WHERE id = ?", (job_id,))


def _item_preview(item: ErpItem) -> dict:
    return {"item_code": item.code, "item_name": item.description[:140], "stock_uom": item.uom, "revision": item.revision}


def _bom_preview(bom: ErpBom) -> dict:
    return {"item": bom.item.code, "revision": bom.revision, "quantity": 1,
            "items": [{"item_code": ln.code, "qty": ln.qty, "uom": ln.uom} for ln in bom.lines]}


def _finish(job_id: int, status: str, payload, response: str) -> None:
    db.update("erp_jobs", job_id, {"status": status, "payload_json": json.dumps(payload)[:20000],
                                   "response_text": response[:4000], "error": None, "updated_at": db.now()})


_lock = threading.Lock()


def process_pending(limit: int = 100) -> int:
    if not _lock.acquire(blocking=False):
        return 0
    try:
        # items first so BOM components exist in ERP
        jobs = db.rows("SELECT id FROM erp_jobs WHERE status = 'pending' ORDER BY CASE kind WHEN 'item' THEN 0 "
                       "WHEN 'bom' THEN 1 ELSE 2 END, id LIMIT ?", (limit,))
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
