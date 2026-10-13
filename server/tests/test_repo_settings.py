# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repository's description and topics file (.github/repository.json) and its check."""
import importlib.util

from kosha import config

_spec = importlib.util.spec_from_file_location("repo_settings", config.REPO_DIR / "scripts" / "repo_settings.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

GOOD = {"description": "Open-source PLM", "homepage": "", "topics": ["plm", "bill-of-materials"]}


def test_the_repository_file_follows_githubs_rules():
    assert rs.problems(rs.load()) == []


def test_check_finds_what_github_would_refuse():
    assert rs.problems(GOOD) == []
    assert rs.problems({**GOOD, "description": ""}) == ["description is missing"]
    assert "GitHub allows 350" in rs.problems({**GOOD, "description": "x" * 351})[0]
    assert rs.problems({**GOOD, "topics": ["PLM"]})[0].startswith("topic 'PLM'")
    assert rs.problems({**GOOD, "topics": ["plm", "plm"]}) == ["a topic is listed twice"]
    assert "GitHub allows 20" in rs.problems({**GOOD, "topics": [f"t{i}" for i in range(21)]})[0]
    assert rs.problems({**GOOD, "homepage": "http://example.com"}) == ["homepage must be empty or start with https://"]
    assert rs.problems({**GOOD, "colour": "red"}) == ["unknown settings: colour"]


def test_apply_without_a_token_changes_nothing_and_says_what_to_do(monkeypatch, capsys):
    monkeypatch.delenv("REPO_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rs, "apply", lambda *a: (_ for _ in ()).throw(AssertionError("must not call GitHub")))
    assert rs.main(["--apply"]) == 0
    assert "REPO_ADMIN_TOKEN is not set" in capsys.readouterr().out


def test_apply_sends_description_and_topics(monkeypatch):
    calls = []
    monkeypatch.setattr(rs, "_call", lambda method, url, token, body: calls.append((method, url, body)))
    rs.apply(GOOD, "owner/repo", "secret")
    assert calls == [("PATCH", "https://api.github.com/repos/owner/repo", {"description": "Open-source PLM", "homepage": ""}),
                     ("PUT", "https://api.github.com/repos/owner/repo/topics", {"names": ["plm", "bill-of-materials"]})]
