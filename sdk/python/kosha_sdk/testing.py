# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Conformance test kit. Run it in an add-in's own tests before publishing:

    from kosha_sdk.testing import check_manifest, check_erp_connector
    assert check_manifest("path/to/addin") == []
    assert check_erp_connector(MyConnector(ctx)) == []      # against a TEST system, never production

Each check returns a list of problems in plain words; an empty list means the add-in passes.
"""
from __future__ import annotations

from pathlib import Path

from .context import Health
from .erp import ErpBom, ErpBomLine, ErpConnector, ErpError, ErpItem, ErpResult, StockInfo
from .errors import ManifestError
from .manifest import load_manifest

_NEEDS = {
    "erp-connector": ("server", "erp:sync"),
    "file-type": ("server", "files:read"),
    "notifier": ("server", "notify:send"),
    "web-extension": ("web", None),
    "cad-addin": ("cad", None),
    "office-addin": ("office", None),
}


def check_manifest(path: str | Path) -> list[str]:
    try:
        m = load_manifest(path)
    except ManifestError as e:
        return [f"manifest: {e}"]
    problems = []
    for kind in m.kinds:
        place, perm = _NEEDS.get(kind, (None, None))
        if place and place not in m.entry_points:
            problems.append(f"a {kind} add-in needs entry_points.{place}")
        if perm and perm not in m.permissions:
            problems.append(f"a {kind} add-in needs the permission {perm}")
    for s in m.settings:
        if s.type == "secret" and s.key.lower() in ("url", "base_url"):
            problems.append(f"setting {s.key} looks like an address, not a secret")
    if m.licence.strip().upper().startswith("PROPRIETARY") and m.publisher.lower() == "kosha project":
        problems.append("official add-ins must use an open-source licence")
    return problems


def check_erp_connector(connector: ErpConnector, prefix: str = "KOSHA-TEST") -> list[str]:
    """Exercise the ERP contract twice over and check idempotency. Use a test company or site."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = connector.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    a = ErpItem(code=f"{prefix}-001", description="CONFORMANCE PART A", revision="A", uom="Nos")
    b = ErpItem(code=f"{prefix}-002", description="CONFORMANCE PART B", revision="A", uom="Nos")
    top = ErpItem(code=f"{prefix}-000", description="CONFORMANCE ASSEMBLY", revision="A", uom="Nos")
    for it in (a, b, top):
        r = connector.upsert_item(it)
        expect(isinstance(r, ErpResult), "upsert_item() must return an ErpResult")
        again = connector.upsert_item(it)
        expect(again.action != "created", f"upsert_item({it.code}) created the item a second time")
    bom = ErpBom(item=top, revision="A", lines=(ErpBomLine(a.code, 2, "Nos"), ErpBomLine(b.code, 1, "Nos")))
    first = connector.upsert_bom(bom)
    expect(first.action in ("created", "updated", "unchanged"), "upsert_bom() returned an unknown action")
    second = connector.upsert_bom(bom)
    expect(second.action == "unchanged", "sending the same BOM twice must return 'unchanged'")
    s = connector.stock_and_cost(a.code)
    expect(isinstance(s, StockInfo) and s.exists, "stock_and_cost() must find an item that was just created")
    try:
        missing = connector.stock_and_cost(f"{prefix}-DOES-NOT-EXIST")
        expect(isinstance(missing, StockInfo) and not missing.exists, "stock_and_cost() must report a missing item")
    except ErpError:
        problems.append("stock_and_cost() must return exists=False for a missing item, not raise")
    return problems
