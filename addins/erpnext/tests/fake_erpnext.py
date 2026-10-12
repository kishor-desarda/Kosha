# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for the ERPNext REST API, used by the Kosha and connector tests."""
from __future__ import annotations

import json
import re

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

KEY, SECRET = "testkey", "testsecret"
STATE = {"items": {}, "boms": {}, "bins": {}, "calls": []}


def _auth(request: Request):
    if request.headers.get("authorization") != f"token {KEY}:{SECRET}":
        return JSONResponse({"exc_type": "AuthenticationError", "message": "Not permitted"}, status_code=401)
    return None


def _err(msg, status=417):
    return JSONResponse({"exc_type": "ValidationError",
                         "_server_messages": json.dumps([json.dumps({"message": msg})])}, status_code=status)


async def whoami(request):
    return _auth(request) or JSONResponse({"message": "kosha@example.com"})


async def item(request: Request):
    if (r := _auth(request)):
        return r
    code = request.path_params["code"]
    STATE["calls"].append((request.method, "Item", code))
    if request.method == "GET":
        if code not in STATE["items"]:
            return JSONResponse({"exc_type": "DoesNotExistError"}, status_code=404)
        return JSONResponse({"data": STATE["items"][code]})
    body = await request.json()
    if code not in STATE["items"]:
        return JSONResponse({"exc_type": "DoesNotExistError"}, status_code=404)
    if "stock_uom" in body:
        return _err("Default Unit of Measure can not be changed directly")
    STATE["items"][code].update(body)
    return JSONResponse({"data": STATE["items"][code]})


async def items(request: Request):
    if (r := _auth(request)):
        return r
    body = await request.json()
    STATE["calls"].append(("POST", "Item", body.get("item_code")))
    for k in ("item_code", "item_group", "stock_uom"):
        if not body.get(k):
            return _err(f"{k} is mandatory")
    if body["stock_uom"] not in ("Nos", "Kg", "Meter", "Millimeter"):
        return _err(f"UOM {body['stock_uom']} not found")
    body.setdefault("valuation_rate", 0)
    STATE["items"][body["item_code"]] = body
    return JSONResponse({"data": body})


async def boms(request: Request):
    if (r := _auth(request)):
        return r
    if request.method == "GET":
        flt = json.loads(request.query_params.get("filters", "[]"))
        code = next((f[2] for f in flt if f[0] == "item"), None)
        data = [{"name": b["name"], "is_default": b["is_default"], "docstatus": b["docstatus"]}
                for b in STATE["boms"].values() if b["item"] == code and b["docstatus"] < 2]
        return JSONResponse({"data": data})
    body = await request.json()
    STATE["calls"].append(("POST", "BOM", body.get("item")))
    if body["item"] not in STATE["items"]:
        return _err(f"Item {body['item']} not found")
    for row in body.get("items", []):
        if row["item_code"] not in STATE["items"]:
            return _err(f"Row: Item {row['item_code']} not found")
    n = sum(1 for b in STATE["boms"].values() if b["item"] == body["item"]) + 1
    name = f"BOM-{body['item']}-{n:03d}"
    for b in STATE["boms"].values():
        if b["item"] == body["item"]:
            b["is_default"] = 0
    body.update({"name": name, "docstatus": 0})
    STATE["boms"][name] = body
    return JSONResponse({"data": body})


async def bom(request: Request):
    if (r := _auth(request)):
        return r
    name = request.path_params["name"]
    if name not in STATE["boms"]:
        return JSONResponse({"exc_type": "DoesNotExistError"}, status_code=404)
    if request.method == "PUT":
        body = await request.json()
        STATE["calls"].append(("PUT", "BOM", name))
        STATE["boms"][name].update(body)
    return JSONResponse({"data": STATE["boms"][name]})


async def bins(request: Request):
    if (r := _auth(request)):
        return r
    flt = json.loads(request.query_params.get("filters", "[]"))
    code = next((f[2] for f in flt if f[0] == "item_code"), None)
    return JSONResponse({"data": STATE["bins"].get(code, [])})


app = Starlette(routes=[
    Route("/api/method/frappe.auth.get_logged_user", whoami),
    Route("/api/resource/Item", items, methods=["POST"]),
    Route("/api/resource/Item/{code:path}", item, methods=["GET", "PUT"]),
    Route("/api/resource/BOM", boms, methods=["GET", "POST"]),
    Route("/api/resource/BOM/{name:path}", bom, methods=["GET", "PUT"]),
    Route("/api/resource/Bin", bins, methods=["GET"]),
])
del re
