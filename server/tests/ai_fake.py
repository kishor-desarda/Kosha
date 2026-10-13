# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fake AI provider add-in for end-to-end and browser tests: answers like a well-behaved model, worked out from
Kosha's request, without any model or network. A file "mode" in its work folder makes it fail on purpose."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

FAKE = "com.example.fake-ai"

PROVIDER = '''
import json, re, time
from kosha_sdk.ai import AiError
from kosha_sdk.testing import FakeAiProvider


class Provider(FakeAiProvider):
    """Answers like a well-behaved model would, worked out from Kosha's request. A file "mode" changes that."""

    def __init__(self, ctx):
        super().__init__(ctx, self.answer, model="fake-e2e")

    def mode(self):
        p = self.ctx.workdir / "mode"
        return p.read_text().strip() if p.exists() else "ok"

    def complete(self, messages, **kw):
        mode = self.mode()
        if mode == "broken":
            raise AiError("The fake AI service is down.")
        if mode == "slow":
            time.sleep(3)
        with open(self.ctx.workdir / "requests.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"system": messages[0].content[:60], "user": messages[-1].content}) + "\\n")
        return super().complete(messages, **kw)

    def answer(self, messages):
        system, user = messages[0].content, messages[-1].content
        if self.mode() == "garbage":
            return "Sorry, no JSON today."
        if "part data tidy" in system:
            req = json.loads(user)
            desc = req["part"]["description"].upper()
            cats = [c["code"] for c in req["categories"]]
            cands = [c["part_number"] for c in req["standard_part_candidates"]]
            if self.mode() == "inventive":
                return json.dumps({"description": desc, "category": "ZZ", "material": None,
                                   "standard_part": "FST-MADE-UP-1", "reasons": {}})
            out = {"description": desc.replace("BLT HEX", "HEX BOLT").replace("M16 X 60", "M16X60"),
                   "category": "FST" if "BLT" in desc or "BOLT" in desc else ("SHF" if "SHAFT" in desc else None),
                   "material": "8.8 steel" if "BLT" in desc else "C45",
                   "standard_part": cands[0] if cands else None,
                   "reasons": {"description": "Abbreviation spelled out", "category": "It is a bolt"}}
            if out["category"] not in cats:
                out["category"] = None
            return "Here you are:\\n```json\\n" + json.dumps(out) + "\\n```"
        if "change logs" in system:
            return json.dumps({"what": "- Hub seat from 110 to 115 (measured).", "why": "Fretting at the hub seat.",
                               "effect": ["Shafts in stock need rework"], "todo": "- Replace printed copies."})
        if "answer questions" in system:
            refs = re.findall(r"\\[(R\\d+)\\] (.*)", user)
            if not refs:
                return json.dumps({"answer": "I don't know.", "known": False, "cited": []})
            ref, line = refs[0]
            return json.dumps({"answer": f"From the records: {line[:60]} [{ref}].", "known": True, "cited": [ref]})
        return "OK"
'''


def make_fake_provider(root: Path) -> Path:
    """Write the add-in into root/fake-ai (point KOSHA_ADDIN_DIR at root). It waits for the admin's approval."""
    folder = root / "fake-ai"
    (folder / "fake_ai_addin").mkdir(parents=True)
    (folder / "kosha-addin.yaml").write_text(textwrap.dedent(f"""\
        id: {FAKE}
        name: Fake AI
        version: 1.0.0
        publisher: Example
        licence: Apache-2.0
        kosha: ">=0.5,<2.0"
        kind: [ai-provider]
        entry_points: {{server: "fake_ai_addin.main:Provider"}}
        permissions: [ai:provide]
        """))
    (folder / "fake_ai_addin" / "__init__.py").write_text("")
    (folder / "fake_ai_addin" / "main.py").write_text(PROVIDER)
    return folder


def workdir(server) -> Path:
    return server.data_dir / "data" / "addins" / FAKE


def set_mode(server, mode: str) -> None:
    workdir(server).mkdir(parents=True, exist_ok=True)
    (workdir(server) / "mode").write_text(mode)


def requests_made(server) -> list[dict]:
    p = workdir(server) / "requests.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
