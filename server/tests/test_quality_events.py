# SPDX-License-Identifier: AGPL-3.0-or-later
"""An add-in that listens to ``deviation.approved`` hears about an approved deviation (e.g. to tell purchasing)."""
import json
import textwrap
import time

import pytest

from harness import KoshaServer

ADDIN_ID = "com.example.purchasing-news"


@pytest.fixture(scope="module")
def kosha_with_addin(tmp_path_factory):
    root = tmp_path_factory.mktemp("addins")
    folder = root / "purchasing-news"
    (folder / "purchasing_news").mkdir(parents=True)
    (folder / "kosha-addin.yaml").write_text(textwrap.dedent(f"""\
        id: {ADDIN_ID}
        name: Purchasing news
        version: 1.0.0
        publisher: Example
        licence: Apache-2.0
        kosha: ">=0.5,<2.0"
        kind: [notifier]
        entry_points: {{server: "purchasing_news.main:Addin"}}
        permissions: [changes:read]
        events: [deviation.approved]
        """))
    (folder / "purchasing_news" / "__init__.py").write_text("")
    (folder / "purchasing_news" / "main.py").write_text(textwrap.dedent("""\
        import json
        from kosha_sdk.events import on_event

        class Addin:
            def __init__(self, ctx):
                self.ctx = ctx

        @on_event("deviation.approved")
        def tell_purchasing(ctx, event):
            with open(ctx.workdir / "approved.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\\n")
        """))
    s = KoshaServer(env={"KOSHA_ADDIN_DIR": str(root)}).start()
    yield s
    s.stop()


def test_deviation_approved_reaches_addins(kosha_with_addin):
    s = kosha_with_addin
    admin = s.client()
    assert admin.call("POST", f"/api/addins/{ADDIN_ID}/enable")["status"] == "enabled"
    eng = s.add_user("erin", "engineer")
    appr = s.add_user("arno", "approver")
    arno_id = [u["id"] for u in admin.call("GET", "/api/users")["users"] if u["username"] == "arno"][0]
    item = admin.call("POST", "/api/items", {"part_number": "PU-200-010", "description": "IMPELLER", "material": "CC480K"})["item"]
    d = eng.call("POST", "/api/deviations", {"title": "CC491K impellers for order 5512", "departure": "material CC491K allowed",
                                             "qty_limit": 3, "affected": [{"item_id": item["id"]}], "approver_ids": [arno_id]})
    eng.call("POST", f"/api/deviations/{d['id']}/submit")
    out = s.data_dir / "data" / "addins" / ADDIN_ID / "approved.jsonl"
    assert not out.exists()                                                          # nothing before approval
    appr.call("POST", f"/api/deviations/{d['id']}/decide", {"decision": "approved"})
    for _ in range(100):
        if out.exists() and out.read_text().strip():
            break
        time.sleep(0.05)
    events = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "deviation.approved" and e["number"] == d["number"] and e["state"] == "active"
    assert e["qty_limit"] == 3 and e["affected"][0]["part_number"] == "PU-200-010" and e["user_id"] == arno_id
