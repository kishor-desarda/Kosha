# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AI engine end to end (specification 14.2 to 14.6), with a fake AI provider add-in (no model, no network):
a model for each job with a fallback, one retry of an unusable answer, the call log with costs, the monthly
budget with its alerts, local-only data, and the jobs drawing check, duplicate explanation, title-block reading
and speech to text."""
import time

import pytest

from ai_fake import FAKE, make_fake_provider, requests_made, set_mode
from harness import KoshaServer
from kosha.cad import dxfwriter as W


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("addins")
    make_fake_provider(root)
    s = KoshaServer(env={"KOSHA_ADDIN_DIR": str(root)}).start()
    admin = s.client()
    admin.call("POST", f"/api/addins/{FAKE}/enable")
    admin.call("PUT", "/api/ai/settings", {"provider": FAKE, "features": {k: True for k in (
        "classify", "changelog", "ask", "drawing_check", "duplicates", "ocr", "voice")}})
    yield s
    s.stop()


def _poll(client, sug, timeout=15):
    deadline = time.time() + timeout
    while sug["status"] == "pending" and time.time() < deadline:
        time.sleep(0.2)
        sug = client.call("GET", f"/api/ai/suggestions/{sug['id']}")
    return sug


def _root(c) -> int:
    return next(f["id"] for f in c.call("GET", "/api/folders")["folders"] if not f["parent_id"])


def _upload(c, name, data, **fields):
    return c.call("POST", "/api/documents/upload", files={"file": (name, data)},
                  fields={"folder_id": _root(c), **fields})["documents"][0]


def _pdf(lines):
    """A one-page PDF with text in the bottom-right corner, written by hand."""
    stream = "BT /F1 10 Tf 420 80 Td " + " ".join(f"({x}) Tj 0 -14 Td" for x in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def _item(c, pn):
    return next(i for i in c.call("GET", f"/api/items?q={pn}")["items"] if i["part_number"] == pn)


def test_model_per_job_fallback_retry_and_the_call_log(server):
    admin, eng = server.client(), server.add_user("ae-eng", "engineer")
    _upload(eng, "AE-100-001 Shaft.dxf", W.shaft(values={"PART_NO": "AE-100-001"}), part_number="AE-100-001",
            description="SHAFT D80", material="C45")
    it = _item(eng, "AE-100-001")

    # each job names its own model; providers that can switch models get it
    admin.call("PUT", "/api/ai/settings", {"jobs": {"classify": {"model": "fake-small"}},
                                           "prices": {"fake-small": {"in": 1000, "out": 2000}}})
    s = _poll(eng, eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}))
    assert s["status"] == "ready" and s["model"] == "fake-small" and requests_made(server)[-1]["model"] == "fake-small"

    # the main model is down: the fallback answers, and says so
    admin.call("PUT", "/api/ai/settings", {"jobs": {"classify": {"model": "fake-down", "fallback_model": "fake-big"}}})
    eng.call("POST", f"/api/ai/suggestions/{s['id']}/dismiss")
    f = _poll(eng, eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}))
    assert f["status"] == "ready" and f["model"] == "fake-big" and "fallback" in f["result"]["fallback"]

    # one unusable answer is asked for again; the person never sees it
    admin.call("PUT", "/api/ai/settings", {"jobs": {"classify": {"model": "", "fallback_model": ""}}})
    eng.call("POST", f"/api/ai/suggestions/{f['id']}/dismiss")
    set_mode(server, "garbage-once")
    r = _poll(eng, eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}))
    assert r["status"] == "ready", r

    # the log: every call with its job, model, tokens and cost, never the content
    u = admin.call("GET", "/api/ai/usage")
    job = next(j for j in u["jobs"] if j["job"] == "classify")
    assert job["calls"] == 5 and job["failed"] == 1 and job["fallbacks"] == 1 and job["cost"] > 0
    assert next(a for a in u["acceptance"] if a["feature"] == "classify")["dismissed"] == 2
    with server.db() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(ai_calls)")]
    assert "content" not in cols and "prompt" not in " ".join(cols).replace("prompt_tokens", "")
    admin.call("GET", "/api/ai/usage?month=26-10", expect=400)

    # bad settings are refused in plain words
    for bad in ({"jobs": {"weather": {}}}, {"jobs": {"classify": {"colour": "red"}}},
                {"jobs": {"classify": {"timeout_s": 2}}}, {"monthly_budget": -1}, {"prices": {"m": {"in": "x"}}},
                {"local_provider": "com.example.none"}, {"local_only": {"folders": [99999]}}):
        admin.call("PUT", "/api/ai/settings", bad, expect=400)


def test_budget_alerts_and_stop(server):
    admin, eng = server.client(), server.add_user("ab-eng", "engineer")
    _upload(eng, "AB-100-001 Plate.dxf", W.flange(100, 80, 4, 10, 40, values={}), part_number="AB-100-001",
            description="PLATE", material="S235")
    it = _item(eng, "AB-100-001")
    spent = admin.call("GET", "/api/ai/usage")["spent"]
    admin.call("PUT", "/api/ai/settings", {"monthly_budget": round(spent + 0.01, 2),
                                           "prices": {"fake-e2e": {"in": 100000, "out": 100000}}})
    s = _poll(eng, eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}))
    assert s["status"] == "ready"
    notes = [n["title"] for n in admin.call("GET", "/api/notifications")["notifications"] if "AI budget" in n["title"]]
    assert any("is used up" in n for n in notes) and any("50% used" in n for n in notes), notes
    eng.call("POST", f"/api/ai/suggestions/{s['id']}/dismiss")
    err = eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}, expect=409)
    assert err["code"] == "ai_budget" and "your work is safe" in err["error"].lower()
    assert admin.call("GET", "/api/ai/usage")["percent"] >= 100
    admin.call("PUT", "/api/ai/settings", {"stop_at_budget": False})
    assert _poll(eng, eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}))["status"] == "ready"
    admin.call("PUT", "/api/ai/settings", {"monthly_budget": 0, "stop_at_budget": True, "prices": {}})


def test_local_only_parts_never_leave_without_a_local_server(server):
    admin, eng = server.client(), server.add_user("al-eng", "engineer")
    _upload(eng, "AL-100-001 Secret housing.dxf", W.bearing_housing(values={"PART_NO": "AL-100-001"}),
            part_number="AL-100-001", description="SECRET HOUSING", material="FG 260")
    it = _item(eng, "AL-100-001")
    with server.db() as c:
        c.execute("UPDATE items SET restricted = 1 WHERE id = ?", (it["id"],))
    n = len(requests_made(server))
    err = eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}, expect=409)
    assert err["code"] == "ai_local_only" and len(requests_made(server)) == n
    a = _poll(eng, eng.call("POST", "/api/ai/ask", {"question": "What is AL-100-001 made of?"}))
    assert "local only" in a["result"]["left_out"] and all(r["label"][:10] != "AL-100-001" for r in a["result"]["records"])

    # with a model server inside the company, they go there (here the same fake add-in plays it)
    admin.call("PUT", "/api/ai/settings", {"local_provider": FAKE, "local_only": {"categories": ["xx"]}})
    assert admin.call("GET", "/api/ai/settings")["settings"]["local_only"]["categories"] == ["XX"]
    s = _poll(eng, eng.call("POST", "/api/ai/classify", {"subject": "item", "id": it["id"]}))
    assert s["status"] == "ready"
    sent = admin.call("GET", f"/api/ai/requests/{s['id']}/sent")["sent"]
    assert sent["local_only"] is True
    admin.call("PUT", "/api/ai/settings", {"local_provider": ""})
    with server.db() as c:
        c.execute("UPDATE items SET restricted = 0 WHERE id = ?", (it["id"],))


def test_drawing_check_rules_then_ai(server):
    eng = server.add_user("dc-eng", "engineer")
    good = _upload(eng, "DC-100-001 Bearing housing.dxf",
                   W.bearing_housing(values={"PART_NO": "DC-100-001", "DESCRIPTION": "BEARING HOUSING",
                                             "MATERIAL": "FG 260", "REV": "Rev. A"}),
                   part_number="DC-100-001", description="BEARING HOUSING", material="FG 260")
    r = eng.call("GET", f"/api/documents/{good['id']}/drawing-check")
    by = {c["key"]: c for c in r["checks"]}
    assert by["part_number"]["ok"] and by["revision"]["ok"] and by["material"]["ok"] and by["dimensions"]["ok"]
    assert by["drawn_by"]["ok"] is False and "who drew it" in by["drawn_by"]["text"]
    assert by["tolerance"]["ok"] is False and "ISO 2768" in by["tolerance"]["text"]
    assert "_texts" not in r and r["problems"] == 3

    wrong = _upload(eng, "DC-100-002 Cover.dxf",
                    W.flange(values={"PART_NO": "DC-100-099", "MATERIAL": "S275JR", "REV": "C"}),
                    part_number="DC-100-002", description="COVER", material="S235")
    by = {c["key"]: c for c in eng.call("GET", f"/api/documents/{wrong['id']}/drawing-check")["checks"]}
    assert "says DC-100-099" in by["part_number"]["text"] and by["part_number"]["severity"] == "problem"
    assert "part says S235" in by["material"]["text"] and "revision A" in by["revision"]["text"]

    s = _poll(eng, eng.call("POST", "/api/ai/drawing-check", {"id": good["id"]}))
    assert s["status"] == "ready" and s["result"]["findings"] == [
        {"severity": "medium", "text": "No surface finish is given for the bore.", "evidence": "drawing texts"}]
    assert s["result"]["rules"]["problems"] == 3


def test_duplicate_explanation(server):
    eng = server.add_user("dx-eng", "engineer")
    for pn, mat in (("DX-100-001", "FG 260"), ("DX-100-002", "S275JR")):
        _upload(eng, f"{pn} Housing.dxf", W.bearing_housing(values={"PART_NO": pn, "MATERIAL": mat}),
                part_number=pn, description="BEARING HOUSING", material=mat)
    eng.call("POST", "/api/common-parts/analyze", {"threshold": 0.9, "kinds": ["geometry"]})
    for _ in range(300):
        run = eng.call("GET", "/api/common-parts/runs/latest")["run"]
        if run["status"] != "running":
            break
        time.sleep(0.1)
    g = next(g for g in eng.call("GET", "/api/common-parts/groups?kind=geometry")["groups"]
             if any(m["document"]["part_number"] == "DX-100-002"
                    for m in eng.call("GET", f"/api/common-parts/groups/{g['id']}")["members"]))
    line = eng.call("GET", f"/api/common-parts/groups/{g['id']}/why")["line"]
    assert "material differs" in line, line
    s = _poll(eng, eng.call("POST", "/api/ai/duplicates", {"id": g["id"]}))
    assert s["status"] == "ready" and s["result"]["interchangeable"] == "likely"
    assert s["result"]["line"].startswith("Both are bearing housings") and s["result"]["rules"] == line


def test_title_block_reading_sends_only_the_corner_and_fills_in_on_accept(server):
    eng = server.add_user("oc-eng", "engineer")
    doc = _upload(eng, "OC-100-001 Cover scan.pdf", _pdf(["PART NO OC-100-001", "MATERIAL EN-GJL-250", "REV B"]),
                  part_number="OC-100-001", description="COVER", material="S235")
    s = _poll(eng, eng.call("POST", "/api/ai/ocr", {"id": doc["id"]}))
    assert s["status"] == "ready", s
    req = requests_made(server)[-1]
    assert req["images"] and req["images"][0].startswith("data:image/png;base64,")
    res = s["result"]
    assert res["read"]["part_number"] == "OC-100-001" and res["read"]["scale"] == "1:2"
    assert {f["key"]: f["suggested"] for f in res["fields"]} == {"description": "PUMP COVER DN80",
                                                                  "material": "EN-GJL-250"}
    assert any("revision B" in c for c in res["checks"])
    eng.call("POST", f"/api/ai/suggestions/{s['id']}/accept", {"fields": {"material": "EN-GJL-250"}})
    assert _item(eng, "OC-100-001")["material"] == "EN-GJL-250"
    dxf = _upload(eng, "OC-100-002 Plate.dxf", W.flange(values={}), part_number="OC-100-002", description="PLATE",
                  material="S235")
    assert "scanned drawings" in eng.call("POST", "/api/ai/ocr", {"id": dxf["id"]}, expect=400)["error"]


def test_speech_to_text(server):
    eng = server.add_user("vo-eng", "engineer")
    r = eng.call("POST", "/api/ai/transcribe", files={"file": ("q.webm", b"\x1aE\xdf\xa3 fake sound", "audio/webm")},
                 fields={"language": "hi"})
    assert r == {"text": "show drawing of MX-100-020"}
    eng.call("POST", "/api/ai/transcribe", files={"file": ("q.txt", b"hello", "text/plain")}, expect=400)
    eng.call("POST", "/api/ai/transcribe", fields={"language": "hi"}, expect=400)
    assert any(j["job"] == "voice" for j in server.client().call("GET", "/api/ai/usage")["jobs"])
