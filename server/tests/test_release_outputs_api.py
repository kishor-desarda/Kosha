# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: the stamped PDF and release sheet made at each release, Uncontrolled when printed, and the superseded
band on older revisions (specification 7.3 and 8.3)."""
import io

import pytest

pdfium = pytest.importorskip("pypdfium2")


def _pdf(pages=1) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(842, 595)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _text(data: bytes) -> str:
    doc = pdfium.PdfDocument(data)
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


def test_release_outputs(kosha):
    admin = kosha.client()
    eng = kosha.add_user("ro-eng", "engineer", full_name="Asha Example")
    appr = kosha.add_user("ro-appr", "approver", full_name="Daniel Example")
    root = next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])
    name = "RO-100-001 Pump base.pdf"
    doc = eng.call("POST", "/api/documents/upload", files={"file": (name, _pdf(2))},
                   fields={"folder_id": root, "part_number": "RO-100-001", "description": "PUMP BASE",
                           "material": "S235"})["documents"][0]
    assert eng.call("GET", f"/api/documents/{doc['id']}/outputs")["outputs"] == []

    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released", "reason": "base drawing"})
    outs = {o["kind"]: o for o in eng.call("GET", f"/api/documents/{doc['id']}/outputs")["outputs"]}
    assert set(outs) == {"released_pdf", "release_sheet"}
    assert all(o["status"] == "ok" and o["revision"] == "A" and not o["superseded_by"] for o in outs.values())
    assert outs["released_pdf"]["filename"] == "RO-100-001 Pump base Rev A.pdf"

    data, ctype = eng.call("GET", f"/api/outputs/{outs['released_pdf']['id']}/file", raw=True)
    text = _text(data)
    assert ctype == "application/pdf" and text.count("Released — Rev A") == 2 and "Daniel Example" in text
    assert "Uncontrolled when printed" in text and "Asha Example" in text        # printed line and watermark
    sheet = _text(eng.call("GET", f"/api/outputs/{outs['release_sheet']['id']}/file", raw=True)[0])
    assert "Release sheet — RO-100-001 Rev A" in sheet and "PUMP BASE" in sheet and name in sheet

    # revision B: the outputs of A are superseded and say so; a download of the old version too
    eng.call("POST", f"/api/documents/{doc['id']}/revise")
    eng.call("POST", f"/api/documents/{doc['id']}/checkout")
    eng.call("POST", f"/api/documents/{doc['id']}/checkin", files={"file": (name, _pdf(1))},
             fields={"comment": "holes moved"})
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released", "reason": "holes moved"})
    listed = eng.call("GET", f"/api/documents/{doc['id']}/outputs")["outputs"]
    old = next(o for o in listed if o["kind"] == "released_pdf" and o["revision"] == "A")
    new = next(o for o in listed if o["kind"] == "released_pdf" and o["revision"] == "B")
    assert old["superseded_by"] == "B" and not new["superseded_by"]
    assert "Superseded — use Rev B" in _text(eng.call("GET", f"/api/outputs/{old['id']}/file", raw=True)[0])
    assert "Superseded" not in _text(eng.call("GET", f"/api/outputs/{new['id']}/file", raw=True)[0])
    assert "Superseded — use Rev B" in _text(eng.call("GET", f"/api/documents/{doc['id']}/file?version=1", raw=True)[0])

    # made again by an approver only; idempotent
    eng.call("POST", f"/api/documents/{doc['id']}/outputs", expect=403)
    again = appr.call("POST", f"/api/documents/{doc['id']}/outputs")["outputs"]
    assert len(again) == len(listed)


def test_outputs_without_pdf(kosha):
    """A file with no PDF gets a release sheet only, and a failed output never blocks the release."""
    admin = kosha.client()
    eng = kosha.add_user("ro2-eng", "engineer")
    appr = kosha.add_user("ro2-appr", "approver")
    root = next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])
    doc = eng.call("POST", "/api/documents/upload", files={"file": ("RO-200-001 Notes.txt", b"weld all round\n")},
                   fields={"folder_id": root, "part_number": "RO-200-001", "description": "NOTES",
                           "material": "S235"})["documents"][0]
    assert appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released", "reason": "notes"})["state"] \
        == "released"
    kinds = [o["kind"] for o in eng.call("GET", f"/api/documents/{doc['id']}/outputs")["outputs"]]
    assert kinds == ["release_sheet"]
