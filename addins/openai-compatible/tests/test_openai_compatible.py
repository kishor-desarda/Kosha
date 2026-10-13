# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""The OpenAI-compatible AI provider against an in-memory server, through the SDK conformance kit. No network."""
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

import fake_chat as FC  # noqa: E402
from kosha_openai_compat.provider import DEFAULT_BASE_URL, OpenAICompatibleProvider  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.ai import AiError, AiNotSupported, conversation  # noqa: E402
from kosha_sdk.manifest import load_manifest  # noqa: E402
from kosha_sdk.testing import check_ai_provider, check_manifest  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FC.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True


@pytest.fixture(autouse=True)
def fresh():
    FC.reset()


def provider(base_url, **overrides):
    settings = {"base_url": base_url, "model": "kosha-test-model", "api_key": FC.KEY, "timeout_s": 5, **overrides}
    return OpenAICompatibleProvider(AddinContext("org.kosha.openai-compatible", settings=settings))


def test_manifest_is_valid_and_defaults_to_a_local_server():
    assert check_manifest(HERE.parent) == []
    m = load_manifest(HERE.parent)
    assert m.setting("base_url").default == DEFAULT_BASE_URL == "http://localhost:11434/v1"
    assert m.setting("api_key").type == "secret" and m.setting("model").default is None
    assert m.permissions == ("ai:provide",)


def test_conformance(base):
    assert check_ai_provider(provider(base, embedding_model="kosha-embed")) == []
    assert check_ai_provider(provider(base)) == []                     # without embeddings too


def test_request_carries_the_messages_the_key_and_json_mode(base):
    p = provider(base)
    r = p.complete(conversation("Answer as JSON.", "Classify this part."), max_tokens=77, temperature=0.1,
                   json_mode=True)
    assert r.text == '{"ok": true}' and r.model == "kosha-test-model" and not r.truncated
    assert r.usage == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    sent = FC.STATE["requests"][-1]
    assert sent["auth"] == f"Bearer {FC.KEY}"
    assert sent["body"]["messages"] == [{"role": "system", "content": "Answer as JSON."},
                                        {"role": "user", "content": "Classify this part."}]
    assert sent["body"]["max_tokens"] == 77 and sent["body"]["stream"] is False
    assert sent["body"]["response_format"] == {"type": "json_object"}
    p.complete(conversation("Be brief.", "Hi"))
    assert "response_format" not in FC.STATE["requests"][-1]["body"]
    provider(base, json_mode=False).complete(conversation("JSON please", "x"), json_mode=True)
    assert "response_format" not in FC.STATE["requests"][-1]["body"]


def test_local_server_without_a_key(base):
    FC.STATE["mode"] = "open"
    p = provider(base, api_key="")
    assert p.complete(conversation("s", "u")).text == "OK"
    assert FC.STATE["requests"][-1]["auth"] is None


def test_older_server_without_json_mode(base):
    FC.STATE["mode"] = "no_format"
    p = provider(base)
    assert p.complete(conversation("Answer as JSON.", "x"), json_mode=True).text == '{"ok": true}'
    assert "response_format" not in FC.STATE["requests"][-1]["body"]
    n = len(FC.STATE["requests"])
    p.complete(conversation("Answer as JSON.", "x"), json_mode=True)        # remembered: no refused attempt first
    assert len(FC.STATE["requests"]) == n + 1


def test_answers_in_parts_and_cut_answers(base):
    FC.STATE["mode"] = "parts"
    FC.STATE["reply"] = "Hello world"
    assert provider(base).complete(conversation("s", "u")).text == "Hello world"
    FC.STATE["mode"] = "cut"
    assert provider(base).complete(conversation("s", "u")).truncated


def test_embeddings_come_back_in_order(base):
    v = provider(base, embedding_model="kosha-embed").embed(["a", "bbb"])
    assert v == [[1.0, 1.0, 0.5], [3.0, 1.0, 0.5]]
    with pytest.raises(AiNotSupported, match="No embedding model"):
        provider(base).embed(["a"])


def test_health(base):
    assert provider(base).health().ok
    h = provider(base, model="no-such-model").health()
    assert not h.ok and "does not offer the model 'no-such-model'" in h.message and "kosha-test-model" in h.message
    FC.STATE["mode"] = "no_models"
    assert provider(base).health().ok and "does not list" in provider(base).health().message
    assert not provider(base, model="").health().ok
    assert not provider("not-an-address").health().ok
    FC.STATE["mode"] = "ok"
    h = provider(base, api_key="wrong").health()
    assert not h.ok and "refused the API key" in h.message


def test_failures_are_plain_messages_and_never_show_the_key(base):
    with pytest.raises(AiError, match="refused the API key"):
        provider(base, api_key="wrong-secret-key").complete(conversation("s", "u"))
    with pytest.raises(AiError, match="does not know the model 'other'"):
        provider(base, model="other").complete(conversation("s", "u"))
    FC.STATE["mode"] = "busy"
    with pytest.raises(AiError, match="busy"):
        provider(base).complete(conversation("s", "u"))
    FC.STATE["mode"] = "broken"
    with pytest.raises(AiError, match="had a problem"):
        provider(base).complete(conversation("s", "u"))
    down = provider(f"http://127.0.0.1:{_free_port()}/v1", api_key="SECRET-KEY", timeout_s=1)
    with pytest.raises(AiError) as e:
        down.complete(conversation("s", "u"))
    assert "Cannot reach the AI server" in str(e.value) and "SECRET-KEY" not in str(e.value)
    with pytest.raises(AiError, match="not set"):
        provider(base, model="").complete(conversation("s", "u"))
