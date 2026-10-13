# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Teams notifier against an in-memory Teams webhook, through the SDK conformance kit."""
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "sdk" / "python")]

uvicorn = pytest.importorskip("uvicorn")

import fake_teams as FT  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.notify import Notice, NotifyError  # noqa: E402
from kosha_sdk.testing import check_manifest, check_notifier, sample_notice  # noqa: E402
from kosha_teams.notifier import TeamsNotifier, adaptive_card  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FT.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(autouse=True)
def fresh():
    FT.reset()


def notifier(url, **overrides):
    return TeamsNotifier(AddinContext("org.kosha.teams", settings={"webhook_url": url, "timeout_s": 5, **overrides}))


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance_with_workflows_and_legacy_webhooks(base):
    url = f"{base}/workflows/{FT.TOKEN}/run?api-version=2016-06-01&sig=abc"
    assert check_notifier(notifier(url)) == []
    assert len(FT.STATE["posts"]) == 3
    FT.STATE["mode"] = "legacy"
    assert check_notifier(notifier(url)) == []


def test_card_content():
    card = adaptive_card(sample_notice())["attachments"][0]["content"]
    texts = [b["text"] for b in card["body"]]
    assert texts[0].startswith("Kosha test: ECO-0001") and "For: Daniel Okafor" in texts
    assert card["body"][0]["color"] == "Warning"
    assert card["actions"] == [{"type": "Action.OpenUrl", "title": "Open in Kosha", "url": "https://plm.example.com/#/change/1"}]
    no_link = adaptive_card(Notice("T", body="x" * 9000, link="#/change/1"))["attachments"][0]["content"]
    assert "actions" not in no_link and len(no_link["body"][1]["text"]) == 3000


def test_failures_are_plain_messages(base):
    with pytest.raises(NotifyError, match="no longer exists"):
        notifier(f"{base}/workflows/deleted/run").send(sample_notice())
    FT.STATE["mode"] = "busy"
    with pytest.raises(NotifyError, match="fewer messages"):
        notifier(f"{base}/workflows/{FT.TOKEN}/run").send(sample_notice())
    FT.STATE["mode"] = "broken"
    with pytest.raises(NotifyError, match="try again later"):
        notifier(f"{base}/workflows/{FT.TOKEN}/run").send(sample_notice())
    FT.STATE["mode"] = "legacy_error"
    with pytest.raises(NotifyError, match="did not post"):
        notifier(f"{base}/workflows/{FT.TOKEN}/run").send(sample_notice())
    down = notifier(f"http://127.0.0.1:{_free_port()}/workflows/x/run?sig=SECRET", timeout_s=1)
    with pytest.raises(NotifyError) as e:
        down.send(sample_notice())
    assert "Cannot reach" in str(e.value) and "SECRET" not in str(e.value)     # the address is a secret


def test_health_without_and_with_address():
    assert not notifier("").health().ok
    assert not notifier("not an address").health().ok
    h = notifier("https://hooks.example.com/workflows/abc").health()
    assert h.ok and "hooks.example.com" in h.message and "abc" not in h.message
    assert "unencrypted" in notifier("http://hooks.example.com/x").health().message
    with pytest.raises(NotifyError, match="not set"):
        notifier("").send(sample_notice())
