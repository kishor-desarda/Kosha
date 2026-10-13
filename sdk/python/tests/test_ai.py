# SPDX-License-Identifier: Apache-2.0
"""The AI provider contract, the fake provider for tests and the conformance check."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kosha_sdk import AddinContext, AddinError, Health  # noqa: E402
from kosha_sdk.ai import (AiError, AiNotSupported, AiProvider, Completion, Message, conversation,  # noqa: E402
                          extract_json)
from kosha_sdk.cli import main  # noqa: E402
from kosha_sdk.permissions import PERMISSIONS  # noqa: E402
from kosha_sdk.testing import FakeAiProvider, check_ai_provider, check_manifest  # noqa: E402


def test_messages_are_checked():
    m = Message("user", "Which bearing?")
    assert m.to_dict() == {"role": "user", "content": "Which bearing?"}
    with pytest.raises(ValueError, match="role"):
        Message("robot", "hello")
    with pytest.raises(ValueError, match="text"):
        Message("user", 42)
    assert [x.role for x in conversation("Be brief.", "Hi")] == ["system", "user"]


def test_completion_knows_when_it_was_cut():
    assert not Completion("ok").truncated
    assert Completion("half an ans", finish_reason="length").truncated


@pytest.mark.parametrize("text, value", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": [1, 2]}\n```', {"a": [1, 2]}),
    ('Here you are:\n```\n{"b": "x"}\n```\nAnything else?', {"b": "x"}),
    ('Sure! {"category": "GR", "why": "teeth"} Hope that helps.', {"category": "GR", "why": "teeth"}),
    ('[{"r": "R1"}]', [{"r": "R1"}]),
])
def test_extract_json_finds_the_object(text, value):
    assert extract_json(text) == value


def test_extract_json_says_when_there_is_none():
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("I am not sure.")


def test_errors_are_addin_errors():
    assert issubclass(AiError, AddinError) and issubclass(AiNotSupported, AiError)


def test_embed_is_optional():
    class TextOnly(AiProvider):
        def health(self):
            return Health.up("ok")

        def complete(self, messages, *, max_tokens=512, temperature=0.2, json_mode=False):
            return Completion("OK", model="m")

    p = TextOnly(AddinContext("com.example.text-only"))
    with pytest.raises(AiNotSupported, match="embeddings"):
        p.embed(["x"])
    assert check_ai_provider(p) == []


def test_fake_provider_answers_from_a_script_and_keeps_the_calls():
    fake = FakeAiProvider(answers=["first", "second"])
    assert fake.complete(conversation("s", "u1")).text == "first"
    assert fake.complete(conversation("s", "u2"), max_tokens=20, json_mode=True).text == "second"
    assert [c["messages"][1].content for c in fake.calls] == ["u1", "u2"]
    assert fake.calls[1]["max_tokens"] == 20 and fake.calls[1]["json_mode"]
    with pytest.raises(AiError, match="no answers left"):
        fake.complete(conversation("s", "u3"))

    echo = FakeAiProvider(answers=lambda msgs: msgs[-1].content.upper())
    r = echo.complete(conversation("s", "hello"))
    assert r.text == "HELLO" and r.model == "fake-model" and r.usage["completion_tokens"] == 1
    cut = FakeAiProvider(answers=Completion("par", finish_reason="length"))
    assert cut.complete(conversation("s", "u")).truncated
    v = FakeAiProvider().embed(["a", "a", "b"])
    assert len(v) == 3 and v[0] == v[1] != v[2] and len(v[0]) == 8


def test_fake_provider_can_fail():
    fake = FakeAiProvider()
    fake.fail_with = AiError("The service is down.")
    assert not fake.health().ok
    with pytest.raises(AiError, match="down"):
        fake.complete(conversation("s", "u"))


def test_conformance_check_passes_the_fake_and_names_problems():
    assert check_ai_provider(FakeAiProvider()) == []
    assert check_ai_provider(FakeAiProvider(embedding_size=None)) == []
    broken = FakeAiProvider()
    broken.fail_with = AiError("Model not found.")
    problems = check_ai_provider(broken)
    assert problems == ["health() reports a problem, so the other checks were skipped: Model not found."]

    class WrongError(FakeAiProvider):
        def complete(self, messages, **kw):
            raise AddinError("generic")

    assert any("must raise AiError" in p for p in check_ai_provider(WrongError()))

    class NotACompletion(FakeAiProvider):
        def complete(self, messages, **kw):
            return "just text"

    assert any("must return a Completion" in p for p in check_ai_provider(NotACompletion()))


def test_ai_provider_needs_its_permission(tmp_path):
    assert "ai:provide" in PERMISSIONS
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "kosha-addin.yaml").write_text(
        "id: com.example.my-model\nname: My model\nversion: 0.1.0\npublisher: Example\nlicence: Apache-2.0\n"
        "kosha: '>=0.5'\nkind: [ai-provider]\nentry_points: {server: 'my_model.provider:Provider'}\n")
    assert check_manifest(folder) == ["an ai-provider add-in needs the permission ai:provide"]


def test_ai_provider_template_creates_a_valid_addin(tmp_path):
    assert main(["new", "ai-provider", "local-model", "--publisher", "com.example", "--dest", str(tmp_path)]) == 0
    folder = tmp_path / "local-model"
    assert check_manifest(folder) == []
    sys.path.insert(0, str(folder))
    try:
        from local_model.provider import Provider as Generated
        p = Generated(AddinContext("com.example.local-model", settings={}))
        assert not p.health().ok
        with pytest.raises(AiError, match="not set"):
            p.complete(conversation("s", "u"))
    finally:
        sys.path.remove(str(folder))
