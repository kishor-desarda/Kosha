# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Conformance test kit. Run it in an add-in's own tests before publishing:

    from kosha_sdk.testing import check_manifest, check_erp_connector, check_notifier
    assert check_manifest("path/to/addin") == []
    assert check_erp_connector(MyConnector(ctx)) == []      # against a TEST system, never production
    assert check_notifier(MyNotifier(ctx)) == []            # posts a few test messages to a TEST channel

Each check returns a list of problems in plain words; an empty list means the add-in passes.
"""
from __future__ import annotations

from pathlib import Path

from .context import Health
from .erp import (ErpBom, ErpBomLine, ErpBomRecord, ErpConnector, ErpError, ErpItem, ErpItemRecord, ErpNotSupported,
                  ErpResult, StockInfo)
from .errors import AddinError, ManifestError
from .manifest import load_manifest
from .notify import Notice, Notifier, NotifyError, Recipient

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


def check_erp_connector(connector: ErpConnector, prefix: str = "KOSHA-TEST", uom: str = "Nos") -> list[str]:
    """Exercise the ERP contract twice over and check idempotency. Use a test company or site.

    `uom` is a unit the ERP knows (ERPNext: Nos, Odoo: Units)."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = connector.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    a = ErpItem(code=f"{prefix}-001", description="CONFORMANCE PART A", revision="A", uom=uom)
    b = ErpItem(code=f"{prefix}-002", description="CONFORMANCE PART B", revision="A", uom=uom)
    top = ErpItem(code=f"{prefix}-000", description="CONFORMANCE ASSEMBLY", revision="A", uom=uom)
    for it in (a, b, top):
        r = connector.upsert_item(it)
        expect(isinstance(r, ErpResult), "upsert_item() must return an ErpResult")
        again = connector.upsert_item(it)
        expect(again.action != "created", f"upsert_item({it.code}) created the item a second time")
    bom = ErpBom(item=top, revision="A", lines=(ErpBomLine(a.code, 2, uom), ErpBomLine(b.code, 1, uom)))
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
    problems += _check_erp_readback(connector, a, top, bom, prefix)
    return problems


def _check_erp_readback(connector: ErpConnector, a: ErpItem, top: ErpItem, bom: ErpBom, prefix: str) -> list[str]:
    """The optional read-back methods used by reconciliation. A connector without them passes."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    missing = f"{prefix}-DOES-NOT-EXIST"
    try:
        got = connector.fetch_items([a.code, missing])
    except ErpNotSupported:
        got = None
    if got is not None:
        expect(isinstance(got, dict), "fetch_items() must return a dict of code -> ErpItemRecord")
        got = got if isinstance(got, dict) else {}
        found = {str(k).upper(): v for k, v in got.items()}
        rec = found.get(a.code.upper())
        expect(isinstance(rec, ErpItemRecord), "fetch_items() must find an item that was just created")
        if isinstance(rec, ErpItemRecord):
            expect(rec.description in (None, a.description), "fetch_items() must return the description that was sent")
            expect(rec.active in (None, True), "fetch_items() reports a just-created item as not active")
        expect(missing.upper() not in found, "fetch_items() must leave out codes the ERP does not have")
    try:
        rb = connector.fetch_bom(top.code)
        supported = True
    except ErpNotSupported:
        rb, supported = None, False
    if supported:
        expect(isinstance(rb, ErpBomRecord), "fetch_bom() must find the BOM that was just sent")
        if isinstance(rb, ErpBomRecord):
            have = sorted((ln.code.upper(), round(float(ln.qty), 6)) for ln in rb.lines)
            want = sorted((ln.code.upper(), round(float(ln.qty), 6)) for ln in bom.lines)
            expect(have == want, f"fetch_bom() returned other lines than were sent: {have} instead of {want}")
        expect(connector.fetch_bom(a.code) is None, "fetch_bom() must return None for an item without a BOM")
    try:
        codes = connector.list_items_from_kosha()
    except ErpNotSupported:
        codes = None
    if codes is not None:
        expect(isinstance(codes, list), "list_items_from_kosha() must return a list of codes")
        expect(a.code.upper() in {str(c).upper() for c in codes or ()},
               "list_items_from_kosha() must list an item Kosha has just created")
    return problems


def sample_notice(**changes) -> Notice:
    """A notice with every field filled, including characters that need escaping."""
    base = dict(title="Kosha test: ECO-0001 <needs> your approval & a check",
                body="Asha submitted ECO-0001 \"Wider base\".\nTwo files are affected: *A* _B_ <C>.",
                link="https://plm.example.com/#/change/1", severity="warning", kind="test",
                recipients=(Recipient("Daniel Okafor", "daniel@example.com", 2),), key="kosha-conformance-1")
    base.update(changes)
    return Notice(**base)


def check_notifier(notifier: Notifier) -> list[str]:
    """Check the notifier contract. Sends three short test messages, so point it at a TEST channel."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = notifier.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    for label, notice in (("a normal notice", sample_notice()),
                          ("the same notice again", sample_notice()),
                          ("a very long notice without a link", sample_notice(body="x" * 20000, link="", recipients=(),
                                                                               key="kosha-conformance-2"))):
        try:
            r = notifier.send(notice)
            expect(r is None, f"send() must return None ({label})")
        except NotifyError as e:
            problems.append(f"send() refused {label}: {e}")
        except AddinError as e:
            problems.append(f"send() must raise NotifyError, not {e.__class__.__name__} ({label}): {e}")
    return problems
