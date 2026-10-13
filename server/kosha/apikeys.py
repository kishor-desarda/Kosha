# SPDX-License-Identifier: AGPL-3.0-or-later
"""API keys for programs (specification 13.2).

A program (the company's AI, a script, an add-in on a server) uses a **service account** with one or more keys.
Each key carries **scopes**, for example ``items:read`` or ``inputs:write``, and may be limited to listed addresses
and to an end date. The key is shown once when it is made; Kosha keeps only its SHA-256 fingerprint. A key is
revoked, never deleted, so the list shows what had access and when it was last used.

Programs **propose but never approve**: a service account acts at most as an engineer (creating items in work,
adding inputs, commenting, drafting change requests); releasing, approving and deleting always need a person.

Send the key as ``Authorization: Bearer ksk_...`` or ``X-API-Key: ksk_...``.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
from datetime import date

from . import db
from .web import bad, conflict, not_found

PREFIX = "ksk_"
# the areas of the API a key can be given, each as :read (GET) and :write (everything else)
AREAS = {
    "items": "Parts (items), OEM references, numbers",
    "files": "Files, folders and versions",
    "boms": "Bills of materials",
    "changes": "Change requests, change orders and deviations",
    "inputs": "Inputs library and projects",
    "discussions": "Discussions and comments",
    "search": "Search and reference data",
    "ai": "AI assistance",
}
SCOPES = [f"{a}:{m}" for a in AREAS for m in ("read", "write")]
# first path segment after /api/ -> area
PATH_AREA = {
    "items": "items", "oems": "items", "oem-refs": "items", "numbering": "items", "numbers": "items",
    "families": "items", "documents": "files", "folders": "files", "uploads": "files", "files": "files",
    "boms": "boms", "changes": "changes", "change-requests": "changes", "deviations": "changes",
    "inputs": "inputs", "projects": "inputs", "collections": "inputs", "discussions": "discussions",
    "search": "search", "meta": "search", "library": "search", "settings": "search", "ai": "ai",
}
ALWAYS = {("GET", "/api/auth/me"), ("GET", "/api/openapi.json"), ("GET", "/api/docs"), ("GET", "/api/settings")}


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def scope_needed(method: str, path: str) -> str | None:
    """The scope a call needs, or None when no key may make it (administration, sign-in, users ...)."""
    parts = path.split("/")
    area = PATH_AREA.get(parts[2] if len(parts) > 2 else "")
    if not area:
        return None
    if area == "search" and path.startswith("/api/settings") and method != "GET":
        return None
    return f"{area}:{'read' if method == 'GET' else 'write'}"


def allowed(user: dict, method: str, path: str) -> str:
    """Why a key may not make this call ("" when it may)."""
    if (method, path) in ALWAYS:
        return ""
    if method == "DELETE":
        return "Programs can propose and change, but deleting always needs a person."
    need = scope_needed(method, path)
    if not need:
        return "API keys cannot use this part of Kosha; it needs a person signed in."
    if need not in user.get("api_scopes", []):
        return f"This API key does not have the scope {need}. An admin can add it under Admin › Users."
    return ""


def authenticate(key: str, client_ip: str) -> dict | None:
    """The service account behind a key, with its scopes, when the key is good and may be used from here."""
    if not key.startswith(PREFIX):
        return None
    row = db.one("SELECT * FROM api_keys WHERE key_hash = ?", (_hash(key),))
    if not row or row["revoked_at"]:
        return None
    if row["expires_on"] and row["expires_on"] < date.today().isoformat():
        return None
    if row["allowed_ips"] and not _ip_allowed(client_ip, row["allowed_ips"]):
        return None
    user = db.one("SELECT id, username, full_name, email, role, ui_mode, ui_theme, active, kind, view_only, "
                  "expires_on FROM users WHERE id = ?", (row["user_id"],))
    if not user or not user["active"] or user["kind"] != "service":
        return None
    if user["expires_on"] and user["expires_on"] < date.today().isoformat():
        return None
    now = db.now()
    if (row["last_used_at"] or "")[:16] != now[:16] or row["last_used_ip"] != client_ip:   # at most once a minute
        try:
            db.execute("UPDATE api_keys SET last_used_at = ?, last_used_ip = ? WHERE id = ?", (now, client_ip, row["id"]))
        except Exception:       # a busy database must not refuse the call
            pass
    if user["role"] in ("checker", "approver", "admin"):     # programs propose; every role check sees an engineer
        user["account_role"], user["role"] = user["role"], "engineer"
    user["api_key_id"] = row["id"]
    user["api_scopes"] = json.loads(row["scopes_json"] or "[]")
    return user


def _ip_allowed(ip: str, allowed_ips: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for part in allowed_ips.replace(",", " ").split():
        try:
            if addr in ipaddress.ip_network(part, strict=False):
                return True
        except ValueError:
            continue
    return False


def _clean(body: dict) -> dict:
    name = " ".join(str(body.get("name") or "").split())[:80]
    if not name:
        raise bad("Name the key after the program that uses it, for example \"Drawing check robot\".")
    scopes = body.get("scopes") or []
    if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes):
        raise bad("Choose at least one scope: " + ", ".join(SCOPES) + ".")
    ips = " ".join(str(body.get("allowed_ips") or "").replace(",", " ").split())
    for part in ips.split():
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise bad(f"{part} is not an address or a network such as 10.0.5.0/24.") from None
    expires = body.get("expires_on") or None
    if expires:
        try:
            expires = date.fromisoformat(str(expires)).isoformat()
        except ValueError:
            raise bad("Write the end date as YYYY-MM-DD.") from None
        if expires < date.today().isoformat():
            raise bad("The end date has passed.")
    return {"name": name, "scopes_json": json.dumps(sorted(set(scopes))), "allowed_ips": ips, "expires_on": expires}


def create(admin_id: int, user_id: int, body: dict) -> dict:
    u = db.one("SELECT id, kind, active, full_name FROM users WHERE id = ?", (user_id,))
    if not u:
        raise not_found("User not found")
    if u["kind"] != "service":
        raise conflict("API keys are for service accounts. Make an account of the kind \"Service account\" for the "
                       "program first.", "not_service_account")
    vals = _clean(body)
    key = PREFIX + secrets.token_urlsafe(30).replace("-", "x").replace("_", "y")
    with db.tx():
        kid = db.insert("api_keys", {**vals, "user_id": user_id, "key_prefix": key[:12], "key_hash": _hash(key),
                                     "created_by": admin_id, "created_at": db.now()})
        db.audit(admin_id, "api_key_create", "user", user_id, f"{vals['name']}: {', '.join(json.loads(vals['scopes_json']))}")
    return {**listing(user_id, kid)[0], "key": key}


def listing(user_id: int, only: int | None = None) -> list[dict]:
    from .lifecycle import user_names
    names = user_names()
    rows = db.rows("SELECT * FROM api_keys WHERE user_id = ?" + (" AND id = ?" if only else "") + " ORDER BY id",
                   (user_id, only) if only else (user_id,))
    out = []
    for r in rows:
        r.pop("key_hash", None)
        out.append({**r, "scopes": json.loads(r.pop("scopes_json") or "[]"), "created_by_name": names.get(r["created_by"]),
                    "revoked_by_name": names.get(r["revoked_by"]) if r["revoked_by"] else None,
                    "active": not r["revoked_at"] and not (r["expires_on"] and r["expires_on"] < date.today().isoformat())})
    return out


def revoke(admin_id: int, key_id: int) -> dict:
    row = db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    if not row:
        raise not_found("API key not found")
    if not row["revoked_at"]:
        with db.tx():
            db.update("api_keys", key_id, {"revoked_at": db.now(), "revoked_by": admin_id})
            db.audit(admin_id, "api_key_revoke", "user", row["user_id"], row["name"])
    return listing(row["user_id"], key_id)[0]
