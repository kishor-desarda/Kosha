# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for Odoo's external JSON-RPC API, used by the connector and Kosha tests.

It implements only what the connector uses: common.version / authenticate and object.execute_kw
with search_read, read, create and write on product.product, uom.uom, mrp.bom, mrp.bom.line and
stock.quant. Domains are lists of [field, operator, value] joined with AND.
"""
from __future__ import annotations

import itertools

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

DB, LOGIN, KEY, UID = "kosha-test", "kosha@example.com", "test-api-key", 2
UOMS = {1: "Units", 2: "kg", 3: "m", 4: "mm"}
PRODUCT_KEYS = {"default_code", "name", "uom_id", "uom_po_id", "active", "weight", "standard_price", "sale_ok",
                "purchase_ok", "type", "detailed_type", "is_storable", "categ_id"}
M2O = {"product.product": {"uom_id": "uom.uom", "uom_po_id": "uom.uom", "product_tmpl_id": "product.template"},
       "mrp.bom": {"product_tmpl_id": "product.template", "company_id": "res.company"},
       "mrp.bom.line": {"product_id": "product.product", "product_uom_id": "uom.uom", "bom_id": "mrp.bom"},
       "stock.quant": {"product_id": "product.product", "location_id": "stock.location"}}

STATE: dict = {}
_ids = itertools.count(100)


def reset() -> None:
    STATE.clear()
    STATE.update({"product.product": {}, "mrp.bom": {}, "mrp.bom.line": {}, "stock.quant": {},
                  "uom.uom": {i: {"id": i, "name": n} for i, n in UOMS.items()}, "calls": []})


reset()


class OdooError(Exception):
    def __init__(self, name: str, message: str):
        super().__init__(message)
        self.name, self.message = name, message


def _name_of(model: str, rid) -> str:
    if model == "uom.uom":
        return UOMS.get(rid, "?")
    if model in ("product.product", "product.template"):
        p = STATE["product.product"].get(rid, {})
        return f"[{p.get('default_code')}] {p.get('name')}" if p.get("default_code") else str(p.get("name", ""))
    if model == "stock.location":
        return {8: "WH/Stock", 9: "WH/Stock/Shelf 1"}.get(rid, f"Location {rid}")
    if model == "mrp.bom":
        return str(STATE["mrp.bom"].get(rid, {}).get("code") or f"BOM {rid}")
    return f"{model} {rid}"


def _value(model: str, row: dict, field: str):
    """A field as Odoo returns it: links as [id, name], empty values as False."""
    v = row.get(field, False)
    target = M2O.get(model, {}).get(field)
    if target and v:
        return [v, _name_of(target, v)]
    if v is None or v == "":
        return False
    return v


def _matches(model: str, row: dict, domain: list) -> bool:
    for field, op, value in domain:
        have = row.get(field, False)
        if op == "=" and not (have == value or (value is False and not have)):
            return False
        if op == "!=" and (have == value or (value is False and not have)):
            return False
        if op == "in" and have not in value:
            return False
        if op == "=ilike" and str(have).lower() != str(value).lower():
            return False
        if op not in ("=", "!=", "in", "=ilike"):
            raise OdooError("builtins.ValueError", f"Invalid leaf {[field, op, value]}")
    return True


def _search(model: str, domain: list, kw: dict) -> list[dict]:
    rows = list(STATE[model].values())
    if "active" in (rows[0] if rows else {}) and kw.get("context", {}).get("active_test", True) \
            and not any(d[0] == "active" for d in domain):
        rows = [r for r in rows if r.get("active", True)]
    rows = [r for r in rows if _matches(model, r, domain)]
    if kw.get("order", "").startswith("sequence"):
        rows.sort(key=lambda r: (r.get("sequence", 0), r["id"]))
    else:
        rows.sort(key=lambda r: r["id"])
    off = int(kw.get("offset") or 0)
    lim = kw.get("limit")
    return rows[off: off + int(lim)] if lim else rows[off:]


def _out(model: str, rows: list[dict], fields: list[str] | None) -> list[dict]:
    out = []
    for r in rows:
        keys = fields or list(r)
        out.append({"id": r["id"], **{f: _value(model, r, f) for f in keys if f != "id"}})
    return out


def _create(model: str, vals: dict) -> int:
    rid = next(_ids)
    if model == "product.product":
        if not vals.get("name"):
            raise OdooError("odoo.exceptions.ValidationError", "The product needs a name.")
        bad = [k for k in vals if k not in PRODUCT_KEYS and not k.startswith("x_")]
        if bad:
            raise OdooError("builtins.ValueError", f"Invalid field '{bad[0]}' on model 'product.product'")
        if vals.get("uom_id") and vals["uom_id"] not in UOMS:
            raise OdooError("odoo.exceptions.ValidationError", "Unknown unit of measure.")
        row = {"active": True, "uom_id": 1, "weight": 0.0, "standard_price": 0.0, **vals, "id": rid, "product_tmpl_id": rid}
    elif model == "mrp.bom":
        lines = vals.pop("bom_line_ids", [])
        tmpl = vals.get("product_tmpl_id")
        if tmpl not in STATE["product.product"]:
            raise OdooError("odoo.exceptions.ValidationError", "Missing required value for the field 'Product'.")
        row = {"active": True, "sequence": 0, "code": False, **vals, "id": rid}
        for cmd in lines:
            if cmd[0] != 0:
                raise OdooError("builtins.ValueError", "only create commands are supported here")
            lv = dict(cmd[2])
            if lv.get("product_id") not in STATE["product.product"]:
                raise OdooError("odoo.exceptions.ValidationError", "A BOM line has no product.")
            lid = next(_ids)
            STATE["mrp.bom.line"][lid] = {"product_uom_id": STATE["product.product"][lv["product_id"]]["uom_id"],
                                          **lv, "id": lid, "bom_id": rid}
    else:
        row = {**vals, "id": rid}
    STATE[model][rid] = row
    return rid


def _execute(model: str, method: str, args: list, kw: dict):
    if model not in STATE or model == "calls":
        raise OdooError("builtins.KeyError", f"Object {model} doesn't exist")
    STATE["calls"].append((model, method))
    if method == "search_read":
        return _out(model, _search(model, args[0] if args else [], kw), kw.get("fields"))
    if method == "read":
        ids = args[0]
        rows = [STATE[model][i] for i in ids if i in STATE[model]]
        return _out(model, rows, kw.get("fields") or (args[1] if len(args) > 1 else None))
    if method == "create":
        vals = args[0]
        return [_create(model, v) for v in vals] if isinstance(vals, list) else _create(model, dict(vals))
    if method == "write":
        ids, vals = args
        for i in ids:
            if model == "product.product":
                bad = [k for k in vals if k not in PRODUCT_KEYS and not k.startswith("x_")]
                if bad:
                    raise OdooError("builtins.ValueError", f"Invalid field '{bad[0]}' on model 'product.product'")
            STATE[model][i].update(vals)
        return True
    raise OdooError("builtins.AttributeError", f"The method '{method}' does not exist on the model '{model}'")


async def jsonrpc(request: Request):
    j = await request.json()
    rid = j.get("id")
    params = j.get("params") or {}
    service, method, args = params.get("service"), params.get("method"), params.get("args") or []
    try:
        if service == "common" and method == "version":
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {"server_version": "17.0", "protocol_version": 1}})
        if service == "common" and method == "authenticate":
            db, login, key = args[0], args[1], args[2]
            ok = db == DB and login == LOGIN and key == KEY
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": UID if ok else False})
        if service == "object" and method == "execute_kw":
            db, uid, key, model, meth = args[:5]
            if db != DB or uid != UID or key != KEY:
                raise OdooError("odoo.exceptions.AccessDenied", "Access Denied")
            margs = args[5] if len(args) > 5 else []
            mkw = args[6] if len(args) > 6 else {}
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": _execute(model, meth, margs, mkw)})
        raise OdooError("builtins.NotImplementedError", f"{service}.{method} is not in this fake")
    except OdooError as e:
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {
            "code": 200, "message": "Odoo Server Error",
            "data": {"name": e.name, "message": e.message, "debug": "Traceback (most recent call last): ..."}}})


def add_stock(code: str, qty: float, location: int = 8) -> None:
    p = next(r for r in STATE["product.product"].values() if r.get("default_code") == code)
    qid = next(_ids)
    STATE["stock.quant"][qid] = {"id": qid, "product_id": p["id"], "location_id": location, "quantity": qty,
                                 "location_id.usage": "internal" if location in (8, 9) else "customer"}


app = Starlette(routes=[Route("/jsonrpc", jsonrpc, methods=["POST"])])
