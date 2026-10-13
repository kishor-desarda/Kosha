# SPDX-License-Identifier: AGPL-3.0-or-later
"""The API description, built from the routes the server has registered (``web.api``), never written by hand.

``openapi(routes)`` returns an OpenAPI 3.1 document: method, path, path parameters, the role a caller needs and
the handler's docstring. ``html(spec)`` turns it into a self-contained page (no scripts, no outside files).
"""
from __future__ import annotations

import html as _html
import inspect
import re

from . import __version__

AREAS = {
    "auth": "Sign-in and sessions", "users": "Users", "settings": "Settings", "admin": "Administration",
    "audit": "Audit log", "numbering": "Part numbers", "search": "Search", "dashboard": "Home screen",
    "folders": "Folders", "documents": "Files and versions", "items": "Items (part master)",
    "boms": "Bills of materials", "changes": "Change orders", "erp": "ERP sync", "common-parts": "Duplicate finder",
    "import": "Legacy import", "addins": "Add-ins (admin)", "addin": "CAD add-ins", "meta": "Reference data",
    "health": "Server status", "openapi.json": "API description", "docs": "API description",
    "ai": "AI assistance (optional)",
}
ROLE_TEXT = {None: "public, no sign-in", "viewer": "any signed-in user", "engineer": "engineer or higher",
             "approver": "approver or higher", "admin": "admin"}
_PARAM = re.compile(r"{([A-Za-z_][A-Za-z0-9_]*)(?::([a-z]+))?}")
_TYPES = {"int": "integer", "float": "number", "str": "string", "path": "string", "uuid": "string"}


def _area(path: str) -> str:
    parts = path.split("/")
    return parts[2] if len(parts) > 2 and parts[1] == "api" else "other"


def _doc(fn) -> tuple[str, str]:
    doc = inspect.getdoc(fn) or ""
    if not doc:
        name = getattr(fn, "__name__", "").replace("_", " ").strip()
        return (name[:1].upper() + name[1:]) if name else "", ""
    first, _, rest = doc.partition("\n")
    return first.strip(), rest.strip()


def _uses_files(fn) -> bool:
    try:
        return "req.files" in inspect.getsource(fn)
    except (OSError, TypeError):
        return False


def operations(routes) -> list[dict]:
    """One entry per (method, path) under /api, sorted by area and path."""
    out = []
    for r in routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/"):
            continue
        meta = getattr(getattr(r, "endpoint", None), "kosha", None)
        if meta:
            fn, methods, role, auth = meta["handler"], [meta["method"]], meta["role"], meta["auth"]
        else:   # plain Starlette routes (e.g. /api/health) are public
            fn, methods, role, auth = r.endpoint, sorted((r.methods or {"GET"}) - {"HEAD"}), None, False
        summary, description = _doc(fn)
        for method in methods:
            out.append({"method": method, "path": path, "openapi_path": _PARAM.sub(r"{\1}", path),
                        "params": [(m.group(1), _TYPES.get(m.group(2) or "str", "string")) for m in _PARAM.finditer(path)],
                        "role": role, "auth": auth, "summary": summary, "description": description,
                        "area": _area(path), "files": method in ("POST", "PUT", "PATCH") and _uses_files(fn),
                        "operation_id": f"{getattr(fn, '__module__', 'kosha').rsplit('.', 1)[-1]}_{fn.__name__}"
                                        f"_{method.lower()}"})
    out.sort(key=lambda o: (o["area"], o["path"], ["GET", "POST", "PUT", "PATCH", "DELETE"].index(o["method"])
                            if o["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE") else 9))
    return out


def openapi(routes) -> dict:
    paths: dict = {}
    tags = []
    for op in operations(routes):
        if op["area"] not in tags:
            tags.append(op["area"])
        responses = {"200": {"description": "Done. The answer is JSON unless the summary says otherwise."},
                     "400": {"$ref": "#/components/responses/Error"}}
        entry = {"operationId": op["operation_id"], "tags": [op["area"]], "summary": op["summary"],
                 "x-kosha-role": op["role"] or "public", "responses": responses}
        if op["description"]:
            entry["description"] = op["description"]
        if op["params"]:
            entry["parameters"] = [{"name": n, "in": "path", "required": True, "schema": {"type": t}}
                                   for n, t in op["params"]]
        if op["auth"]:
            entry["security"] = [{"bearer": []}, {"access_token": []}]
            responses["401"] = {"$ref": "#/components/responses/Error"}
            if op["role"] and op["role"] != "viewer":
                responses["403"] = {"$ref": "#/components/responses/Error"}
        else:
            entry["security"] = []
        if op["method"] in ("POST", "PUT", "PATCH"):
            media = "multipart/form-data" if op["files"] else "application/json"
            entry["requestBody"] = {"required": False, "content": {media: {"schema": {"type": "object"}}}}
        paths.setdefault(op["openapi_path"], {})[op["method"].lower()] = entry
    return {
        "openapi": "3.1.0",
        "info": {"title": "Kosha API", "version": __version__,
                 "description": "REST API of the Kosha PLM server. Sign in with POST /api/auth/login and send the "
                                "token as `Authorization: Bearer <token>`. `x-kosha-role` is the lowest role that "
                                "may call an operation (viewer < engineer < approver < admin). Every path also "
                                "works under /api/v1/ (for example /api/v1/items), the versioned form programs "
                                "should use. Errors are JSON: `{\"error\": \"plain message\", \"code\": "
                                "\"...\", \"request_id\": \"...\"}`; the request ID is also in the X-Request-ID "
                                "header and the server log. Updates may send If-Match with the record's "
                                "row_version (412 when someone changed it meanwhile); POST may send an "
                                "Idempotency-Key (a repeat returns the first answer); long lists page with "
                                "?cursor= and next_cursor; too many calls a minute answer 429 with Retry-After.",
                 "license": {"name": "AGPL-3.0-or-later", "identifier": "AGPL-3.0-or-later"}},
        "servers": [{"url": "/"}],
        "tags": [{"name": t, "description": AREAS.get(t, t.replace("-", " ").capitalize())} for t in tags],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
                "access_token": {"type": "apiKey", "in": "query", "name": "access_token",
                                 "description": "The same token as a query parameter, for links to files and previews."},
            },
            "responses": {"Error": {"description": "Refused; the message says why and what to do.",
                                    "content": {"application/json": {"schema": {
                                        "type": "object", "required": ["error", "code"],
                                        "properties": {"error": {"type": "string"}, "code": {"type": "string"}}}}}}},
        },
    }


