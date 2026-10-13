# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for end-to-end tests that talk to a simulated ERP or chat server running in the test process."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from harness import Client, free_port

REPO = Path(__file__).resolve().parents[2]
for sub in ("erpnext", "odoo", "dolibarr", "file-exchange", "slack", "teams", "telegram"):
    p = str(REPO / "addins" / sub / "tests")
    if p not in sys.path:
        sys.path.append(p)


def serve(app) -> tuple[object, str]:
    """Run a Starlette app on a free port in a background thread; returns (server, base URL)."""
    import uvicorn
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


def folder(client, name: str) -> int:
    root = client.call("GET", "/api/folders")["folders"][0]["id"]
    return client.call("POST", "/api/folders", {"parent_id": root, "name": name})["folder"]["id"]


_APPROVERS: dict[str, Client] = {}


def _second_approver(client) -> Client:
    """Someone other than the uploader, so releases pass the four-eyes rule."""
    if client.base not in _APPROVERS:
        users = {u["username"] for u in client.call("GET", "/api/users")["users"]}
        if "rel-approver" not in users:
            client.call("POST", "/api/users", {"username": "rel-approver", "full_name": "Release Approver",
                                               "role": "approver", "password": "password-123"})
        r = Client(client.base).call("POST", "/api/auth/login", {"username": "rel-approver", "password": "password-123"})
        _APPROVERS[client.base] = Client(client.base, r["token"])
    return _APPROVERS[client.base]


def released_part(client, folder_id: int, pn: str, description: str, material: str = "") -> dict:
    """Check in a drawing for the part and release it: Kosha then has a released item with that part number."""
    from kosha.cad import dxfwriter as W
    # a part made in-house needs its material before review (release_rules)
    values = {"PART_NO": pn, "DESCRIPTION": description, "MATERIAL": material or "S235"}
    r = client.call("POST", "/api/documents/upload", files={"file": (f"{pn} {description.title()}.dxf",
                                                                    W.flange(values=values))},
                    fields={"folder_id": folder_id})
    assert not r["errors"], r
    doc = r["documents"][0]
    _second_approver(client).call("POST", f"/api/documents/{doc['id']}/state", {"to": "released"})
    item = client.call("GET", f"/api/items?q={pn}")["items"][0]
    assert item["state"] == "released", item
    return {"doc": doc, "item": item}


def released_bom(client, assy_item_id: int, lines: list[tuple[int, float]]) -> int:
    bom_id = client.call("POST", "/api/boms", {"item_id": assy_item_id})["bom_id"]
    client.call("PUT", f"/api/boms/{bom_id}/lines", {"lines": [{"item_id": i, "qty": q} for i, q in lines]})
    client.call("POST", f"/api/boms/{bom_id}/release")
    return bom_id


def drain_erp(client, timeout: float = 30) -> list[dict]:
    """Process the ERP queue until nothing is waiting; returns the jobs."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        client.call("POST", "/api/erp/process")
        jobs = client.call("GET", "/api/erp/jobs")["jobs"]
        if not any(j["status"] in ("pending", "running") for j in jobs):
            return jobs
        time.sleep(0.2)
    raise AssertionError("ERP jobs still waiting: " + str([j for j in jobs if j["status"] in ("pending", "running")]))


def run_check(client, timeout: float = 30) -> dict:
    """Start a reconciliation and wait for it to end."""
    client.call("POST", "/api/erp/reconcile")
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.call("GET", "/api/erp/reconcile")
        if d["run"]["status"] != "running":
            return d
        time.sleep(0.2)
    raise AssertionError("the reconciliation did not finish")
