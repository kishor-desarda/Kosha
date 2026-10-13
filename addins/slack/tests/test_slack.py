# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Slack notifier against an in-memory Slack webhook, through the SDK conformance kit."""
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

import fake_slack as FS  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.notify import Notice, NotifyError  # noqa: E402
from kosha_sdk.testing import check_manifest, check_notifier, sample_notice  # noqa: E402
from kosha_slack.notifier import SlackNotifier, slack_payload  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FS.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(autouse=True)
def fresh():
    FS.reset()


def notifier(url, **overrides):
    return SlackNotifier(AddinContext("org.kosha.slack", settings={"webhook_url": url, "timeout_s": 5, **overrides}))


def good(base):
    return f"{base}/services/{FS.TEAM}/{FS.HOOK}/{FS.SECRET}"


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance(base):
    assert check_notifier(notifier(good(base))) == []
    assert len(FS.STATE["posts"]) == 3


def test_message_content_is_escaped():
    p = slack_payload(sample_notice())
    section = p["blocks"][0]["text"]["text"]
    assert section.startswith(":warning: *Kosha test: ECO-0001 &lt;needs&gt; your approval &amp; a check*")
    assert "&lt;C&gt;" in section and "<C>" not in section
    assert p["blocks"][1]["elements"][0]["text"] == "For: Daniel Okafor"
    assert p["blocks"][2]["elements"][0]["url"] == "https://plm.example.com/#/change/1"
    long = slack_payload(Notice("T", body="y" * 9000, link="#/x"))
    assert len(long["blocks"][0]["text"]["text"]) <= 3000 and len(long["blocks"]) == 1   # relative links get no button


def test_failures_are_plain_messages(base):
    with pytest.raises(NotifyError, match="no longer accepts this webhook"):
        notifier(f"{base}/services/{FS.TEAM}/{FS.HOOK}/wrong").send(sample_notice())
    with pytest.raises(NotifyError, match="does not exist in Slack"):
        notifier(f"{base}/services/T999/{FS.HOOK}/{FS.SECRET}").send(sample_notice())
    FS.STATE["mode"] = "archived"
    with pytest.raises(NotifyError, match="archived"):
        notifier(good(base)).send(sample_notice())
    FS.STATE["mode"] = "busy"
    with pytest.raises(NotifyError, match="fewer messages"):
        notifier(good(base)).send(sample_notice())
    FS.STATE["mode"] = "broken"
    with pytest.raises(NotifyError, match="try again later"):
        notifier(good(base)).send(sample_notice())
    down = notifier(f"http://127.0.0.1:{_free_port()}/services/T/B/SECRETPART", timeout_s=1)
    with pytest.raises(NotifyError) as e:
        down.send(sample_notice())
    assert "Cannot reach" in str(e.value) and "SECRETPART" not in str(e.value)


def test_health():
    assert not notifier("").health().ok
    h = notifier("https://hooks.example.com/services/T/B/S").health()
    assert h.ok and "hooks.example.com" in h.message and "/S" not in h.message
    with pytest.raises(NotifyError, match="not set"):
        notifier("").send(sample_notice())
