# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI assistance, unit level: settings, reading the model's answers strictly, retrieval for Ask Kosha, and the
request runner (idempotent, guarded, plain failures, nothing waits forever)."""
import json
import threading

import pytest

from kosha import ai, db
from kosha.web import ApiError

from kosha_sdk import AddinError  # noqa: E402
from kosha_sdk.ai import Completion  # noqa: E402

USER = {"id": 1, "role": "engineer", "full_name": "Asha Menon"}


@pytest.fixture
def kdb_ai(kdb, monkeypatch):
    """An empty database, one user, and a pretend provider "com.example.ai" that is ready."""
    db.insert("users", {"id": 1, "username": "asha", "full_name": "Asha Menon", "role": "engineer",
                        "password_hash": "x", "created_at": db.now()})
    db.insert("users", {"id": 2, "username": "tom", "full_name": "Tom Becker", "role": "viewer",
                        "password_hash": "x", "created_at": db.now()})
    monkeypatch.setattr(ai, "providers", lambda: [{"id": "com.example.ai", "name": "Pretend AI", "description": "",
                                                   "status": "enabled", "ready": True, "why": ""}])
    ai.save_settings({"provider": "com.example.ai", "features": {k: True for k in ai.FEATURES}}, None)
    return kdb


def _fake_call(answers, calls=None, gate=None):
    def call(addin_id, method, *args, timeout=None, **kw):
        if calls is not None:
            calls.append((addin_id, method, args, kw, timeout))
        if gate is not None:
            gate.wait(5)
        out = answers(args[0]) if callable(answers) else answers
        if isinstance(out, Exception):
            raise out
        return out
    return call


def _prepared(text="hello", feature="ask", shared=True, interpret=None):
    return ai.Prepared(feature, "question", None, "", ai._hash(text), ai.conversation("sys", text),
                       interpret or (lambda c: {"echo": c.text}), shared=shared, params={"question": text})


# ----------------------------------------------------------------------------------------- settings --

def test_everything_is_off_by_default(kdb):
    cfg = ai.settings()
    assert cfg["provider"] == "" and cfg["features"] == {k: False for k in ai.FEATURES}
    assert cfg["send_properties"] and not cfg["send_people"] and not cfg["send_file_text"]
    st = ai.status()
    assert st == {"on": False, "features": {k: False for k in ai.FEATURES}, "provider": "", "why": ""}
    with pytest.raises(ApiError) as e:
        ai.require("ask")
    assert e.value.status == 409 and e.value.code == "ai_off" and "Admin › AI" in e.value.message


def test_settings_are_checked(kdb_ai):
    with pytest.raises(ValueError, match="no AI provider add-in"):
        ai.save_settings({"provider": "com.example.other"}, None)
    with pytest.raises(ValueError, match="Unknown AI feature"):
        ai.save_settings({"features": {"poetry": True}}, None)
    with pytest.raises(ValueError, match="between 10 and 600"):
        ai.save_settings({"timeout_s": 1}, None)
    with pytest.raises(ValueError, match="whole number"):
        ai.save_settings({"max_records": "lots"}, None)
    cfg = ai.save_settings({"send_file_text": "yes", "features": {"ask": False}, "max_records": "5"}, 1)
    assert cfg["send_file_text"] is True and cfg["max_records"] == 5
    assert cfg["features"] == {**{k: True for k in ai.FEATURES}, "ask": False}
    assert ai.status()["features"]["ask"] is False and ai.status()["on"]
    row = db.one("SELECT * FROM audit WHERE action = 'ai_settings' ORDER BY id DESC LIMIT 1")
    assert "on: classify, changelog" in row["details"] and "send_file_text=yes" in row["details"]


def test_a_provider_that_is_not_ready_keeps_the_features_off(kdb_ai, monkeypatch):
    monkeypatch.setattr(ai, "providers", lambda: [{"id": "com.example.ai", "name": "Pretend AI", "description": "",
                                                   "status": "disabled", "ready": False,
                                                   "why": "Switched off under Admin › Add-ins."}])
    st = ai.status()
    assert not st["on"] and "Switched off" in st["why"]
    assert ai.feature_state("classify") == (False, "The AI provider is not ready: Switched off under Admin › Add-ins.")


# ------------------------------------------------------------------------------- reading the answers --

CATS = [{"code": "FST", "name": "Fasteners (bought out)"}, {"code": "SH", "name": "Shafts"}]
MATS = [{"code": "C45", "name": "C45 (1.0503)"}, {"code": "42CrMo4", "name": "42CrMo4 (1.7225)"}]
CANDS = [{"part_number": "FST-ISO4017-M16X60-8.8", "designation": "ISO 4017 M16x60 - 8.8",
          "description": "HEX SCREW M16X60 8.8", "existing": None}]
TARGET = {"item_id": 7, "document_id": None}


def test_classification_keeps_only_listed_new_values():
    current = {"description": "BLT HEX M16 X 60", "category": "", "material": "C45"}
    ans = {"description": "  HEX  BOLT M16X60 ", "category": "fasteners (bought out)", "material": "c45 (1.0503)",
           "standard_part": "fst-iso4017-m16x60-8.8", "reasons": {"category": "a bolt", "standard_part": "same size"}}
    r = ai.read_classification(ans, current, CATS, MATS, CANDS, TARGET)
    fields = {f["key"]: f for f in r["fields"]}
    assert fields["description"]["suggested"] == "HEX BOLT M16X60"
    assert fields["category"]["suggested"] == "FST" and fields["category"]["reason"] == "a bolt"
    assert "material" not in fields                                   # C45 is what it already is
    assert r["standard_part"]["part_number"] == "FST-ISO4017-M16X60-8.8" and r["standard_part"]["reason"] == "same size"
    assert not r["nothing"] and r["ignored"] == []


def test_classification_ignores_inventions():
    r = ai.read_classification({"category": "GEARS-ISH", "standard_part": "FST-INVENTED", "material": "Unobtainium",
                                "description": None, "reasons": "not a dict"},
                               {"description": "X", "category": "", "material": ""}, CATS, MATS, CANDS, TARGET)
    assert [f["key"] for f in r["fields"]] == ["material"]
    assert r["fields"][0]["note"].startswith("Not used on any part")
    assert r["standard_part"] is None and len(r["ignored"]) == 2
    nothing = ai.read_classification({}, {"description": "X"}, CATS, MATS, CANDS, TARGET)
    assert nothing["nothing"]
    # a file without a part: only its description can be suggested
    doc_only = ai.read_classification({"description": "Y", "category": "SH", "material": "C45"},
                                      {"description": "X"}, CATS, MATS, [], {"item_id": None, "document_id": 3})
    assert [f["key"] for f in doc_only["fields"]] == ["description"]


def test_keywords_keep_part_numbers_and_drop_filler():
    assert ai.keywords("Which bearings are used in MX-100-020, and what is the M16x60 bolt?") == [
        "bearing", "used", "mx-100-020", "m16x60", "bolt"]
    assert ai.keywords("the of and?") == []
    assert len(ai.keywords(" ".join(f"word{i}" for i in range(20)))) == 8


# --------------------------------------------------------------------------------------- retrieval --

def _records():
    now = db.now()
    fid = db.insert("folders", {"name": "Mixers", "created_at": now})
    sh = db.insert("items", {"part_number": "MX-100-020", "description": "AGITATOR SHAFT", "material": "42CrMo4",
                             "created_at": now, "updated_at": now})
    db.insert("items", {"part_number": "CP-80-001", "description": "PUMP CASING", "created_at": now, "updated_at": now})
    db.insert("documents", {"folder_id": fid, "filename": "MX-100-020 Agitator Shaft.dxf", "ext": "dxf",
                            "doc_type": "drawing", "item_id": sh, "part_number": "MX-100-020",
                            "description": "AGITATOR SHAFT", "created_at": now, "updated_at": now})
    db.insert("documents", {"folder_id": fid, "filename": "MX-100-099 Old Shaft.dxf", "ext": "dxf",
                            "doc_type": "drawing", "part_number": "MX-100-099", "description": "AGITATOR SHAFT OLD",
                            "deleted": 1, "created_at": now, "updated_at": now})
    db.insert("change_orders", {"number": "ECO-0001", "title": "Bigger agitator shaft hub seat", "requested_by": 1,
                                "reason": "Fretting", "created_at": now})
    for title, conf, by in (("Agitator benchmark", "normal", 2), ("Agitator target price", "restricted", 2)):
        db.insert("inputs", {"title": title, "kind": "other", "confidentiality": conf, "created_by": by,
                             "created_at": now, "updated_at": now})
    return sh


def test_retrieval_finds_records_and_respects_what_the_person_may_see(kdb_ai):
    sh = _records()
    hits = ai.retrieve(USER, "What material is the agitator shaft MX-100-020?")
    kinds = [(h["kind"], h["row"].get("part_number") or h["row"].get("number") or h["row"].get("title")) for h in hits]
    assert kinds[0] == ("item", "MX-100-020")
    assert ("document", "MX-100-020") in kinds and ("change_order", "ECO-0001") in kinds
    assert ("input", "Agitator benchmark") in kinds
    assert ("input", "Agitator target price") not in kinds            # restricted, and Asha is not allowed
    assert all(h["row"].get("part_number") != "MX-100-099" for h in hits)     # deleted files never
    admin = {"id": 9, "role": "admin"}
    assert ("input", "Agitator target price") in [(h["kind"], h["row"].get("title")) for h in
                                                 ai.retrieve(admin, "agitator target price")]
    assert ai.retrieve(USER, "the of and") == []
    assert not ai.can_read({"role": "nobody"}, "item", {"id": sh})


def test_question_context_obeys_the_send_settings(kdb_ai):
    _records()
    p = ai.prepare_question(USER, "Who asked for the bigger agitator shaft hub seat?")
    ctx = p.messages[1].content
    assert "[R1]" in ctx and "Asha" not in ctx and "material 42CrMo4" in ctx
    ai.save_settings({"send_people": True, "send_properties": False}, None)
    ctx = ai.prepare_question(USER, "Who asked for the bigger agitator shaft hub seat?").messages[1].content
    assert "requested by Asha Menon" in ctx and "material 42CrMo4" not in ctx
    empty = ai.prepare_question(USER, "zqxv?")
    assert empty.answer_now["known"] is False and empty.messages == []
    with pytest.raises(ApiError):
        ai.prepare_question(USER, "  ")


def test_answers_are_linked_to_the_records_cited(kdb_ai):
    _records()
    p = ai.prepare_question(USER, "agitator shaft material")
    r = p.interpret(Completion(json.dumps({"answer": "It is 42CrMo4 [R1]; see also [R9].", "known": True,
                                           "cited": ["R1", "R2"]})))
    assert [s["ref"] for s in r["sources"]] == ["R1", "R2"] and r["known"]      # R9 does not exist: dropped
    assert r["sources"][0]["link"].startswith("#/item/")
    plain = p.interpret(Completion("I don't know, the records do not say."))      # no JSON at all
    assert plain["known"] is False and plain["sources"] == []
    with pytest.raises(ValueError):
        p.interpret(Completion(json.dumps({"answer": ""})))


# ------------------------------------------------------------------------------------------ runner --

def test_requests_are_idempotent_and_guarded(kdb_ai, monkeypatch):
    calls = []
    monkeypatch.setattr(ai.registry, "call", _fake_call(Completion("hi", model="m1"), calls))
    a = ai.submit(USER, _prepared("same"), wait_s=5)
    assert a["status"] == "ready" and a["result"] == {"echo": "hi"} and a["model"] == "m1"
    b = ai.submit(USER, _prepared("same"), wait_s=5)
    assert b["id"] == a["id"] and len(calls) == 1
    addin, method, args, kw, timeout = calls[0]
    assert (addin, method, timeout) == ("com.example.ai", "complete", 90) and kw["json_mode"] is True
    # private requests (questions) are never shared between people
    tom = {"id": 2, "role": "viewer"}
    c = ai.submit(tom, _prepared("same", shared=False), wait_s=5)
    d = ai.submit(USER, _prepared("same", shared=False), wait_s=5)
    assert c["id"] != d["id"]
    with pytest.raises(ApiError) as e:
        ai.get(c["id"], USER)
    assert e.value.status == 403


def test_failures_are_plain_and_detailed_only_for_the_admin(kdb_ai, monkeypatch):
    monkeypatch.setattr(ai.registry, "call", _fake_call(AddinError("Model 'x' not found (HTTP 404).")))
    r = ai.submit(USER, _prepared("one", feature="classify"), wait_s=5)
    assert r["status"] == "failed" and r["error"] == ai.UNREACHABLE and "detail" not in r
    assert "HTTP 404" in ai.get(r["id"], {"id": 9, "role": "admin"})["detail"]
    monkeypatch.setattr(ai.registry, "call", _fake_call(Completion("not json")))
    bad = ai.submit(USER, _prepared("two", interpret=lambda c: ai._obj(c.text)), wait_s=5)
    assert bad["status"] == "failed" and bad["error"] == ai.UNREADABLE
    monkeypatch.setattr(ai.registry, "call", _fake_call("a plain string, not a Completion"))
    odd = ai.submit(USER, _prepared("three", feature="classify"), wait_s=5)
    assert odd["status"] == "failed" and "does not understand" in ai.get(odd["id"], {"id": 9, "role": "admin"})["detail"]
    monkeypatch.setattr(ai.registry, "call", _fake_call(Completion("cut", finish_reason="length")))
    cut = ai.submit(USER, _prepared("four"), wait_s=5)
    assert cut["status"] == "ready" and "cut off" in cut["result"]["note"]


def test_slow_answers_come_later_and_nobody_queues_without_limit(kdb_ai, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(ai.registry, "call", _fake_call(Completion("late"), gate=gate))
    first = ai.submit(USER, _prepared("q1"), wait_s=0)
    assert first["status"] == "pending"
    ai.submit(USER, _prepared("q2"), wait_s=0)
    ai.submit(USER, _prepared("q3"), wait_s=0)
    with pytest.raises(ApiError) as e:
        ai.submit(USER, _prepared("q4"), wait_s=0)
    assert e.value.status == 429 and "3 requests waiting" in e.value.message
    gate.set()
    ai._futures[first["id"]].result(5) if first["id"] in ai._futures else None
    for _ in range(50):
        if ai.get(first["id"], USER)["status"] == "ready":
            break
        threading.Event().wait(0.05)
    assert ai.get(first["id"], USER)["result"] == {"echo": "late"}


def test_requests_cut_off_by_a_restart_say_so(kdb_ai):
    sid = db.insert("ai_suggestions", {"dedupe_key": "k", "feature": "ask", "subject": "question", "status": "pending",
                                       "created_by": 1, "created_at": "2020-01-01T00:00:00+00:00"})
    r = ai.get(sid, USER)
    assert r["status"] == "failed" and r["error"] == ai.UNREACHABLE


def test_accepting_needs_a_ready_open_suggestion(kdb_ai):
    now = db.now()
    iid = db.insert("items", {"part_number": "P-1", "description": "BLT", "created_at": now, "updated_at": now})
    result = {"fields": [{"key": "description", "suggested": "BOLT"}, {"key": "category", "suggested": "FST"}],
              "standard_part": None, "target": {"item_id": iid, "document_id": None}}
    sid = db.insert("ai_suggestions", {"dedupe_key": "k", "feature": "classify", "subject": "item", "subject_id": iid,
                                       "status": "ready", "result_json": json.dumps(result), "created_by": 1,
                                       "created_at": now})
    with pytest.raises(ApiError, match="view-only"):
        ai.accept_classification({"id": 2, "role": "viewer"}, sid, {"category": "FST"})
    with pytest.raises(ApiError, match="Tick at least one"):
        ai.accept_classification(USER, sid, {})
    with pytest.raises(ApiError, match="cannot be empty"):
        ai.accept_classification(USER, sid, {"description": " "})
    with pytest.raises(ApiError, match="no standard part"):
        ai.accept_classification(USER, sid, {"category": "FST"}, mark_standard=True)
    r = ai.accept_classification(USER, sid, {"category": "FST"})
    assert r["decision"] == "accepted"
    it = db.one("SELECT * FROM items WHERE id = ?", (iid,))
    assert it["description"] == "BLT" and json.loads(it["props_json"])["category"] == "FST"
    with pytest.raises(ApiError, match="already accepted"):
        ai.accept_classification(USER, sid, {"category": "FST"})
    assert db.one("SELECT details FROM audit WHERE action = 'ai_accept'")["details"].endswith("accepted: category")
