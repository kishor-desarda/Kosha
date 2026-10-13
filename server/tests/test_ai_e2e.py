# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI assistance end to end, with the Demo Engineering data and a fake AI provider add-in (no model, no network).

Off by default; the admin chooses a provider and features; suggestions change nothing until a person accepts
them (audited); Ask Kosha only sees what the asking person may open; a failing provider leaves work untouched.
"""
import json
import time

import pytest

from ai_fake import FAKE, make_fake_provider, requests_made, set_mode
from harness import KoshaServer

OFFICIAL = "org.kosha.openai-compatible"
UNREACHABLE = "The AI assistant could not be reached; your work is safe. Try again later or ask your administrator."


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("addins")
    make_fake_provider(root)
    s = KoshaServer(demo=True, env={"KOSHA_ADDIN_DIR": str(root)}).start()
    yield s
    s.stop()


def _item(client, pn):
    return next(i for i in client.call("GET", f"/api/items?q={pn}")["items"] if i["part_number"] == pn)


def _poll(client, sug, timeout=15):
    deadline = time.time() + timeout
    while sug["status"] == "pending" and time.time() < deadline:
        time.sleep(0.2)
        sug = client.call("GET", f"/api/ai/suggestions/{sug['id']}")
    return sug


def test_ai_is_off_until_the_admin_switches_it_on(server):
    asha = server.client("asha")
    st = asha.call("GET", "/api/ai/status")
    assert st == {"on": False, "features": {"classify": False, "changelog": False, "ask": False}, "provider": "",
                  "why": ""}
    r = asha.call("POST", "/api/ai/ask", {"question": "Which shaft?"}, expect=409)
    assert r["code"] == "ai_off" and "switched off" in r["error"]
    asha.call("POST", "/api/ai/classify", {"subject": "item", "id": 1}, expect=409)
    asha.call("GET", "/api/ai/settings", expect=403)

    admin = server.client()
    s = admin.call("GET", "/api/ai/settings")
    assert s["settings"]["provider"] == "" and not any(s["settings"]["features"].values())
    assert s["settings"]["send_properties"] and not s["settings"]["send_file_text"] and not s["settings"]["send_people"]
    prov = {p["id"]: p for p in s["providers"]}
    assert prov[OFFICIAL]["status"] == "enabled" and not prov[OFFICIAL]["ready"]
    assert "Model name" in prov[OFFICIAL]["why"]                  # installed, but sends nothing until set up
    assert prov[FAKE]["status"] == "awaiting_approval" and not prov[FAKE]["ready"]
    assert admin.call("PUT", "/api/ai/settings", {"provider": "com.example.nothing"}, expect=400)["error"]
    admin.call("PUT", "/api/ai/settings", {"features": {"weather": True}}, expect=400)
    admin.call("PUT", "/api/ai/settings", {"timeout_s": 5}, expect=400)

    # a chosen provider that is not ready yet: the features stay off, and the admin is told why
    admin.call("PUT", "/api/ai/settings", {"provider": FAKE, "features": {"classify": True, "changelog": True,
                                                                          "ask": True}})
    st = asha.call("GET", "/api/ai/status")
    assert not st["on"] and "not ready" in st["why"]

    admin.call("POST", f"/api/addins/{FAKE}/enable")
    st = asha.call("GET", "/api/ai/status")
    assert st["on"] and st["features"] == {"classify": True, "changelog": True, "ask": True}
    assert st["provider"] == "Fake AI"
    t = admin.call("POST", "/api/ai/test")
    assert t["ok"] and '"OK"' in t["message"]
    audit = admin.call("GET", "/api/audit?limit=20")["entries"]
    assert any(e["action"] == "ai_settings" and FAKE in e["details"] for e in audit)


def test_classification_is_a_suggestion_until_someone_accepts_it(server):
    asha, daniel, tom = server.client("asha"), server.client("daniel"), server.client("tom")
    admin = server.client()
    p1001 = _item(asha, "P-1001")                                  # "BLT HEX M16 X 60 8.8", released, bought out
    tom.call("POST", "/api/ai/classify", {"subject": "item", "id": p1001["id"]}, expect=403)

    sug = _poll(asha, asha.call("POST", "/api/ai/classify", {"subject": "item", "id": p1001["id"]}))
    assert sug["status"] == "ready" and sug["model"] == "fake-e2e", sug
    res = sug["result"]
    fields = {f["key"]: f for f in res["fields"]}
    assert fields["description"]["suggested"] == "HEX BOLT M16X60 8.8"
    assert fields["description"]["current"] == "BLT HEX M16 X 60 8.8"
    assert fields["category"]["suggested"] == "FST" and fields["category"]["note"].startswith("Fasteners")
    assert fields["material"]["note"].startswith("Not used on any part")      # not in any list: flagged
    assert res["standard_part"] and "M16" in res["standard_part"]["part_number"].upper()

    # pressing the button again asks nobody: the same suggestion comes back
    again = asha.call("POST", "/api/ai/classify", {"subject": "item", "id": p1001["id"]})
    assert again["id"] == sug["id"]
    assert sum(1 for r in requests_made(server) if "part data tidy" in r["system"]) == 1

    # nothing was written by the suggestion
    assert _item(asha, "P-1001")["description"] == "BLT HEX M16 X 60 8.8"

    # what was sent: the part, the allowed lists, nothing from files (not allowed by default)
    sent = admin.call("GET", f"/api/ai/requests/{sug['id']}/sent")["sent"]
    user_msg = json.loads(sent["messages"][1]["content"])
    assert user_msg["part"]["description"] == "BLT HEX M16 X 60 8.8" and user_msg["part"]["make_or_buy"] == "buy"
    assert "title_blocks" not in user_msg["part"] and {"code": "FST", "name": "Fasteners (bought out)"} in \
        user_msg["categories"]
    asha.call("GET", f"/api/ai/requests/{sug['id']}/sent", expect=403)

    # a released part: an engineer may not change its description, so nothing is changed at all
    r = asha.call("POST", f"/api/ai/suggestions/{sug['id']}/accept",
                  {"fields": {"description": "HEX BOLT M16X60 8.8", "category": "FST"}}, expect=409)
    assert "Nothing was changed" in r["error"]
    assert json.loads(_item(asha, "P-1001")["props_json"]).get("category") is None
    asha.call("POST", f"/api/ai/suggestions/{sug['id']}/accept", {"fields": {"weight": "3"}}, expect=400)

    # the approver accepts the category as suggested and the description with an edit of his own
    done = daniel.call("POST", f"/api/ai/suggestions/{sug['id']}/accept",
                       {"fields": {"description": "HEX BOLT M16 X 60 8.8", "category": "FST"}})
    assert done["decision"] == "edited"
    it = _item(asha, "P-1001")
    assert it["description"] == "HEX BOLT M16 X 60 8.8" and json.loads(it["props_json"])["category"] == "FST"
    assert it["material"] == ""                                       # not ticked, not changed
    daniel.call("POST", f"/api/ai/suggestions/{sug['id']}/accept", {"fields": {"category": "FST"}}, expect=409)
    audit = admin.call("GET", f"/api/audit?entity=item&entity_id={it['id']}")["entries"]
    accept = next(e for e in audit if e["action"] == "ai_accept")
    assert accept["user"] == "Daniel Okafor" and f"#{sug['id']} accepted with changes" in accept["details"]
    assert "description" in accept["details"] and "category" in accept["details"]

    # a new request after the decision asks again (the facts changed)
    fresh = _poll(asha, asha.call("POST", "/api/ai/classify", {"subject": "item", "id": p1001["id"]}))
    assert fresh["id"] != sug["id"]
    asha.call("POST", f"/api/ai/suggestions/{fresh['id']}/dismiss")
    assert asha.call("GET", f"/api/ai/suggestions/{fresh['id']}")["decision"] == "dismissed"


def test_answers_outside_the_lists_are_ignored_and_properties_stay_home_when_not_allowed(server):
    asha, admin = server.client("asha"), server.client()
    key = _item(asha, "MX-100-050")                                  # SHAFT KEY 28X16X100, C45
    set_mode(server, "inventive")
    try:
        sug = _poll(asha, asha.call("POST", "/api/ai/classify", {"subject": "item", "id": key["id"]}))
    finally:
        set_mode(server, "ok")
    res = sug["result"]
    assert not any(f["key"] == "category" for f in res["fields"]) and res["standard_part"] is None
    assert any("'ZZ'" in x for x in res["ignored"]) and any("FST-MADE-UP-1" in x for x in res["ignored"])

    admin.call("PUT", "/api/ai/settings", {"send_properties": False})
    try:
        hub = _item(asha, "MX-100-021")
        sug = _poll(asha, asha.call("POST", "/api/ai/classify", {"subject": "item", "id": hub["id"]}))
        part = json.loads(admin.call("GET", f"/api/ai/requests/{sug['id']}/sent")["sent"]["messages"][1]["content"])["part"]
        assert set(part) == {"part_number", "description", "files"}
    finally:
        admin.call("PUT", "/api/ai/settings", {"send_properties": True})


def test_change_log_draft_is_editable_and_its_use_is_audited(server):
    asha, admin = server.client("asha"), server.client()
    co = next(c for c in asha.call("GET", "/api/changes")["changes"] if c["title"].startswith("Increase agitator"))
    server.client("tom").call("POST", "/api/ai/changelog", {"subject": "change_order", "id": co["id"]}, expect=403)
    sug = _poll(asha, asha.call("POST", "/api/ai/changelog", {"subject": "change_order", "id": co["id"],
                                                              "note": "Fretting seen at two sites"}))
    assert sug["status"] == "ready", sug
    sec = sug["result"]["sections"]
    assert sec["what"].startswith("- Hub seat") and sec["effect"] == "- Shafts in stock need rework"
    assert sug["result"]["from_rules"] == []
    sent = admin.call("GET", f"/api/ai/requests/{sug['id']}/sent")["sent"]["messages"][1]["content"]
    assert "Change order ECO-0001" in sent and "Fretting seen at two sites" in sent
    assert "Asha" not in sent                                        # people's names are not sent by default
    with server.db() as con:
        gens = [r[0] for r in con.execute("SELECT generator FROM changelog_drafts WHERE subject = 'change_order' AND "
                                          "subject_id = ?", (co["id"],)).fetchall()]
    assert f"ai:{FAKE}" in gens

    # nothing is saved until the engineer saves; the save names the draft, and the audit log says so
    assert asha.call("GET", f"/api/changes/{co['id']}/changelog")["saved"] is None
    sec = dict(sec, why="Fretting at the hub seat, seen at two sites.")
    asha.call("PUT", f"/api/changes/{co['id']}/changelog", {"note": "Fretting seen at two sites", "sections": sec,
                                                            "ai_suggestion_id": sug["id"]})
    assert asha.call("GET", f"/api/ai/suggestions/{sug['id']}")["decision"] == "edited"
    audit = admin.call("GET", f"/api/audit?entity=change_order&entity_id={co['id']}")["entries"]
    assert any(e["action"] == "ai_accept" and f"#{sug['id']} accepted with changes" in e["details"] for e in audit)


def test_ask_kosha_answers_from_records_the_person_may_open(server):
    asha, daniel, tom, admin = server.client("asha"), server.client("daniel"), server.client("tom"), server.client()
    a = _poll(tom, tom.call("POST", "/api/ai/ask", {"question": "Which material is the agitator shaft MX-100-020 made of?"}))
    assert a["status"] == "ready" and a["result"]["known"], a
    src = a["result"]["sources"]
    assert src and src[0]["ref"] == "R1" and src[0]["link"].startswith("#/")
    assert "[R1]" in a["result"]["answer"]
    asha.call("GET", f"/api/ai/suggestions/{a['id']}", expect=403)             # answers are private
    admin.call("GET", f"/api/ai/suggestions/{a['id']}", expect=403)

    # a restricted input (approvers only) reaches Daniel's question but never Asha's
    q = "What is the target price for Customer A?"
    mine = _poll(asha, asha.call("POST", "/api/ai/ask", {"question": q}))
    his = _poll(daniel, daniel.call("POST", "/api/ai/ask", {"question": q}))
    sent_asha = admin.call("GET", f"/api/ai/requests/{mine['id']}/sent")["sent"]["messages"][1]["content"]
    sent_daniel = admin.call("GET", f"/api/ai/requests/{his['id']}/sent")["sent"]["messages"][1]["content"]
    assert "Customer A target price" in sent_daniel and "Customer A target price" not in sent_asha
    assert all(r["label"] != "Customer A target price" for r in mine["result"]["records"])

    # nothing in Kosha matches: a plain "I don't know", and the provider is not even asked
    n = len(requests_made(server))
    none = asha.call("POST", "/api/ai/ask", {"question": "zqxv wobblefrotz?"})
    assert none["status"] == "ready" and not none["result"]["known"]
    assert none["result"]["answer"].startswith("I don't know") and len(requests_made(server)) == n
    asha.call("POST", "/api/ai/ask", {"question": "x"}, expect=400)


def test_a_failing_provider_leaves_work_untouched_and_says_so_plainly(server):
    asha, admin = server.client("asha"), server.client()
    sleeve = _item(asha, "MX-100-021")
    set_mode(server, "broken")
    try:
        a = _poll(asha, asha.call("POST", "/api/ai/ask", {"question": "Who checked out the shaft sleeve?"}))
        c = _poll(asha, asha.call("POST", "/api/ai/classify", {"subject": "item", "id": sleeve["id"]}))
    finally:
        set_mode(server, "ok")
    assert a["status"] == "failed" and a["error"] == UNREACHABLE and "detail" not in a
    assert c["status"] == "failed" and c["error"] == UNREACHABLE
    assert "fake AI service is down" in admin.call("GET", "/api/ai/settings")["requests"][0]["message"]
    assert _item(asha, "MX-100-021")["description"] == sleeve["description"]
    # a failed request is not reused: "Try again" asks again
    ok = _poll(asha, asha.call("POST", "/api/ai/classify", {"subject": "item", "id": sleeve["id"]}))
    assert ok["status"] == "ready" and ok["id"] != c["id"]

    set_mode(server, "garbage")
    try:
        g = _poll(asha, asha.call("POST", "/api/ai/changelog", {"subject": "change_order", "id": 1, "note": "garbage"}))
    finally:
        set_mode(server, "ok")
    assert g["status"] == "failed" and "could not read the answer" in g["error"]

    set_mode(server, "slow")
    try:
        p = asha.call("POST", "/api/ai/ask", {"question": "Which bearing housing is the drive end?", "wait": 0})
        assert p["status"] == "pending"
        assert _poll(asha, p)["status"] == "ready"
    finally:
        set_mode(server, "ok")


def test_switching_a_feature_off_hides_it_again(server):
    admin, asha = server.client(), server.client("asha")
    admin.call("PUT", "/api/ai/settings", {"features": {"ask": False}})
    st = asha.call("GET", "/api/ai/status")
    assert st["features"] == {"classify": True, "changelog": True, "ask": False}
    asha.call("POST", "/api/ai/ask", {"question": "Which shaft?"}, expect=409)
    admin.call("PUT", "/api/ai/settings", {"provider": ""})
    assert not asha.call("GET", "/api/ai/status")["on"]
