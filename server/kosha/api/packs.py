# SPDX-License-Identifier: AGPL-3.0-or-later
"""Industry packs: list, install, update and remove; the company's own changes to pack data."""
from __future__ import annotations

from ..packs import KINDS, DataError, PackConfig, PackManager
from ..web import Req, api, bad, not_found


def _manager() -> PackManager:
    return PackManager()


def _kind_key(req: Req) -> tuple[str, str]:
    kind, key = (req.b("kind") or "").strip(), (req.b("key") or "").strip()
    if kind not in KINDS:
        raise bad(f"Unknown kind of record '{kind}'.")
    if not key:
        raise bad("Say which record: the key is missing.")
    return kind, key


@api("GET", "/api/packs", role="admin")
def list_packs(req: Req):
    m = _manager()
    return {"packs": m.overview(), "kinds": KINDS, "folder": str(m.loader.root),
            "local_changes": len(m.local_changes())}


@api("GET", "/api/packs/{pack_id}", role="admin")
def pack_detail(req: Req):
    try:
        return _manager().detail(req.params["pack_id"])
    except DataError as e:
        raise not_found(str(e))


@api("GET", "/api/packs/{pack_id}/plan", role="admin")
def pack_plan(req: Req):
    """What installing or updating would change (nothing is written)."""
    try:
        return {**_manager().plan(req.params["pack_id"]), "kinds": KINDS}
    except DataError as e:
        raise bad(str(e), "invalid_pack")


@api("POST", "/api/packs/{pack_id}/install", role="admin")
def pack_install(req: Req):
    try:
        return _manager().install(req.params["pack_id"], req.uid)
    except DataError as e:
        raise bad(str(e), "invalid_pack")


@api("POST", "/api/packs/{pack_id}/update", role="admin")
def pack_update(req: Req):
    return pack_install(req)


@api("POST", "/api/packs/{pack_id}/remove", role="admin")
def pack_remove(req: Req):
    try:
        return _manager().remove(req.params["pack_id"], req.uid)
    except DataError as e:
        raise bad(str(e), "not_installed")


# ------------------------------------------------- effective records, changes --

@api("GET", "/api/pack-records")
def pack_records(req: Req):
    """The effective configuration (packs plus the company's changes), optionally one kind or scope."""
    kind = req.q("kind")
    if kind and kind not in KINDS:
        raise bad(f"Unknown kind of record '{kind}'.")
    cfg = PackConfig.current()
    kinds = [kind] if kind else list(KINDS)
    out = []
    for k in kinds:
        if k == "library_row" and not req.q("scope") and not kind:
            continue            # large; ask for one table at a time
        out += [r.to_dict() for r in cfg.records(k, req.q("scope") or None)]
    return {"records": out, "kinds": KINDS}


@api("GET", "/api/pack-records/changes", role="admin")
def pack_changes(req: Req):
    return {"changes": _manager().local_changes(), "kinds": KINDS}


@api("PUT", "/api/pack-records", role="admin")
def pack_record_set(req: Req):
    kind, key = _kind_key(req)
    try:
        return {"record": _manager().set_local(kind, key, req.b("value"), req.uid)}
    except DataError as e:
        raise bad(str(e), "invalid_record")


@api("POST", "/api/pack-records/hide", role="admin")
def pack_record_hide(req: Req):
    kind, key = _kind_key(req)
    try:
        _manager().hide(kind, key, req.uid)
    except DataError as e:
        raise bad(str(e), "invalid_record")
    return {"ok": True}


@api("POST", "/api/pack-records/revert", role="admin")
def pack_record_revert(req: Req):
    kind, key = _kind_key(req)
    try:
        return _manager().revert(kind, key, req.uid)
    except DataError as e:
        raise bad(str(e), "invalid_record")


@api("POST", "/api/pack-records/keep", role="admin")
def pack_record_keep(req: Req):
    kind, key = _kind_key(req)
    try:
        _manager().keep_mine(kind, key, req.uid)
    except DataError as e:
        raise bad(str(e), "invalid_record")
    return {"ok": True}
