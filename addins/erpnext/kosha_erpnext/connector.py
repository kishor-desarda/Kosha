# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""ERPNext (Frappe) connector.

    Item  -> /api/resource/Item   create, or update only when something changed
    BOM   -> /api/resource/BOM    create and submit when the lines differ from the default BOM
    Stock <- /api/resource/Bin and Item.valuation_rate

Authentication uses an API key and secret generated in ERPNext under User › API Access.
Every method is idempotent, as the ErpConnector contract requires.
"""
from __future__ import annotations

import json
import urllib.parse

from kosha_sdk import AddinContext, Health
from kosha_sdk.erp import ErpBom, ErpConnector, ErpError, ErpEvent, ErpItem, ErpResult, StockInfo
from kosha_sdk.http import HttpError

from . import signing

EVENT_KINDS = {"item_changed", "stock_changed", "bom_cancelled"}


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def frappe_message(raw: str) -> str:
    """The most useful human message inside a Frappe error response."""
    try:
        j = json.loads(raw)
    except ValueError:
        return raw[:300]
    if j.get("_server_messages"):
        try:
            msgs = json.loads(j["_server_messages"])
            texts = [json.loads(m).get("message", m) if isinstance(m, str) and m.startswith("{") else m for m in msgs]
            return " ".join(str(t) for t in texts)[:500]
        except ValueError:
            pass
    if j.get("exception"):
        return str(j["exception"]).split("\n")[-1][:500]
    return str(j.get("message") or j.get("exc_type") or raw[:300])


class ERPNextConnector(ErpConnector):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self.cfg = s
        headers = {}
        if s.get("api_key"):
            headers["Authorization"] = f"token {s.get('api_key')}:{s.get('api_secret', '')}"
        self._base = (s.get("base_url") or "").rstrip("/")
        self._http = ctx.http(self._base or "http://unset", headers=headers, timeout_s=float(s.get("timeout_s") or 30),
                              verify_tls=bool(s.get("verify_tls", True)), retries=2) if self._base else None

    # ------------------------------------------------------------ plumbing
    def _call(self, method: str, path: str, body=None, params=None, allow_404=False):
        if self._http is None:
            raise ErpError("The ERPNext address is not set (ERP settings).")
        try:
            r = self._http.request(method, path, json_body=body, params=params, allow_404=allow_404)
        except HttpError as e:
            if e.status is None:
                raise ErpError(f"Cannot reach ERPNext at {self._base}. {e}") from None
            raise ErpError(frappe_message(e.detail) or str(e), status=e.status, detail=e.detail) from None
        return None if r is None else r.json()

    def _get_item(self, code: str) -> dict | None:
        r = self._call("GET", f"/api/resource/Item/{_q(code)}", allow_404=True)
        return None if r is None else r.get("data")

    # ------------------------------------------------------------ contract
    def health(self) -> Health:
        try:
            user = self._call("GET", "/api/method/frappe.auth.get_logged_user").get("message", "")
        except ErpError as e:
            return Health.down(str(e))
        return Health.up(f"Connected to ERPNext as {user}.")

    def upsert_item(self, item: ErpItem) -> ErpResult:
        cfg = self.cfg
        common = {"item_name": (item.description or item.code)[:140], "description": item.description or item.code}
        if cfg.get("revision_field"):
            common[cfg["revision_field"]] = item.revision
        if cfg.get("material_field") and item.material:
            common[cfg["material_field"]] = item.material
        if item.weight_kg:
            common["weight_per_unit"] = item.weight_kg
            common["weight_uom"] = "Kg"
        existing = self._get_item(item.code)
        if existing:
            if all(existing.get(k) == v for k, v in common.items()):
                return ErpResult.unchanged("unchanged", item.code)
            self._call("PUT", f"/api/resource/Item/{_q(item.code)}", common)
            return ErpResult.updated("updated", common, item.code)
        payload = {
            "item_code": item.code,
            "item_group": item.item_group or cfg.get("default_item_group") or "Products",
            "stock_uom": item.uom or "Nos",
            "is_stock_item": 1,
            "include_item_in_manufacturing": 1,
            **common,
        }
        self._call("POST", "/api/resource/Item", payload)
        return ErpResult.created("created", payload, item.code)

    def _default_bom(self, code: str) -> dict | None:
        res = self._call("GET", "/api/resource/BOM", params={
            "filters": [["item", "=", code], ["docstatus", "<", 2], ["is_active", "=", 1]],
            "fields": ["name", "is_default", "docstatus"], "order_by": "creation desc", "limit_page_length": 20,
        }).get("data") or []
        if not res:
            return None
        pick = next((b for b in res if b.get("is_default")), res[0])
        return self._call("GET", f"/api/resource/BOM/{_q(pick['name'])}").get("data")

    def upsert_bom(self, bom: ErpBom) -> ErpResult:
        wanted = sorted((ln.code.upper(), round(float(ln.qty), 6)) for ln in bom.lines)
        current = self._default_bom(bom.item.code)
        if current:
            have = sorted((str(r.get("item_code", "")).upper(), round(float(r.get("qty", 0)), 6))
                          for r in current.get("items", []))
            if have == wanted:
                return ErpResult.unchanged(f"unchanged ({current['name']})", current["name"])
        payload = {
            "item": bom.item.code, "quantity": 1, "is_active": 1, "is_default": 1,
            "rm_cost_as_per": "Valuation Rate",
            "items": [{"item_code": ln.code, "qty": ln.qty, "uom": ln.uom or "Nos"} for ln in bom.lines],
        }
        if self.cfg.get("company"):
            payload["company"] = self.cfg["company"]
        if self.cfg.get("revision_field"):
            payload[self.cfg["revision_field"]] = bom.revision
        created = self._call("POST", "/api/resource/BOM", payload).get("data") or {}
        name = created.get("name", "")
        if self.cfg.get("submit_boms", True) and name:
            self._call("PUT", f"/api/resource/BOM/{_q(name)}", {"docstatus": 1})
        return ErpResult.created(f"created {name}", payload, name)

    def stock_and_cost(self, code: str) -> StockInfo:
        it = self._get_item(code)
        if not it:
            return StockInfo(exists=False)
        bins = self._call("GET", "/api/resource/Bin", params={
            "filters": [["item_code", "=", code]], "fields": ["warehouse", "actual_qty"], "limit_page_length": 100,
        }).get("data") or []
        return StockInfo(exists=True, stock=sum(float(b.get("actual_qty") or 0) for b in bins),
                         cost=it.get("valuation_rate") or it.get("standard_rate"), warehouses=tuple(bins))

    def handle_webhook(self, headers: dict[str, str], body: bytes) -> list[ErpEvent]:
        """Calls from the Kosha Frappe app inside ERPNext: signed, recent, and only the known kinds."""
        reason = signing.verify(self.cfg.get("webhook_secret") or "", headers, body)
        if reason:
            raise ErpError(reason, status=401)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ErpError("The call from ERPNext is not valid JSON.", status=400) from None
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            raise ErpError("The call from ERPNext has no events.", status=400)
        out = []
        for e in events[:500]:
            if isinstance(e, dict) and e.get("kind") in EVENT_KINDS and isinstance(e.get("code"), str) and e["code"].strip():
                out.append(ErpEvent(kind=e["kind"], code=e["code"].strip()[:140], detail=str(e.get("detail", ""))[:200]))
        return out
