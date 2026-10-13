# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for Dolibarr's REST API (`/api/index.php`), used by the connector and Kosha tests.

It implements only what the connector uses, the way Dolibarr answers: numbers as text, extra fields in
`array_options`, errors as {"error": {"code", "message"}}, and (like older Dolibarr versions) 404 instead of
an empty list. List filters (`sqlfilters`) understand `(t.field:op:'value')` and `(ef.field:op:'value')`
joined by `or` / `and`, with the operators = and !=. Lines can only be added to a draft BOM, as in Dolibarr.
"""
from __future__ import annotations

import itertools
import re

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

KEY, LOGIN, VERSION = "test-dolibarr-key", "kosha-sync", "20.0.2"
UNITS = [{"id": "1", "code": "P", "label": "Piece", "short_label": "p", "unit_type": "qty", "active": "1"},
         {"id": "2", "code": "KG", "label": "WeightUnitkg", "short_label": "kg", "unit_type": "weight", "active": "1"},
         {"id": "3", "code": "M", "label": "SizeUnitm", "short_label": "m", "unit_type": "size", "active": "1"},
         {"id": "4", "code": "MM", "label": "SizeUnitmm", "short_label": "mm", "unit_type": "size", "active": "1"}]
WAREHOUSES = {"1": {"id": "1", "ref": "Main store", "lieu": "Hall 1"}, "2": {"id": "2", "ref": "Assembly", "lieu": ""}}
PRODUCT_KEYS = {"ref", "label", "description", "type", "status", "status_buy", "weight", "weight_units", "fk_unit",
                "finished", "array_options", "fk_default_bom", "cost_price", "pmp"}
EXTRA_FIELDS = {"options_kosha_revision", "options_kosha_material"}

STATE: dict = {}
_ids = itertools.count(100)


def reset() -> None:
    STATE.clear()
    STATE.update({"products": {}, "boms": {}, "lines": {}, "stock": {}, "calls": [], "lists_404_when_empty": True})


reset()


class DolibarrError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _error(code: int, message: str) -> JSONResponse:
    reason = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}
    return JSONResponse({"error": {"code": code, "message": f"{reason.get(code, 'Error')}: {message}"},
                         "debug": {"source": "api_fake.class.php", "stages": {"success": ["get"], "failure": ["call"]}}},
                        status_code=code)


def _text(value):
    """Dolibarr returns numbers as text and empty values as null."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.8f}"
    return str(value) if isinstance(value, int) else value


_LEAF = re.compile(r"\((t|ef)\.(\w+):(=|!=):(?:'([^']*)'|([0-9.]+))\)")


def _matches(row: dict, flt: str) -> bool:
    if not flt:
        return True
    parts = re.split(r"\s+(or|and)\s+", flt.strip())
    result, op = None, "or"
    for part in parts:
        if part in ("or", "and"):
            op = part
            continue
        m = _LEAF.fullmatch(part)
        if not m:
            raise DolibarrError(400, f"Error when validating parameter sqlfilters -> {part}")
        alias, field, cmp, sval, nval = m.groups()
        value = sval if sval is not None else nval
        if alias == "ef":
            have = (row.get("array_options") or {}).get(f"options_{field}")
        else:
            have = row.get("id" if field == "rowid" else field)
        have = "" if have is None else str(have)
        hit = (have.lower() == value.lower()) if cmp == "=" else (have.lower() != value.lower() and have != "")
        result = hit if result is None else (result or hit) if op == "or" else (result and hit)
    return bool(result)


def _page(rows: list[dict], request: Request) -> list[dict]:
    q = request.query_params
    flt = q.get("sqlfilters", "")
    rows = [r for r in rows if _matches(r, flt)]
    rows.sort(key=lambda r: int(r["id"]), reverse=q.get("sortorder", "ASC").upper() == "DESC")
    limit = int(q.get("limit") or 100)
    page = int(q.get("page") or 0)
    return rows[page * limit:(page + 1) * limit]


def _product_out(p: dict) -> dict:
    out = {k: _text(v) for k, v in p.items() if k != "array_options"}
    out["array_options"] = dict(p.get("array_options") or {})
    out["id"] = str(p["id"])
    return out