_CSS = """
:root{--bg:#f6f7f5;--fg:#1d2125;--muted:#5d666e;--line:#d9dde0;--card:#fff;--code:#eef1f3;--accent:#1f5fa8}
@media (prefers-color-scheme:dark){:root{--bg:#16191d;--fg:#e8eaec;--muted:#9aa3ab;--line:#2c3238;--card:#1d2126;
--code:#262b31;--accent:#7fb3f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}main{max-width:1040px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
p{margin:6px 0}a{color:var(--accent)}code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:13px}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin:16px 0}.muted{color:var(--muted)}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:6px 0}
summary{cursor:pointer;padding:8px 12px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
summary .p{font-family:ui-monospace,Consolas,monospace;font-size:13.5px;word-break:break-all}
.m{display:inline-block;min-width:62px;text-align:center;font:600 12px ui-monospace,Consolas,monospace;
padding:2px 6px;border-radius:4px;color:#fff;background:#4a6b8a}.GET{background:#2f7d4f}.POST{background:#1f5fa8}
.PUT{background:#8a5a12}.PATCH{background:#6b4fa0}.DELETE{background:#a8322a}
.body{padding:0 12px 10px 12px;border-top:1px solid var(--line)}.role{font-size:12.5px;color:var(--muted)}
pre{white-space:pre-wrap;margin:8px 0;font:inherit}
"""


def html(spec: dict) -> str:
    e = _html.escape
    groups: dict[str, list] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            groups.setdefault(op["tags"][0], []).append((method.upper(), path, op))
    labels = {t["name"]: t["description"] for t in spec.get("tags", [])}
    order = sorted(groups, key=lambda t: labels.get(t, t).lower())
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>Kosha API {e(spec['info']['version'])}</title><style>{_CSS}</style></head><body><main>",
           f"<h1>Kosha API</h1><p class=muted>Version {e(spec['info']['version'])} · "
           f"{sum(len(v) for v in groups.values())} operations · machine-readable: "
           '<a href="/api/openapi.json">/api/openapi.json</a> (OpenAPI 3.1)</p>',
           "<p>Sign in with <code>POST /api/auth/login</code> (<code>{\"username\": ..., \"password\": ...}</code>) and "
           "send the token as <code>Authorization: Bearer &lt;token&gt;</code>. Each operation shows the lowest role "
           "that may call it. Errors come back as JSON with a plain-language <code>error</code> and a "
           "<code>code</code>.</p>", "<nav>"]
    out += [f'<a href="#{e(t)}">{e(labels.get(t, t))}</a>' for t in order]
    out.append("</nav>")
    for t in order:
        out.append(f'<h2 id="{e(t)}">{e(labels.get(t, t))}</h2>')
        for method, path, op in groups[t]:
            role = op.get("x-kosha-role", "public")
            role_text = ROLE_TEXT.get(None if role == "public" else role, role)
            desc = op.get("description", "")
            params = ", ".join(f"<code>{e(p['name'])}</code> ({e(p['schema']['type'])})"
                               for p in op.get("parameters", []))
            body = next(iter(op.get("requestBody", {}).get("content", {})), "")
            out.append(f'<details><summary><span class="m {e(method)}">{e(method)}</span>'
                       f'<span class=p>{e(path)}</span><span>{e(op.get("summary", ""))}</span></summary>'
                       f'<div class=body><p class=role>Who may call it: {e(role_text)}</p>'
                       + (f"<pre>{e(desc)}</pre>" if desc else "")
                       + (f"<p>Path parameters: {params}</p>" if params else "")
                       + (f"<p>Body: <code>{e(body)}</code></p>" if body else "")
                       + "</div></details>")
    out.append("</main></body></html>")
    return "\n".join(out)
