# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Telegram notifier against an in-memory Bot API, through the SDK conformance kit."""
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

import fake_telegram as FG  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.notify import Notice, NotifyError  # noqa: E402
from kosha_sdk.testing import check_manifest, check_notifier, sample_notice  # noqa: E402
from kosha_telegram.notifier import TelegramNotifier, telegram_html  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FG.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(autouse=True)
def fresh():
    FG.reset()


def notifier(base, **overrides):
    settings = {"bot_token": FG.TOKEN, "chat_id": FG.CHAT, "api_base": base, "timeout_s": 5, **overrides}
    return TelegramNotifier(AddinContext("org.kosha.telegram", settings=settings))


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance(base):
    assert check_notifier(notifier(base)) == []
    assert len(FG.STATE["messages"]) == 3
    first = FG.STATE["messages"][0]
    assert first["parse_mode"] == "HTML" and first["chat_id"] == FG.CHAT


def test_topic_number_is_sent(base):
    notifier(base, topic_id="42").send(sample_notice())
    assert FG.STATE["messages"][-1]["message_thread_id"] == 42


def test_message_is_safe_html_within_the_limit():
    text = telegram_html(sample_notice())
    assert text.startswith("<b>Kosha test: ECO-0001 &lt;needs&gt; your approval &amp; a check</b>")
    assert "&lt;C&gt;" in text and '<a href="https://plm.example.com/#/change/1">Open in Kosha</a>' in text
    assert "<i>For: Daniel Okafor</i>" in text
    long = telegram_html(Notice("T", body="<&>" * 3000, link="https://plm.example.com/#/x"))
    assert len(long) <= 4096 and long.endswith("Open in Kosha</a>")
    assert "Open in Kosha" not in telegram_html(Notice("T", link="#/relative"))


def test_health_names_the_bot(base):
    h = notifier(base).health()
    assert h.ok and "@kosha_test_bot" in h.message and FG.CHAT in h.message
    assert not notifier(base, chat_id="").health().ok


def test_failures_are_plain_messages_without_the_token(base):
    with pytest.raises(NotifyError, match="does not accept the bot token") as e:
        notifier(base, bot_token="999:WRONG").send(sample_notice())
    assert "WRONG" not in str(e.value)
    with pytest.raises(NotifyError, match="cannot find the chat"):
        notifier(base, chat_id="-100999").send(sample_notice())
    FG.STATE["kicked"] = True
    with pytest.raises(NotifyError, match="may not post"):
        notifier(base).send(sample_notice())
    down = notifier(f"http://127.0.0.1:{_free_port()}", timeout_s=1)
    with pytest.raises(NotifyError) as e:
        down.send(sample_notice())
    assert "Cannot reach" in str(e.value) and FG.TOKEN not in str(e.value)
    assert FG.TOKEN not in down.health().message
    with pytest.raises(NotifyError, match="token is not set"):
        notifier(base, bot_token="").send(sample_notice())