def _bom_out(b: dict) -> dict:
    out = {k: _text(v) for k, v in b.items()}
    out["lines"] = [_line_out(ln) for ln in _lines_of(b["id"])]
    return out


def _line_out(ln: dict) -> dict:
    return {k: _text(v) for k, v in ln.items()}


def _lines_of(bom_id: int) -> list[dict]:
    return sorted((ln for ln in STATE["lines"].values() if ln["fk_bom"] == bom_id), key=lambda ln: (ln["position"], ln["id"]))


def _product_by_ref(ref: str) -> dict | None:
    return next((p for p in STATE["products"].values() if p["ref"].lower() == ref.lower()), None)


def _check_extra(values: dict) -> None:
    for k in (values.get("array_options") or {}):
        if k not in EXTRA_FIELDS:
            raise DolibarrError(400, f"Error updating product: Unknown extrafield {k}")


async def _body(request: Request) -> dict:
    try:
        j = await request.json()
    except ValueError:
        raise DolibarrError(400, "Body is not JSON") from None
    if not isinstance(j, dict):
        raise DolibarrError(400, "Body must be an object")
    return j


async def api(request: Request):
    if request.headers.get("DOLAPIKEY") != KEY:
        return _error(401, "Access denied")
    path = request.path_params["path"].strip("/")
    method = request.method
    STATE["calls"].append((method, path))
    try:
        result = await _route(method, path, request)
    except DolibarrError as e:
        return _error(e.code, e.message)
    if isinstance(result, list) and not result and STATE["lists_404_when_empty"]:
        return _error(404, "No record found")
    return JSONResponse(result)


async def _route(method: str, path: str, request: Request):
    parts = path.split("/")
    if path == "status" and method == "GET":
        return {"success": {"code": 200, "dolibarr_version": VERSION, "access_locked": "0"}}
    if path == "users/info" and method == "GET":
        return {"id": "7", "login": LOGIN, "lastname": "Integration", "firstname": "Kosha", "admin": "0"}
    if path == "setup/dictionary/units" and method == "GET":
        return list(UNITS)
    if parts[0] == "warehouses" and len(parts) == 2 and method == "GET":
        if parts[1] not in WAREHOUSES:
            raise DolibarrError(404, "warehouse not found")
        return dict(WAREHOUSES[parts[1]])
    if parts[0] == "products":
        return await _products(method, parts[1:], request)
    if parts[0] == "boms":
        return await _boms(method, parts[1:], request)
    raise DolibarrError(404, f"API not found (failed to route {method} /{path})")


async def _products(method: str, parts: list[str], request: Request):
    if not parts and method == "GET":
        return [_product_out(p) for p in _page(list(STATE["products"].values()), request)]
    if not parts and method == "POST":
        vals = await _body(request)
        bad = [k for k in vals if k not in PRODUCT_KEYS]
        if bad:
            raise DolibarrError(400, f"Error creating product: unknown field {bad[0]}")
        if not vals.get("ref") or not vals.get("label"):
            raise DolibarrError(400, "label field missing")
        if _product_by_ref(vals["ref"]):
            raise DolibarrError(500, "Error creating product: ErrorProductAlreadyExists")
        if vals.get("fk_unit") and str(vals["fk_unit"]) not in {u["id"] for u in UNITS}:
            raise DolibarrError(500, "Error creating product: unknown unit")
        _check_extra(vals)
        pid = next(_ids)
        STATE["products"][pid] = {"weight": None, "weight_units": None, "fk_unit": None, "pmp": 0.0,
                                  "cost_price": None, "fk_default_bom": None, **vals, "id": pid}
        return pid
    if parts[0] == "ref" and len(parts) == 2 and method == "GET":
        p = _product_by_ref(parts[1])
        if not p:
            raise DolibarrError(404, "Product not found")
        return _product_out(p)
    try:
        pid = int(parts[0])
    except ValueError:
        raise DolibarrError(404, "Product not found") from None
    p = STATE["products"].get(pid)
    if not p:
        raise DolibarrError(404, "Product not found")
    if len(parts) == 1 and method == "GET":
        return _product_out(p)
    if len(parts) == 1 and method == "PUT":
        vals = await _body(request)
        bad = [k for k in vals if k not in PRODUCT_KEYS]
        if bad:
            raise DolibarrError(400, f"Error updating product: unknown field {bad[0]}")
        _check_extra(vals)
        if "array_options" in vals:          # like older Dolibarr versions: the whole set of extra fields is replaced
            p["array_options"] = dict(vals.pop("array_options") or {})
        if vals.get("fk_default_bom") and int(vals["fk_default_bom"]) not in STATE["boms"]:
            raise DolibarrError(500, "Error updating product: unknown BOM")
        p.update(vals)
        return _product_out(p)
    if len(parts) == 2 and parts[1] == "stock" and method == "GET":
        stock = {wid: {"real": _text(float(q))} for (sp, wid), q in STATE["stock"].items() if sp == pid}
        if not stock:
            raise DolibarrError(404, "No stock found")
        return {"stock_warehouses": stock}
    raise DolibarrError(404, "API not found")


