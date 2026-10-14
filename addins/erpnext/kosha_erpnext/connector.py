# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""ERPNext (Frappe) connector.

    Item  -> /api/resource/Item   create, or update only when something changed (HSN, OEM references, spares only
                                  and customer go to the fields named in the settings; obsolete disables the item)
    BOM   -> /api/resource/BOM    create and submit when the lines differ from the default BOM
    PDF   -> /api/resource/File   the released drawing attached to its Item; earlier revisions taken off
    Stock <- /api/resource/Bin and Item.valuation_rate
    Reconciliation <- /api/resource/Item and BOM, read only

Authentication uses an API key and secret generated in ERPNext under User › API Access.
Every method is idempotent, as the ErpConnector contract requires.
"""
from __future__ import annotations

import base64
import html
import json
import re
import urllib.parse

from kosha_sdk import AddinContext, Health
from kosha_sdk.erp import (ErpBom, ErpBomLine, ErpBomRecord, ErpConnector, ErpError, ErpEvent, ErpItem, ErpItemRecord,
                           ErpNotSupported, ErpResult, StockInfo)
from kosha_sdk.http import HttpError

from . import signing

EVENT_KINDS = {"item_changed", "stock_changed", "bom_cancelled", "sales_order", "purchase_order", "rfq",
               "subcontracting_order", "customer", "supplier", "price_list"}


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def plain_text(value: str) -> str:
    """ERPNext keeps descriptions as HTML once someone edits them in its editor; compare the words only."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return " ".join(text.split())


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
        if cfg.get("hsn_field") and item.hsn:
            common[cfg["hsn_field"]] = item.hsn
        if cfg.get("oem_field"):
            common[cfg["oem_field"]] = "; ".join(item.oem_refs)
        if cfg.get("customer_field"):
            common[cfg["customer_field"]] = item.customer
        if cfg.get("spares_only_field"):
            common[cfg["spares_only_field"]] = 1 if item.status == "spares_only" else 0
        if item.status == "obsolete":
            common["disabled"] = 1          # only ever switched off from Kosha; switching back on is the ERP's call
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
        with_ops = bool(bom.operations) and bool(self.cfg.get("send_operations"))
        wanted = sorted((ln.code.upper(), round(float(ln.qty), 6)) for ln in bom.lines)
        wanted_ops = sorted((o.description, o.work_centre, round(float(o.run_min), 2)) for o in bom.operations) \
            if with_ops else []
        current = self._default_bom(bom.item.code)
        if current:
            have = sorted((str(r.get("item_code", "")).upper(), round(float(r.get("qty", 0)), 6))
                          for r in current.get("items", []))
            have_ops = sorted((str(r.get("operation") or ""), str(r.get("workstation") or ""),
                               round(float(r.get("time_in_mins") or 0), 2)) for r in current.get("operations") or []) \
                if with_ops else []
            if have == wanted and have_ops == wanted_ops:
                return ErpResult.unchanged(f"unchanged ({current['name']})", current["name"])
        names = {o.op_no: o.description for o in bom.operations}
        payload = {
            "item": bom.item.code, "quantity": 1, "is_active": 1, "is_default": 1,
            "rm_cost_as_per": "Valuation Rate",
            "items": [{"item_code": ln.code, "qty": ln.qty, "uom": ln.uom or "Nos",
                       **({"operation": names[ln.op_no]} if with_ops and ln.op_no in names else {})} for ln in bom.lines],
        }
        if with_ops:     # the Operation and Workstation records must exist in ERPNext under these names
            payload["with_operations"] = 1
            payload["operations"] = [{"operation": o.description, "workstation": o.work_centre,
                                      "time_in_mins": o.run_min, "description": o.description}
                                     for o in bom.operations]
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

    # ------------------------------------------------- reading back (reconciliation)
    def _item_fields(self) -> list[str]:
        fields = ["item_code", "item_name", "description", "stock_uom", "disabled"]
        fields += [f for f in (self.cfg.get("revision_field"), self.cfg.get("material_field")) if f]
        return fields

    def _record(self, row: dict) -> ErpItemRecord:
        rev, mat = self.cfg.get("revision_field"), self.cfg.get("material_field")
        return ErpItemRecord(
            code=str(row.get("item_code") or ""),
            description=plain_text(row.get("description") or row.get("item_name") or ""),
            uom=row.get("stock_uom") or "",
            revision=str(row.get(rev) or "") if rev else None,
            material=str(row.get(mat) or "") if mat else None,
            active=not row.get("disabled"),
            from_kosha=bool(row.get(rev)) if rev else None)

    def fetch_items(self, codes) -> dict[str, ErpItemRecord]:
        wanted = [c for c in dict.fromkeys(codes) if c]
        out: dict[str, ErpItemRecord] = {}
        for start in range(0, len(wanted), 100):
            chunk = wanted[start:start + 100]
            rows = self._call("GET", "/api/resource/Item", params={
                "filters": [["item_code", "in", chunk]], "fields": self._item_fields(),
                "limit_page_length": len(chunk)}).get("data") or []
            for row in rows:
                rec = self._record(row)
                out[rec.code] = rec
        return out

    def fetch_bom(self, code: str) -> ErpBomRecord | None:
        current = self._default_bom(code)
        if not current:
            return None
        rev = self.cfg.get("revision_field")
        lines = tuple(ErpBomLine(str(r.get("item_code") or ""), float(r.get("qty") or 0), str(r.get("uom") or ""))
                      for r in current.get("items", []))
        return ErpBomRecord(code=str(current.get("item") or code), lines=lines,
                            revision=str(current.get(rev) or "") if rev else None, reference=str(current.get("name") or ""))

    def list_items_from_kosha(self) -> list[str]:
        rev = self.cfg.get("revision_field")
        if not rev:
            raise ErpNotSupported("Set the revision custom field in the ERPNext connection settings, so Kosha can "
                                  "recognise the items it created in ERPNext.")
        out: list[str] = []
        start, page = 0, 500
        while True:
            rows = self._call("GET", "/api/resource/Item", params={
                "filters": [[rev, "is", "set"]], "fields": ["item_code"], "limit_start": start,
                "limit_page_length": page}).get("data") or []
            out += [str(r.get("item_code")) for r in rows if r.get("item_code")]
            if len(rows) < page:
                return out
            start += page

    # ------------------------------------------------- attachments
    def attach_file(self, code: str, revision: str, filename: str, content: bytes) -> ErpResult:
        """Attach the released PDF to the Item as a private file, then take off the PDFs of earlier revisions that
        Kosha attached (named "<code> Rev <x>.pdf"). Files people attached by hand are never touched."""
        if not self._get_item(code):
            raise ErpError(f"The item {code} is not in ERPNext yet. It is sent first; retry this afterwards.")
        files = self._call("GET", "/api/resource/File", params={
            "filters": [["attached_to_doctype", "=", "Item"], ["attached_to_name", "=", code],
                        ["file_name", "like", f"{code} Rev %"]],
            "fields": ["name", "file_name", "file_size"], "limit_page_length": 100}).get("data") or []
        same = [f for f in files if f.get("file_name") == filename and int(f.get("file_size") or 0) == len(content)]
        if same:
            result = ErpResult.unchanged(f"{filename} already attached", same[0]["name"])
        else:
            created = self._call("POST", "/api/resource/File", {
                "file_name": filename, "attached_to_doctype": "Item", "attached_to_name": code, "is_private": 1,
                "content": base64.b64encode(content).decode("ascii"), "decode": 1}).get("data") or {}
            result = ErpResult.created(f"{filename} attached", {"file_name": filename, "size": len(content)},
                                       str(created.get("name") or ""))
        keep = {result.external_id}
        for f in files:                     # new one first, old ones after: the item is never without its drawing
            if f.get("name") not in keep:
                self._call("DELETE", f"/api/resource/File/{_q(f['name'])}", allow_404=True)
        return result
