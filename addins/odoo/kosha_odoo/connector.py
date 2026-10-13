# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Odoo connector, through Odoo's external JSON-RPC API (`/jsonrpc`).

    Item  -> product.product (internal reference `default_code` = part number): create, or write only what changed
    BOM   -> mrp.bom + mrp.bom.line: a new BOM, put first, when the lines differ from the BOM Odoo uses now
    Stock <- stock.quant in internal locations; cost <- the product's standard_price
    Reconciliation <- product.product, mrp.bom and mrp.bom.line, read only

Authentication: `common.authenticate` with the user's login and an API key (Odoo › Preferences ›
Account Security › New API Key), then `object.execute_kw` with the same key. Every method is
idempotent, as the ErpConnector contract requires: Kosha looks before it creates, and a call that
creates something is never repeated automatically.
"""
from __future__ import annotations

import itertools
import json
import re

from kosha_sdk import AddinContext, Health
from kosha_sdk.erp import (ErpBom, ErpBomLine, ErpBomRecord, ErpConnector, ErpError, ErpItem, ErpItemRecord,
                           ErpNotSupported, ErpResult, StockInfo)
from kosha_sdk.http import HttpError

PRODUCT_SETUP = {
    "Odoo 15 to 17": {"detailed_type": "product"},
    "Odoo 18 or later": {"type": "consu", "is_storable": True},
    "Consumable (no stock)": {"type": "consu"},
}
_REV_REF = re.compile(r"^Rev\s+(.+)$", re.IGNORECASE)


def odoo_message(error: dict) -> str:
    """The most useful human message inside an Odoo JSON-RPC error."""
    data = error.get("data") if isinstance(error, dict) else None
    data = data if isinstance(data, dict) else {}
    name = str(data.get("name") or "")
    if name.endswith("AccessDenied") or name.endswith("SessionExpiredException"):
        return "Odoo refused the user name or API key."
    msg = data.get("message") or (error.get("message") if isinstance(error, dict) else "") or "Odoo refused the request."
    return " ".join(str(msg).split())[:500]


def _m2o_name(value) -> str:
    """Odoo returns a link to another record as [id, "name"], or False when it is empty."""
    return str(value[1]) if isinstance(value, (list, tuple)) and len(value) > 1 else ""


def _m2o_id(value) -> int | None:
    return int(value[0]) if isinstance(value, (list, tuple)) and value else None


def _text(value) -> str:
    """Odoo uses False for an empty text field."""
    return "" if value in (None, False) else str(value)


class OdooConnector(ErpConnector):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self.cfg = s
        self._base = (s.get("base_url") or "").rstrip("/")
        self._db = s.get("database") or ""
        self._login = s.get("username") or ""
        self._key = s.get("api_key") or ""
        self._company = int(s["company_id"]) if str(s.get("company_id") or "").strip().isdigit() else None
        timeout = float(s.get("timeout_s") or 30)
        verify = bool(s.get("verify_tls", True))
        # reads may be retried after a network hiccup; calls that create records are sent once only
        self._http = ctx.http(self._base, timeout_s=timeout, verify_tls=verify, retries=2) if self._base else None
        self._http_once = ctx.http(self._base, timeout_s=timeout, verify_tls=verify, retries=0) if self._base else None
        self._ids = itertools.count(1)
        self._uid: int | None = None
        self._uoms: dict[str, int | None] = {}

    # ------------------------------------------------------------ plumbing
    def _rpc(self, service: str, method: str, *args, once: bool = False):
        http = self._http_once if once else self._http
        if http is None:
            raise ErpError("The Odoo address is not set (ERP settings).")
        body = {"jsonrpc": "2.0", "method": "call", "id": next(self._ids),
                "params": {"service": service, "method": method, "args": list(args)}}
        try:
            r = http.post("/jsonrpc", body)
        except HttpError as e:
            if e.status is None:
                raise ErpError(f"Cannot reach Odoo at {self._base}. {e}") from None
            raise ErpError(f"Odoo answered HTTP {e.status}. Check the address (it should be the start page of Odoo).",
                           status=e.status, detail=e.detail[:4000]) from None
        try:
            j = r.json()
        except ValueError:
            raise ErpError("Odoo's answer was not JSON. Check the address (it should be the start page of Odoo).",
                           detail=r.text[:4000]) from None
        if not isinstance(j, dict):
            raise ErpError("Odoo's answer could not be read.")
        if j.get("error"):
            raise ErpError(odoo_message(j["error"]), detail=json.dumps(j["error"])[:4000])
        return j.get("result")

    def _user(self) -> int:
        if self._uid is None:
            if self._http is None:
                raise ErpError("The Odoo address is not set (ERP settings).")
            if not (self._db and self._login and self._key):
                raise ErpError("Fill in the Odoo database, user and API key (ERP settings).")
            uid = self._rpc("common", "authenticate", self._db, self._login, self._key, {})
            if not uid:
                raise ErpError(f"Odoo refused the user {self._login} or the API key for database {self._db}.", status=401)
            self._uid = int(uid)
        return self._uid

    def _kw(self, model: str, method: str, args: list, kw: dict | None = None, once: bool = False):
        kw = dict(kw or {})
        ctx = dict(kw.pop("context", None) or {})
        if self._company:
            ctx.setdefault("allowed_company_ids", [self._company])
        if ctx:
            kw["context"] = ctx
        return self._rpc("object", "execute_kw", self._db, self._user(), self._key, model, method, args, kw, once=once)

    def _custom(self) -> tuple[str, str]:
        return (self.cfg.get("revision_field") or "").strip(), (self.cfg.get("material_field") or "").strip()

    def _product_fields(self) -> list[str]:
        fields = ["id", "default_code", "name", "uom_id", "active", "product_tmpl_id", "weight", "standard_price"]
        return fields + [f for f in self._custom() if f]

    def _products(self, codes, active_only: bool = False) -> dict[str, dict]:
        """{CODE: product row}, archived products included so they are never created twice."""
        wanted = [c for c in dict.fromkeys(codes) if c]
        out: dict[str, dict] = {}
        for start in range(0, len(wanted), 100):
            chunk = wanted[start:start + 100]
            rows = self._kw("product.product", "search_read", [[["default_code", "in", chunk]]],
                            {"fields": self._product_fields(), "context": {"active_test": active_only}}) or []
            for r in rows:
                code = _text(r.get("default_code")).upper()
                if code and (code not in out or (r.get("active") and not out[code].get("active"))):
                    out[code] = r
        return out

    def _uom_id(self, name: str) -> int | None:
        name = (name or "").strip()
        if not name:
            return None
        if name not in self._uoms:
            rows = self._kw("uom.uom", "search_read", [[["name", "=ilike", name]]], {"fields": ["id", "name"], "limit": 1})
            self._uoms[name] = int(rows[0]["id"]) if rows else None
        return self._uoms[name]

    def _boms(self, tmpl_id: int) -> list[dict]:
        """Active BOMs of a product, the one Odoo uses first."""
        return self._kw("mrp.bom", "search_read", [[["product_tmpl_id", "=", tmpl_id], ["active", "=", True]]],
                        {"fields": ["id", "code", "sequence", "product_tmpl_id"], "order": "sequence, id"}) or []

    def _bom_lines(self, bom_id: int) -> list[tuple[str, float, str]]:
        rows = self._kw("mrp.bom.line", "search_read", [[["bom_id", "=", bom_id]]],
                        {"fields": ["product_id", "product_qty", "product_uom_id"]}) or []
        ids = sorted({_m2o_id(r.get("product_id")) for r in rows if _m2o_id(r.get("product_id"))})
        codes = {}
        if ids:
            for p in self._kw("product.product", "read", [ids], {"fields": ["default_code"],
                                                                  "context": {"active_test": False}}) or []:
                codes[int(p["id"])] = _text(p.get("default_code"))
        return [(codes.get(_m2o_id(r.get("product_id")), "") or _m2o_name(r.get("product_id")),
                 float(r.get("product_qty") or 0), _m2o_name(r.get("product_uom_id"))) for r in rows]

    # ------------------------------------------------------------ contract
    def health(self) -> Health:
        try:
            version = self._rpc("common", "version") or {}
            uid = self._user()
        except ErpError as e:
            return Health.down(str(e))
        ver = version.get("server_version", "") if isinstance(version, dict) else ""
        return Health.up(f"Connected to Odoo {ver} as {self._login} (user {uid}) on database {self._db}.".replace("  ", " "))

    def upsert_item(self, item: ErpItem) -> ErpResult:
        rev_field, mat_field = self._custom()
        values: dict = {"name": item.description or item.code}
        if rev_field:
            values[rev_field] = item.revision
        if mat_field and item.material:
            values[mat_field] = item.material
        if item.weight_kg:
            values["weight"] = float(item.weight_kg)
        existing = self._products([item.code]).get(item.code.upper())
        if existing:
            changed = {k: v for k, v in values.items() if (existing.get(k) if existing.get(k) is not False else "") != v}
            if not changed:
                return ErpResult.unchanged("unchanged", _text(existing.get("default_code")))
            self._kw("product.product", "write", [[existing["id"]], changed])
            return ErpResult.updated("updated", changed, _text(existing.get("default_code")))
        uom_id = self._uom_id(item.uom)
        if item.uom and uom_id is None:
            raise ErpError(f"Odoo has no unit of measure called {item.uom!r}. Add it in Odoo, or map Kosha's unit to an "
                           "Odoo unit under ERP sync › Connection settings › Unit mapping.")
        payload = {"default_code": item.code, **values, "sale_ok": True, "purchase_ok": True,
                   **PRODUCT_SETUP.get(self.cfg.get("product_setup") or "", PRODUCT_SETUP["Odoo 15 to 17"])}
        if uom_id:
            payload["uom_id"] = payload["uom_po_id"] = uom_id
        new_id = self._kw("product.product", "create", [payload], once=True)
        return ErpResult.created(f"created (product {new_id})", payload, item.code)

    def upsert_bom(self, bom: ErpBom) -> ErpResult:
        parent = self._products([bom.item.code]).get(bom.item.code.upper())
        if not parent:
            raise ErpError(f"{bom.item.code} is not in Odoo yet, so its BOM cannot be added.")
        tmpl_id = _m2o_id(parent.get("product_tmpl_id"))
        current = self._boms(tmpl_id)
        wanted = sorted((ln.code.upper(), round(float(ln.qty), 6)) for ln in bom.lines)
        if current:
            have = sorted((code.upper(), round(qty, 6)) for code, qty, _ in self._bom_lines(current[0]["id"]))
            if have == wanted:
                ref = _text(current[0].get("code")) or f"BOM {current[0]['id']}"
                return ErpResult.unchanged(f"unchanged ({ref})", str(current[0]["id"]))
        children = self._products([ln.code for ln in bom.lines])
        missing = [ln.code for ln in bom.lines if ln.code.upper() not in children]
        if missing:
            raise ErpError(f"These parts are not in Odoo yet: {', '.join(missing[:10])}.")
        lines = []
        for ln in bom.lines:
            vals = {"product_id": children[ln.code.upper()]["id"], "product_qty": float(ln.qty)}
            uom_id = self._uom_id(ln.uom)
            if uom_id:
                vals["product_uom_id"] = uom_id
            lines.append([0, 0, vals])
        payload = {"product_tmpl_id": tmpl_id, "product_qty": 1, "type": "normal", "code": f"Rev {bom.revision}",
                   "sequence": min([int(b.get("sequence") or 0) for b in current] + [11]) - 1, "bom_line_ids": lines}
        if self._company:
            payload["company_id"] = self._company
        new_id = self._kw("mrp.bom", "create", [payload], once=True)
        return ErpResult.created(f"created BOM {new_id} (Rev {bom.revision})", payload, str(new_id))

    def stock_and_cost(self, code: str) -> StockInfo:
        p = self._products([code]).get(code.upper())
        if not p:
            return StockInfo(exists=False)
        quants = self._kw("stock.quant", "search_read",
                          [[["product_id", "=", p["id"]], ["location_id.usage", "=", "internal"]]],
                          {"fields": ["location_id", "quantity"]}) or []
        cost = p.get("standard_price")
        return StockInfo(exists=True, stock=sum(float(q.get("quantity") or 0) for q in quants),
                         cost=float(cost) if cost not in (None, False) else None,
                         warehouses=tuple({"warehouse": _m2o_name(q.get("location_id")),
                                           "actual_qty": float(q.get("quantity") or 0)} for q in quants))

    # ------------------------------------------------- reading back (reconciliation)
    def fetch_items(self, codes) -> dict[str, ErpItemRecord]:
        rev_field, mat_field = self._custom()
        out = {}
        for p in self._products(codes).values():
            code = _text(p.get("default_code"))
            out[code] = ErpItemRecord(
                code=code, description=_text(p.get("name")), uom=_m2o_name(p.get("uom_id")),
                revision=_text(p.get(rev_field)) if rev_field else None,
                material=_text(p.get(mat_field)) if mat_field else None,
                active=bool(p.get("active")), from_kosha=bool(p.get(rev_field)) if rev_field else None)
        return out

    def fetch_bom(self, code: str) -> ErpBomRecord | None:
        p = self._products([code]).get(code.upper())
        if not p:
            return None
        current = self._boms(_m2o_id(p.get("product_tmpl_id")))
        if not current:
            return None
        b = current[0]
        ref = _text(b.get("code"))
        m = _REV_REF.match(ref)
        lines = tuple(ErpBomLine(c, q, u) for c, q, u in self._bom_lines(b["id"]))
        return ErpBomRecord(code=_text(p.get("default_code")), lines=lines, revision=m.group(1).strip() if m else None,
                            reference=ref or f"BOM {b['id']}")

    def list_items_from_kosha(self) -> list[str]:
        rev_field, _ = self._custom()
        if not rev_field:
            raise ErpNotSupported("Set the revision custom field in the Odoo connection settings, so Kosha can "
                                  "recognise the products it created in Odoo.")
        out: list[str] = []
        offset, page = 0, 500
        while True:
            rows = self._kw("product.product", "search_read", [[[rev_field, "!=", False]]],
                            {"fields": ["default_code"], "offset": offset, "limit": page, "order": "id"}) or []
            out += [_text(r.get("default_code")) for r in rows if _text(r.get("default_code"))]
            if len(rows) < page:
                return out
            offset += page