async def _boms(method: str, parts: list[str], request: Request):
    if not parts and method == "GET":
        return [_bom_out(b) for b in _page(list(STATE["boms"].values()), request)]
    if not parts and method == "POST":
        vals = await _body(request)
        pid = int(vals.get("fk_product") or 0)
        if pid not in STATE["products"]:
            raise DolibarrError(500, "Error creating BOM: ErrorFieldRequired Product")
        if any(b["ref"].lower() == str(vals.get("ref", "")).lower() for b in STATE["boms"].values()):
            raise DolibarrError(500, "Error creating BOM: ErrorRefAlreadyExists")
        bid = next(_ids)
        STATE["boms"][bid] = {"bomtype": 0, "qty": 1.0, "efficiency": 1.0, "status": 0, "label": "", **vals,
                              "fk_product": pid, "id": bid}
        return bid
    try:
        bid = int(parts[0])
    except ValueError:
        raise DolibarrError(404, "BOM not found") from None
    b = STATE["boms"].get(bid)
    if not b:
        raise DolibarrError(404, "BOM not found")
    if len(parts) == 1 and method == "GET":
        return _bom_out(b)
    if len(parts) == 1 and method == "PUT":
        vals = await _body(request)
        b.update({k: v for k, v in vals.items() if k not in ("id", "lines")})
        return _bom_out(b)
    if len(parts) == 2 and parts[1] == "lines" and method == "GET":
        return [_line_out(ln) for ln in _lines_of(bid)]
    if len(parts) == 2 and parts[1] == "lines" and method == "POST":
        if int(b.get("status") or 0) != 0:
            raise DolibarrError(500, "Error adding line: the BOM is not a draft")
        vals = await _body(request)
        if int(vals.get("fk_product") or 0) not in STATE["products"]:
            raise DolibarrError(500, "Error adding line: ErrorFieldRequired Product")
        lid = next(_ids)
        STATE["lines"][lid] = {"position": 0, "qty_frozen": 0, "disable_stock_change": 0, "efficiency": 1.0,
                               "fk_unit": None, **vals, "id": lid, "fk_bom": bid}
        return lid
    if len(parts) == 3 and parts[1] == "lines" and method == "DELETE":
        lid = int(parts[2])
        if int(b.get("status") or 0) != 0:
            raise DolibarrError(500, "Error deleting line: the BOM is not a draft")
        if STATE["lines"].get(lid, {}).get("fk_bom") != bid:
            raise DolibarrError(404, "Line not found")
        del STATE["lines"][lid]
        return {"success": {"code": 200, "message": "line deleted"}}
    raise DolibarrError(404, "API not found")


def add_stock(code: str, qty: float, warehouse: str = "1") -> None:
    p = _product_by_ref(code)
    key = (p["id"], warehouse)
    STATE["stock"][key] = STATE["stock"].get(key, 0.0) + qty


def product(code: str) -> dict | None:
    return _product_by_ref(code)


def boms_of(code: str) -> list[dict]:
    p = _product_by_ref(code)
    return sorted((b for b in STATE["boms"].values() if p and b["fk_product"] == p["id"]), key=lambda b: b["id"])


def lines_of(bom_id: int) -> list[tuple[str, float]]:
    return [(STATE["products"][ln["fk_product"]]["ref"], float(ln["qty"])) for ln in _lines_of(bom_id)]


app = Starlette(routes=[Route("/api/index.php/{path:path}", api, methods=["GET", "POST", "PUT", "DELETE"])])
