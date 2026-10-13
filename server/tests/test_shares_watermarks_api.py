# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: watermarked PDF downloads, share links for people outside the company, and secrets kept encrypted
(specification 15.3 and 15.4)."""
import io
import json

import pytest
from harness import Client

pdfium = pytest.importorskip("pypdfium2")


def _pdf(pages=1) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _text(data: bytes) -> str:
    doc = pdfium.PdfDocument(data)
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


def _released_pdf(kosha, admin, eng, name="SH-100-001 Datasheet.pdf"):
    root = next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])
    r = eng.call("POST", "/api/documents/upload", files={"file": (name, _pdf(2))},
                 fields={"folder_id": root, "part_number": name.split()[0], "description": "DATASHEET",
                         "material": "S235"})
    assert not r["errors"], r
    doc = r["documents"][0]
    return doc


def test_watermarked_downloads(kosha):
    admin = kosha.client()
    eng = kosha.add_user("wm-eng", "engineer", full_name="Asha Example")
    appr = kosha.add_user("wm-appr", "approver")
    doc = _released_pdf(kosha, admin, eng, "WM-100-001 Datasheet.pdf")
    plain, _ = eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)
    assert "Company confidential" not in _text(plain)          # in work: unchanged, so it can be edited
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released", "reason": "datasheet"})
    marked, ctype = eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)
    text = _text(marked)
    assert ctype == "application/pdf" and text.count("Asha Example") == 2 and "Company confidential" in text
    admin.call("PUT", "/api/settings", {"watermark_text": "Demo Engineering internal"})
    assert "Demo Engineering internal" in _text(eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)[0])
    admin.call("PUT", "/api/settings", {"watermark_downloads": False})
    assert "Asha Example" not in _text(eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)[0])
    admin.call("PUT", "/api/settings", {"watermark_downloads": True})


def test_share_links(kosha):
    admin = kosha.client()
    eng = kosha.add_user("sh-eng", "engineer")
    other = kosha.add_user("sh-other", "engineer")
    appr = kosha.add_user("sh-appr", "approver")
    doc = _released_pdf(kosha, admin, eng)
    assert eng.call("POST", "/api/shares", {"document_ids": [doc["id"]], "recipient": "Ravi, Example Castings"},
                    expect=409)["code"] == "not_released"
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released", "reason": "datasheet"})
    assert "who" in eng.call("POST", "/api/shares", {"document_ids": [doc["id"]]}, expect=400)["error"]
    assert eng.call("POST", "/api/shares", {"document_ids": [doc["id"]], "recipient": "x", "days": 400},
                    expect=400)
    s = eng.call("POST", "/api/shares", {"document_ids": [doc["id"]], "recipient": "Ravi, Example Castings",
                                         "days": 7, "note": "For the quotation"})
    token = s["token"]
    assert s["state"] == "active" and s["files"][0]["filename"] == doc["filename"] and "token_hash" not in s

    # the recipient opens it without an account; the PDF carries their name
    outside = Client(kosha.base)
    v = outside.call("GET", f"/api/share/{token}")
    assert v["recipient"] == "Ravi, Example Castings" and v["files"][0]["revision"] == "A" and not v["view_only"]
    data, ctype = outside.call("GET", f"/api/share/{token}/files/{doc['id']}/download", raw=True)
    assert ctype == "application/pdf" and "Ravi, Example Castings" in _text(data)
    outside.call("GET", f"/api/share/{token}x", expect=404)
    outside.call("GET", f"/api/share/{token}/files/999/download", expect=404)

    # only its maker (or an admin) sees and withdraws it; withdrawn links stop at once
    assert other.call("GET", "/api/shares")["shares"] == []
    other.call("POST", f"/api/shares/{s['id']}/withdraw", expect=403)
    listed = eng.call("GET", "/api/shares")["shares"][0]
    assert listed["opened_count"] == 1 and listed["download_count"] == 1
    eng.call("POST", f"/api/shares/{s['id']}/withdraw")
    assert outside.call("GET", f"/api/share/{token}", expect=410)["code"] == "share_withdrawn"

    # a password and view-only: preview yes, download no
    p = eng.call("POST", "/api/shares", {"document_ids": [doc["id"]], "recipient": "Supplier QA",
                                         "password": "castings-2026", "view_only": True})
    t2 = p["token"]
    assert outside.call("GET", f"/api/share/{t2}") == {"password": True, "recipient": "Supplier QA"}
    outside.call("GET", f"/api/share/{t2}?key=forged", expect=401)
    assert outside.call("POST", f"/api/share/{t2}/unlock", {"password": "wrong-one"}, expect=400)["code"] == "bad_password"
    key = outside.call("POST", f"/api/share/{t2}/unlock", {"password": "castings-2026"})["key"]
    assert outside.call("GET", f"/api/share/{t2}?key={key}")["view_only"] is True
    outside.call("GET", f"/api/share/{t2}/files/{doc['id']}/download?key={key}", expect=403)

    # a newer revision is flagged; an expired link ends
    eng.call("POST", f"/api/documents/{doc['id']}/revise")
    with kosha.db() as con:
        con.execute("UPDATE documents SET state = 'released' WHERE id = ?", (doc["id"],))
    s3 = Client(kosha.base).call("GET", f"/api/share/{t2}?key={key}")
    assert s3["files"][0]["superseded"]
    with kosha.db() as con:
        con.execute("UPDATE share_links SET expires_on = '2000-01-01' WHERE id = ?", (p["id"],))
    assert outside.call("GET", f"/api/share/{t2}?key={key}", expect=410)["code"] == "share_expired"
    with kosha.db() as con:
        actions = {r[0] for r in con.execute("SELECT action FROM audit WHERE entity = 'share'").fetchall()}
    assert {"share_create", "share_open", "share_download", "share_withdraw"} <= actions


def test_secrets_are_stored_encrypted(kosha):
    admin = kosha.client()
    hook = admin.call("POST", "/api/webhooks", {"name": "Screens", "url": "http://127.0.0.1:9/x",
                                                "events": ["item.created"]})["webhook"]
    with kosha.db() as con:
        stored = con.execute("SELECT secret FROM webhooks WHERE id = ?", (hook["id"],)).fetchone()[0]
    assert stored.startswith("enc1:") and hook["secret"] not in stored


def test_vault_round_trip(kdb):
    from kosha import vault
    sealed = vault.seal("erp-api-secret")
    assert vault.sealed(sealed) and "erp-api-secret" not in sealed
    assert vault.unseal(sealed) == "erp-api-secret" and vault.unseal("legacy plain") == "legacy plain"
    assert vault.seal(sealed) == sealed and vault.seal("") == ""
    broken = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
    with pytest.raises(vault.VaultError):
        vault.unseal(broken)
    from kosha import db
    db.set_setting("smtp", {"host": "x", "password": "old-plain"})
    assert vault.seal_existing() >= 1
    assert json.loads(db.scalar("SELECT value_json FROM settings WHERE key = 'smtp'"))["password"].startswith("enc1:")
    from kosha import mailer
    assert mailer.settings()["password"] == "old-plain"
