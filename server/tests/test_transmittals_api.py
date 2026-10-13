# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: vendor packages and transmittals (record, sheet, share link, superseded warning), Pack and Go, and
projects made from ERP sales orders (specification 6.2, 6.8 and 6.9)."""
import io
import zipfile

import pytest
from harness import Client

pdfium = pytest.importorskip("pypdfium2")


def _pdf() -> bytes:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(842, 595)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _text(data: bytes) -> str:
    doc = pdfium.PdfDocument(data)
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


def _upload(c, root, name, data):
    r = c.call("POST", "/api/documents/upload", files={"file": (name, data)},
               fields={"folder_id": root, "part_number": name.split()[0], "description": "BASE PLATE",
                       "material": "S235"})
    assert not r["errors"], r
    return r["documents"][0]


def test_vendor_package(kosha):
    admin = kosha.client()
    eng = kosha.add_user("tr-eng", "engineer", full_name="Asha Example")
    other = kosha.add_user("tr-other", "engineer")
    appr = kosha.add_user("tr-appr", "approver")
    root = next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])
    pdf = _upload(eng, root, "TP-100-001 Base plate.pdf", _pdf())
    notes = _upload(eng, root, "TP-100-001 Cutting notes.txt", b"plasma cut, deburr\n")
    eng.call("POST", "/api/transmittals", {"document_ids": [pdf["id"]], "recipient": "Example Cutting"}, expect=409)
    for d in (pdf, notes):
        appr.call("POST", f"/api/documents/{d['id']}/state", {"to": "released", "reason": "for the vendor"})

    t = eng.call("POST", "/api/transmittals", {"document_ids": [pdf["id"]], "recipient": "Ravi, Example Cutting",
                                               "purpose": "Quotation", "order_ref": "PO-26-0107", "days": 30})["transmittal"]
    assert t["number"].startswith("TR-") and t["link"] == "active" and t["status"] == "open"
    assert sorted(f["filename"] for f in t["files"]) == ["TP-100-001 Base plate.pdf", "TP-100-001 Cutting notes.txt"]
    sheet = _text(eng.call("GET", f"/api/transmittals/{t['id']}/sheet", raw=True)[0])
    assert t["number"] in sheet and "PO-26-0107" in sheet and "TP-100-001 Cutting notes.txt" in sheet
    assert [x["id"] for x in eng.call("GET", f"/api/transmittals?document_id={notes['id']}")["transmittals"]] == [t["id"]]
    other.call("GET", f"/api/transmittals/{t['id']}", expect=404)
    assert other.call("GET", "/api/transmittals")["transmittals"] == []

    # the vendor sees the files and the PDF stamped at release, marked with their name
    outside = Client(kosha.base)
    token = eng.call("GET", "/api/shares")["shares"][0]
    v = outside.call("GET", f"/api/share/{t['token']}")
    plate = next(f for f in v["files"] if f["id"] == pdf["id"])
    assert len(plate["outputs"]) == 1 and not plate["superseded"]
    stamped = _text(outside.call("GET", f"/api/share/{t['token']}/outputs/{plate['outputs'][0]['id']}", raw=True)[0])
    assert "Released — Rev A" in stamped and "Ravi, Example Cutting" in stamped and "Uncontrolled when printed" in stamped
    assert token["recipient"] == "Ravi, Example Cutting"

    # a newer revision while the order is open: the sender is told, the vendor's page says so
    eng.call("POST", f"/api/documents/{pdf['id']}/revise")
    eng.call("POST", f"/api/documents/{pdf['id']}/checkout")
    eng.call("POST", f"/api/documents/{pdf['id']}/checkin", files={"file": ("TP-100-001 Base plate.pdf", _pdf())},
             fields={"comment": "holes"})
    appr.call("POST", f"/api/documents/{pdf['id']}/state", {"to": "released", "reason": "holes"})
    t2 = eng.call("GET", f"/api/transmittals/{t['id']}")["transmittal"]
    assert t2["superseded"][0]["new_revision"] == "B" and t2["superseded"][0]["sent_revision"] == "A"
    with kosha.db() as con:
        assert con.execute("SELECT COUNT(*) FROM notifications WHERE title LIKE ?",
                           (f"{t['number']}: TP-100-001 Base plate.pdf has a newer revision",)).fetchone()[0] == 1
    v = outside.call("GET", f"/api/share/{t['token']}")
    assert next(f for f in v["files"] if f["id"] == pdf["id"])["superseded"]
    again = _text(outside.call("GET", f"/api/share/{t['token']}/outputs/{plate['outputs'][0]['id']}", raw=True)[0])
    assert "Superseded — ask for the new drawing" in again

    # closing ends the link
    other.call("POST", f"/api/transmittals/{t['id']}/close", expect=404)
    closed = eng.call("POST", f"/api/transmittals/{t['id']}/close")["transmittal"]
    assert closed["status"] == "closed" and closed["link"] == "withdrawn"
    outside.call("GET", f"/api/share/{t['token']}", expect=410)


def test_pack_and_go(kosha):
    admin = kosha.client()
    eng = kosha.add_user("pg-eng", "engineer")
    root = next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])
    _upload(eng, root, "PG-100-001 Plate.txt", b"plate\n")
    _upload(eng, root, "PG-100-002 Rib.txt", b"rib\n")
    bid = eng.call("POST", "/api/boms", {"part_number": "PG-100-000", "description": "FRAME"})["bom_id"]
    eng.call("PUT", f"/api/boms/{bid}/lines", {"lines": [{"part_number": "PG-100-001", "qty": 1},
                                                          {"part_number": "PG-100-002", "qty": 4}]})
    data, ctype = eng.call("GET", f"/api/boms/{bid}/pack-and-go", raw=True)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(z.namelist()) == ["PG-100-001 Plate.txt", "PG-100-002 Rib.txt", "Transmittal list.csv"]
    assert z.read("PG-100-002 Rib.txt") == b"rib\n" and "PG-100-001 Plate.txt" in z.read("Transmittal list.csv").decode()
    assert eng.call("GET", f"/api/boms/{bid}/pack-and-go?released=1", expect=409)["code"] == "empty"


def test_project_from_sales_order(kosha):
    eng = kosha.add_user("so-eng", "engineer")
    r = eng.call("POST", "/api/projects/from-sales-order", {"sales_order": "SO-26-0042", "customer": "Example Foods",
                                                            "due_date": "2026-12-15"})
    p = r["project"]
    assert r["created"] and p["code"].startswith("PRJ-") and p["sales_order"] == "SO-26-0042"
    assert p["customer"] == "Example Foods" and p["due_date"] == "2026-12-15"
    again = eng.call("POST", "/api/projects/from-sales-order", {"sales_order": "so-26-0042"})
    assert not again["created"] and again["project"]["id"] == p["id"]
    folders = eng.call("GET", "/api/folders")["folders"]
    subs = sorted(f["name"] for f in folders if f["parent_id"] == p["folder_id"])
    assert subs == ["Customer documents", "Design", "GA and layout", "Manuals", "Purchase"]
    eng.call("POST", "/api/projects/from-sales-order", {"sales_order": " "}, expect=400)
