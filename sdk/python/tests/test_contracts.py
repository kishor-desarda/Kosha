# SPDX-License-Identifier: Apache-2.0
"""The optional ERP read-back methods and the Notifier contract, with in-memory stand-ins."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kosha_sdk import AddinContext, AddinError, Health  # noqa: E402
from kosha_sdk.cli import main  # noqa: E402
from kosha_sdk.erp import (ErpBom, ErpBomRecord, ErpConnector, ErpItem, ErpItemRecord, ErpNotSupported,  # noqa: E402
                           ErpResult, StockInfo)
from kosha_sdk.events import EVENTS, on_event  # noqa: E402
from kosha_sdk.notify import Notice, Notifier, NotifyError, Recipient  # noqa: E402
from kosha_sdk.testing import check_erp_connector, check_manifest, check_notifier, sample_notice  # noqa: E402


class MemoryErp(ErpConnector):
    """A tiny ERP in a dict: just enough to show what the conformance kit expects."""

    def __init__(self, ctx, readback=True, wrong_lines=False):
        super().__init__(ctx)
        self.items: dict[str, ErpItem] = {}
        self.boms: dict[str, ErpBom] = {}
        self.readback, self.wrong_lines = readback, wrong_lines

    def health(self):
        return Health.up("memory")

    def upsert_item(self, item):
        if self.items.get(item.code) == item:
            return ErpResult.unchanged("unchanged", item.code)
        action = ErpResult.updated if item.code in self.items else ErpResult.created
        self.items[item.code] = item
        return action("ok", {}, item.code)

    def upsert_bom(self, bom):
        if self.boms.get(bom.item.code) == bom:
            return ErpResult.unchanged("unchanged")
        self.boms[bom.item.code] = bom
        return ErpResult.created("ok")

    def stock_and_cost(self, code):
        return StockInfo(exists=code in self.items)

    def fetch_items(self, codes):
        if not self.readback:
            return super().fetch_items(codes)
        return {c: ErpItemRecord(c, self.items[c].description, self.items[c].uom, active=True)
                for c in codes if c in self.items}

    def fetch_bom(self, code):
        if not self.readback:
            return super().fetch_bom(code)
        b = self.boms.get(code)
        if b is None:
            return None
        lines = b.lines[:1] if self.wrong_lines else b.lines
        return ErpBomRecord(code, lines, b.revision, f"BOM-{code}")

    def list_items_from_kosha(self):
        if not self.readback:
            return super().list_items_from_kosha()
        return list(self.items)


def ctx():
    return AddinContext("com.example.memory")


def test_readback_is_optional_and_says_so():
    c = MemoryErp(ctx(), readback=False)
    for call in (lambda: c.fetch_items(["X"]), lambda: c.fetch_bom("X"), c.list_items_from_kosha):
        with pytest.raises(ErpNotSupported, match="cannot"):
            call()
    assert issubclass(ErpNotSupported, AddinError)          # the server shows the message, never a crash
    assert check_erp_connector(c) == []                     # a connector without read-back still passes


def test_readback_is_checked_when_offered():
    assert check_erp_connector(MemoryErp(ctx())) == []
    problems = check_erp_connector(MemoryErp(ctx(), wrong_lines=True))
    assert any("fetch_bom() returned other lines" in p for p in problems), problems


def test_records_default_to_unknown_fields():
    r = ErpItemRecord("MX-1")
    assert r.description is None and r.revision is None and r.active is None


# ------------------------------------------------------------------ notifier

class MemoryNotifier(Notifier):
    def __init__(self, ctx, fail=False):
        super().__init__(ctx)
        self.sent: list[Notice] = []
        self.fail = fail

    def health(self):
        return Health.up("ok")

    def send(self, notice):
        if self.fail:
            raise NotifyError("The channel said no.")
        self.sent.append(notice)


def test_notice_text_and_html_are_safe():
    n = sample_notice()
    text = n.text()
    assert n.title in text and "For: Daniel Okafor" in text and "Open in Kosha: https://plm.example.com/#/change/1" in text
    page = n.html()
    assert "&lt;needs&gt;" in page and "<needs>" not in page and "&amp; a check" in page
    assert '<a href="https://plm.example.com/#/change/1">' in page and "<br>" in page


def test_notice_helpers():
    n = Notice("T", body="abcdef", severity="loud", recipients=(Recipient("A"), Recipient("")))
    assert n.severity == "info"                       # unknown severities fall back to info
    assert n.for_line() == "For: A"
    assert n.short_body(4) == "abc…" and n.short_body(10) == "abcdef"
    assert "Open in Kosha" not in n.text() and "<a " not in n.html()


def test_notifier_conformance():
    good = MemoryNotifier(ctx())
    assert check_notifier(good) == []
    assert len(good.sent) == 3 and good.sent[0].key == good.sent[1].key
    problems = check_notifier(MemoryNotifier(ctx(), fail=True))
    assert problems and "refused" in problems[0]


def test_new_events_are_known():
    assert "change.submitted" in EVENTS and "erp.job_failed" in EVENTS
    on_event("erp.job_failed")(lambda c, e: None)


def test_notifier_template_creates_a_valid_addin(tmp_path):
    assert main(["new", "notifier", "team-chat", "--publisher", "com.example", "--dest", str(tmp_path)]) == 0
    folder = tmp_path / "team-chat"
    assert check_manifest(folder) == []
    sys.path.insert(0, str(folder))
    try:
        from team_chat.notifier import Notifier as Generated
        n = Generated(AddinContext("com.example.team-chat", settings={}))
        assert not n.health().ok
        with pytest.raises(NotifyError, match="not set"):
            n.send(sample_notice())
    finally:
        sys.path.remove(str(folder))
