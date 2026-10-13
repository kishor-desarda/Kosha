# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for the ERPNext REST API, used by the Kosha and connector tests."""
from __future__ import annotations

import base64
import json
import re

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

KEY, SECRET = "testkey", "testsecret"
STATE = {"items": {}, "boms": {}, "bins": {}, "files": {}, "calls": []}


def _auth(request: Request):
    if request.headers.get("authorization") != f"token {KEY}:{SECRET}":
        return JSONResponse({"exc_type": "AuthenticationError", "message": "Not permitted"}, status_code=401)
    return None


def _matches(row: dict, filters: list) -> bool:
    """Frappe filters as lists: [field, op, value] or [doctype, field, op, value]."""
    for f in filters:
        field, op, value = f[-3], f[-2], f[-1]
        have = row.get(field)
        if op == "=" and have != value:
            return False
        if op == "!=" and have == value:
            return False
        if op == "in" and have not in value:
            return False
        if op == "<" and not (have or 0) < value:
            return False
        if op == "is" and (value == "set") != bool(have):
            return False
        if op == "like" and not re.fullmatch(re.escape(value).replace("%", ".*"), str(have or "")):
            return False
    return True


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
    if request.method == "GET":
        flt = json.loads(request.query_params.get("filters", "[]"))
        fields = json.loads(request.query_params.get("fields", '["name"]'))
        start = int(request.query_params.get("limit_start", 0))
        size = int(request.query_params.get("limit_page_length", 20))
        STATE["calls"].append(("GET", "Item", "list"))
        rows = [it for it in STATE["items"].values() if _matches(it, flt)][start:start + size]
        return JSONResponse({"data": [{k: it.get(k) for k in fields} for it in rows]})
    body = await request.json()
    STATE["calls"].append(("POST", "Item", body.get("item_code")))
    for k in ("item_code", "item_group", "stock_uom"):
        if not body.get(k):
            return _err(f"{k} is mandatory")
    if body["stock_uom"] not in ("Nos", "Kg", "Meter", "Millimeter"):
        return _err(f"UOM {body['stock_uom']} not found")
    body.setdefault("valuation_rate", 0)
    body.setdefault("disabled", 0)
    STATE["items"][body["item_code"]] = body
    return JSONResponse({"data": body})


async def boms(request: Request):
    if (r := _auth(request)):
        return r
    if request.method == "GET":
        flt = json.loads(request.query_params.get("filters", "[]"))
        code = next((f[2] for f in flt if f[0] == "item"), None)
        data = [{"name": b["name"], "is_default": b["is_default"], "docstatus": b["docstatus"]}
                for b in STATE["boms"].values() if b["item"] == code and b["docstatus"] < 2
                and b.get("is_active", 1)]
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


async def files(request: Request):
    if (r := _auth(request)):
        return r
    if request.method == "GET":
        flt = json.loads(request.query_params.get("filters", "[]"))
        fields = json.loads(request.query_params.get("fields", '["name"]'))
        rows = [f for f in STATE["files"].values() if _matches(f, flt)]
        return JSONResponse({"data": [{k: f.get(k) for k in fields} for f in rows]})
    body = await request.json()
    STATE["calls"].append(("POST", "File", body.get("file_name")))
    if body.get("attached_to_name") not in STATE["items"]:
        return _err(f"Item {body.get('attached_to_name')} not found")
    content = base64.b64decode(body["content"]) if body.get("decode") else body["content"].encode()
    name = f"FILE-{len(STATE['files']) + 1:04d}"
    STATE["files"][name] = {"name": name, "file_name": body["file_name"], "file_size": len(content), "content": content,
                            "attached_to_doctype": body.get("attached_to_doctype"),
                            "attached_to_name": body.get("attached_to_name"), "is_private": body.get("is_private", 0)}
    return JSONResponse({"data": {k: v for k, v in STATE["files"][name].items() if k != "content"}})


async def file(request: Request):
    if (r := _auth(request)):
        return r
    name = request.path_params["name"]
    STATE["calls"].append(("DELETE", "File", name))
    if STATE["files"].pop(name, None) is None:
        return JSONResponse({"exc_type": "DoesNotExistError"}, status_code=404)
    return JSONResponse({"message": "ok"})


app = Starlette(routes=[
    Route("/api/method/frappe.auth.get_logged_user", whoami),
    Route("/api/resource/Item", items, methods=["GET", "POST"]),
    Route("/api/resource/Item/{code:path}", item, methods=["GET", "PUT"]),
    Route("/api/resource/BOM", boms, methods=["GET", "POST"]),
    Route("/api/resource/BOM/{name:path}", bom, methods=["GET", "PUT"]),
    Route("/api/resource/Bin", bins, methods=["GET"]),
    Route("/api/resource/File", files, methods=["GET", "POST"]),
    Route("/api/resource/File/{name:path}", file, methods=["DELETE"]),
])
