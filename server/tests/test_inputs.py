# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inputs library: text extraction per format, the portable search index, access rules, de-duplication and
bulk actions, run in-process against a temporary data folder."""
import io
import zipfile

import pytest

from kosha import config, db, inputs, projects, store
from kosha.inputs import (SearchIndex, TextExtractor, Upload, atoms, library, normalise, run_isolated, stem,
                          tokens)
from kosha.web import ApiError


# ------------------------------------------------------------------ helpers --

@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BLOB_DIR", tmp_path / "blobs")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kosha.db")
    db.close_thread_connection()
    db.init()
    users = {}
    for name, role in (("admin", "admin"), ("eng", "engineer"), ("eng2", "engineer"), ("view", "viewer"),
                       ("appr", "approver")):
        uid = db.insert("users", {"username": name, "full_name": name.title(), "role": role, "password_hash": "x",
                                  "created_at": db.now()})
        users[name] = {"id": uid, "username": name, "role": role}
    now = db.now()
    for pn, desc in (("MX-100-020", "AGITATOR SHAFT"), ("GB-7", "HELICAL GEARBOX")):
        db.insert("items", {"part_number": pn, "description": desc, "created_at": now, "updated_at": now})
    yield users
    db.close_thread_connection()


def item_id(pn):
    return db.scalar("SELECT id FROM items WHERE part_number = ?", (pn,))


def up(name, data):
    return library.store_upload(Upload(name, io.BytesIO(data)))


def search(user, **p):
    return library.search(user, {k: str(v) for k, v in p.items()})


def titles(res):
    return [i["title"] for i in res["inputs"]]


def zip_bytes(parts: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in parts.items():
            z.writestr(name, content)
    return buf.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


# ------------------------------------------------------------------ tokenizer --

def test_part_numbers_and_sizes_stay_whole():
    t = list(tokens("Shaft MX-100-020 uses M16 x 60 bolts, Ø110 h6 seat, ⌀95 bore, steel 1.4401 and 42CrMo4; DN150/PN16."))
    for expected in ("mx-100-020", "m16x60", "ø110", "ø95", "1.4401", "42crmo4", "dn150/pn16", "h6"):
        assert expected in t, (expected, t)
    assert normalise("M16X60") == normalise("m16×60") == "m16x60"
    assert {"mx", "100", "020"} <= atoms("mx-100-020") and {"16", "60"} <= atoms("m16x60")
    assert atoms("ø110") == {"110"} and atoms("1.4401") == set()


def test_english_plurals_are_stemmed_and_short_words_kept():
    assert [stem(w) for w in ("pumps", "batteries", "boxes", "inches", "glasses", "bearings")] == \
        ["pump", "battery", "box", "inch", "glass", "bearing"]
    assert [stem(w) for w in ("class", "status", "analysis", "gas", "bus")] == ["class", "status", "analysis", "gas", "bus"]
    assert SearchIndex.query_terms("the Pumps and MX-100") == ["pump", "mx-100"]
    assert SearchIndex.query_terms("the") == ["the"]            # only stop words: keep them rather than nothing


def test_highlight_and_snippet_mark_the_matched_words():
    si = SearchIndex()
    segs = si.highlight("Agitator shaft MX-100-020 with Ø110 seat", si.query_terms("mx-100 110"))
    assert [s for s, hit in segs if hit] == ["MX-100-020", "Ø110"]
    text = ("Introduction. " * 80) + "Competitor B uses M16 x 60 bolts on all batteries. " + ("Filler. " * 80)
    snip = si.snippet(text, si.query_terms("m16x60 battery"))
    assert [s for s, hit in snip if hit] == ["M16 x 60", "batteries"]
    assert snip[0][0] == "… " and snip[-1][0] == " …"


# ------------------------------------------------------------------ extraction --

def test_docx_xlsx_pptx_are_read_with_or_without_the_office_libraries():
    ex = TextExtractor()
    docx_ = zip_bytes({"[Content_Types].xml": "<Types/>", "word/document.xml":
                       f'<w:document {W}><w:body><w:p><w:r><w:t>Noise below 75 dB</w:t></w:r><w:r><w:tab/>'
                       f'<w:t>for MX-100-020</w:t></w:r></w:p><w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:body></w:document>',
                       "word/footer1.xml": f'<w:ftr {W}><w:p><w:r><w:t>Customer A confidential</w:t></w:r></w:p></w:ftr>'})
    r = ex.run(docx_, "req.docx")
    assert (r.status, r.file_type) == ("done", "docx")
    assert "Noise below 75 dB\tfor MX-100-020\nSecond line" in r.text and "Customer A confidential" in r.text

    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Customer requirement: shaft Ø110 h6")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Seal", "Mechanical"
    buf = io.BytesIO()
    d.save(buf)
    r = ex.run(buf.getvalue(), "spec.docx")
    assert r.status == "done" and "Ø110 h6" in r.text and "Mechanical" in r.text

    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.title = "Agitators"
    wb.active.append(["Model", "Maker", "kW"])
    wb.active.append(["AX-12", "Competitor A", 18.5])
    wb.create_sheet("Prices").append(["Index", 108, True])
    buf = io.BytesIO()
    wb.save(buf)
    r = ex.run(buf.getvalue(), "bench.xlsx")
    assert r.status == "done" and r.pages == 2
    assert "Sheet: Agitators" in r.text and "AX-12\tCompetitor A\t18.5" in r.text and "Index\t108\tTRUE" in r.text

    pptx = pytest.importorskip("pptx")
    pr = pptx.Presentation()
    slide = pr.slides.add_slide(pr.slide_layouts[1])
    slide.shapes.title.text = "Test report TR-07"
    slide.placeholders[1].text = "Bearing temperature 68 °C"
    buf = io.BytesIO()
    pr.save(buf)
    r = ex.run(buf.getvalue(), "report.pptx")
    assert r.status == "done" and "Test report TR-07" in r.text and "68 °C" in r.text


def test_pdf_text_layer_and_thumbnail():
    ex = TextExtractor()
    r = ex.run(inputs._simple_pdf("Bearing 22220 EK", ["Dynamic load rating 425 kN"]), "datasheet.pdf")
    assert (r.status, r.file_type, r.pages) == ("done", "pdf", 1)
    assert "425 kN" in r.text
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buf = io.BytesIO()
    c = reportlab.Canvas(buf)
    c.drawString(72, 720, "Vibration test of pump CP-80: 2.8 mm/s")
    c.showPage()
    c.drawString(72, 720, "Page two")
    c.save()
    r = ex.run(buf.getvalue(), "test.pdf")
    assert r.status == "done" and r.pages == 2 and "2.8 mm/s" in r.text and "Page two" in r.text
    try:
        import PIL  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError:
        return
    assert r.thumb and r.thumb[0] == "png"                      # first page, reusing the Explorer preview code


def test_plain_text_html_email_and_unreadable_files():
    ex = TextExtractor()
    assert ex.run("Meeting: M16 x 60 bolts\n".encode("utf-16"), "notes.txt").text.startswith("Meeting")
    assert "MX-100-020,2" in ex.run(b"part,qty\nMX-100-020,2\n", "parts.csv").text
    html = ex.run(b"<html><head><style>x{}</style></head><body><h1>Spec</h1><script>bad()</script><p>Ra 0.8</p></body></html>",
                  "page.html")
    assert "Spec" in html.text and "Ra 0.8" in html.text and "bad()" not in html.text
    mail = ex.run(b"Subject: Noise limit\r\nFrom: Customer A <a@example.com>\r\n\r\nPlease keep below 75 dB(A).\r\n", "m.eml")
    assert mail.status == "done" and "Noise limit" in mail.text and "75 dB(A)" in mail.text
    old = ex.run(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, "old.xls")
    assert old.status == "skipped" and ".xlsx" in old.note
    broken = ex.run(b"PK\x03\x04 this is not a real zip", "broken.docx")
    assert broken.status == "failed" and "damaged" in broken.note
    binary = ex.run(bytes(range(256)) * 20, "firmware.bin")
    assert binary.status == "skipped" and "can't read text" in binary.note


def test_xml_document_type_declarations_are_refused():
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
            f'<w:document {W}><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>')
    r = TextExtractor().run(zip_bytes({"word/document.xml": bomb}), "bomb.docx")
    assert r.status == "failed" and "document type declaration" in r.note


def _text_image(text: str):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1000, 260), "white")
    try:
        font = ImageFont.load_default(size=56)
    except (TypeError, OSError):
        pytest.skip("Pillow without FreeType: no font large enough for OCR")
    ImageDraw.Draw(img).text((30, 90), text, fill="black", font=font)
    return img


def test_ocr_reads_photos_and_scanned_pdfs_and_can_be_switched_off():
    pytest.importorskip("PIL")
    if not inputs.ocr_status()[0]:
        pytest.skip("Tesseract is not installed")
    img = _text_image("NAMEPLATE CP-80 PUMP")
    png, pdf = io.BytesIO(), io.BytesIO()
    img.save(png, format="PNG")
    img.save(pdf, format="PDF")                                  # an image-only PDF, like a scan
    r = TextExtractor().run(png.getvalue(), "photo.png")
    assert (r.status, r.method) == ("done", "OCR") and "CP-80" in r.text and r.thumb and r.thumb[0] == "jpg"
    r = TextExtractor().run(pdf.getvalue(), "scan.pdf")
    assert r.status == "done" and r.method == "OCR (scanned PDF)" and "NAMEPLATE" in r.text
    off = TextExtractor({"ocr": False})
    r = off.run(png.getvalue(), "photo.png")
    assert r.status == "skipped" and "switched off" in r.note and r.thumb      # thumbnails still made
    assert off.run(pdf.getvalue(), "scan.pdf").status == "empty"


def test_isolated_reader_answers_and_is_stopped_after_its_time_limit(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("Competitor B datasheet, Ø100 shaft")
    r = run_isolated(str(f), "notes.txt", {"ocr": False}, timeout=60)
    assert r.status == "done" and "Ø100" in r.text
    slow = run_isolated(str(f), "notes.txt", {"ocr": False}, timeout=0.01)
    assert slow.status == "failed" and "took longer" in slow.note


# ------------------------------------------------------------------ library --

def test_identical_files_are_stored_and_read_once(lib):
    eng, eng2 = lib["eng"], lib["eng2"]
    data = b"Gearbox benchmark: Competitor A ratio 12.5"
    a = library.create(eng, {"title": "Benchmark A"}, [up("a.txt", data)])
    b = library.create(eng2, {"title": "Copy of the benchmark"}, [up("copy.txt", data)])
    sha = db.scalar("SELECT blob_sha FROM input_files WHERE input_id = ?", (a,))
    assert db.scalar("SELECT blob_sha FROM input_files WHERE input_id = ?", (b,)) == sha
    assert len([p for p in config.BLOB_DIR.rglob("*") if p.is_file()]) == 1          # one copy on disk
    assert db.scalar("SELECT COUNT(*) FROM input_content") == 1
    assert [d["id"] for d in library.duplicates_of(eng, sha, b)] == [a]
    assert library.worker.drain() == 1                                               # read once for both
    assert db.scalar("SELECT COUNT(DISTINCT sha) FROM input_text_terms") == 1
    assert set(titles(search(eng, q="ratio 12.5"))) == {"Benchmark A", "Copy of the benchmark"}
    with pytest.raises(ApiError, match="empty"):
        up("empty.txt", b"")


def test_search_covers_fields_text_parts_and_projects_and_ranks_title_first(lib):
    eng = lib["eng"]
    prj = projects.create(eng, {"code": "PRJ-2026-1", "name": "Mixer upgrade", "customer": "Customer A"})
    t1 = library.create(eng, {"title": "Gearbox noise test", "tags": ["acoustics"], "kind": "test_report"},
                        [up("t1.txt", b"Measured 72 dB at full load.")])
    t2 = library.create(eng, {"title": "Supplier catalogue", "source": "Supplier C", "kind": "supplier_datasheet",
                              "item_ids": [item_id("MX-100-020")], "project_ids": [prj["id"]]},
                        [up("cat.txt", b"Helical gearbox series with M16 x 60 foot bolts. Gearboxes ship in boxes.")])
    library.create(eng, {"title": "Unrelated note", "note": "Paint colour RAL 5010"})
    library.worker.drain()
    res = search(eng, q="gearbox")
    assert titles(res) == ["Gearbox noise test", "Supplier catalogue"]                # title beats file text
    assert res["inputs"][0]["score"] > res["inputs"][1]["score"]
    assert [s for s, hit in res["inputs"][0]["title_hl"] if hit] == ["Gearbox"]
    assert res["inputs"][1]["snippet_file"] == "cat.txt"
    assert titles(search(eng, q="gear")) == titles(res)                                # prefix search
    assert titles(search(eng, q="M16x60")) == ["Supplier catalogue"]                   # "M16 x 60" in the text
    assert titles(search(eng, q="box")) == ["Supplier catalogue"]                      # plural "boxes"
    assert titles(search(eng, q="MX-100-020")) == ["Supplier catalogue"]               # linked part number
    assert titles(search(eng, q="agitator shaft")) == ["Supplier catalogue"]           # part description
    assert titles(search(eng, q="PRJ-2026-1")) == ["Supplier catalogue"]               # project code
    assert titles(search(eng, q="customer a")) == ["Supplier catalogue"]               # project customer
    assert titles(search(eng, q="acoustics 72")) == ["Gearbox noise test"]             # tag + text, all terms must match
    assert titles(search(eng, q="RAL 5010")) == ["Unrelated note"]
    assert search(eng, q="gearbox paint")["total"] == 0
    # filters and sorting
    assert titles(search(eng, kind="test_report")) == ["Gearbox noise test"]
    assert titles(search(eng, type="note")) == ["Unrelated note"]
    assert titles(search(eng, project=prj["id"])) == ["Supplier catalogue"]
    assert titles(search(eng, item=item_id("MX-100-020"))) == ["Supplier catalogue"]
    assert titles(search(eng, tag="ACOUSTICS")) == ["Gearbox noise test"]
    assert titles(search(eng, sort="title", dir="asc")) == ["Gearbox noise test", "Supplier catalogue", "Unrelated note"]
    library.update(eng, t1, {"date": "2025-01-10"})
    library.update(eng, t2, {"date": "2025-06-01"})
    assert titles(search(eng, **{"from": "2025-01-01", "to": "2025-03-31"})) == ["Gearbox noise test"]
    facets = search(eng, q="gearbox")["facets"]
    assert {k["key"]: k["n"] for k in facets["kind"] if k["n"]} == {"test_report": 1, "supplier_datasheet": 1}
    # renaming the project re-indexes the inputs that belong to it
    projects.update(eng, prj["id"], {"code": "PRJ-2026-9"})
    assert titles(search(eng, q="PRJ-2026-9")) == ["Supplier catalogue"] and search(eng, q="PRJ-2026-1")["total"] == 0


def test_restricted_inputs_are_invisible_everywhere_to_people_without_access(lib):
    eng, eng2, view, appr, admin = (lib[k] for k in ("eng", "eng2", "view", "appr", "admin"))
    prj = projects.create(eng, {"code": "PRJ-9", "name": "Secret pricing"})
    secret = library.create(eng, {"title": "Target price Customer A", "confidentiality": "restricted",
                                  "access_roles": ["approver"], "access_users": [eng2["id"]], "tags": ["pricing"],
                                  "item_ids": [item_id("GB-7")], "project_ids": [prj["id"]]},
                            [up("price.txt", b"Target price 41000 per gearbox")])
    library.create(eng, {"title": "Public datasheet", "tags": ["datasheet"]}, [up("public.txt", b"Gearbox datasheet")])
    library.worker.drain()
    sha = db.scalar("SELECT blob_sha FROM input_files WHERE input_id = ?", (secret,))
    for who, sees in ((eng, True), (eng2, True), (appr, True), (admin, True), (view, False)):
        assert ("Target price Customer A" in titles(search(who))) is sees, who
        assert (search(who, q="41000")["total"] == 1) is sees
        assert (search(who)["total"] == 2) is sees
        assert any(t["key"] == "pricing" for t in library.tags(who)) is sees
        assert any(f["key"] == "pricing" for f in search(who)["facets"]["tag"]) is sees
        assert library.count_by(who, "project", [prj["id"]]).get(prj["id"], 0) == (1 if sees else 0)
        assert library.count_by(who, "item").get(item_id("GB-7"), 0) == (1 if sees else 0)
        if sees:
            assert library.detail(who, secret)["title"] == "Target price Customer A"
            assert library.file_row(who, secret, 1)["blob_sha"] == sha
        else:
            with pytest.raises(ApiError, match="not found"):
                library.detail(who, secret)
            with pytest.raises(ApiError, match="not found"):
                library.file_row(who, secret, 1)
    # a viewer uploading the same file is not told where the restricted copy lives
    assert library.duplicates_of(view, sha) == [] and library.duplicates_of(appr, sha)[0]["id"] == secret
    # only the person who added it (or an approver/admin) may change who sees it
    with pytest.raises(ApiError, match="who may see it"):
        library.update(eng2, secret, {"confidentiality": "normal"})
    library.update(eng, secret, {"access_roles": [], "access_users": []})
    assert search(eng2, q="41000")["total"] == 0 and search(eng, q="41000")["total"] == 1
    library.update(eng, secret, {"confidentiality": "normal"})
    assert search(view, q="41000")["total"] == 1


def test_bulk_tag_link_kind_delete_and_restore(lib):
    eng, eng2, view = lib["eng"], lib["eng2"], lib["view"]
    prj = projects.create(eng, {"code": "PRJ-B", "name": "Bulk"})
    ids = [library.create(eng, {"title": f"Photo {k}", "kind": "photo"}, [up(f"p{k}.txt", f"photo {k}".encode())])
           for k in range(3)]
    hidden = library.create(eng2, {"title": "Private", "note": "mine", "confidentiality": "restricted"})
    r = library.bulk(eng, [*ids, hidden, 999], "add_tags", {"tags": ["Site visit", "pump"]})
    assert r["done"] == 3 and {s["id"] for s in r["skipped"]} == {hidden, 999}
    assert search(eng, tag="site visit")["total"] == 3
    assert library.bulk(eng, ids[:1], "remove_tags", {"tags": ["PUMP"]})["done"] == 1
    assert search(eng, tag="pump")["total"] == 2
    library.bulk(eng, ids, "link", {"item_ids": [item_id("GB-7")], "project_ids": [prj["id"]]})
    assert search(eng, item=item_id("GB-7"))["total"] == 3 and search(eng, q="PRJ-B")["total"] == 3
    library.bulk(eng, ids[:2], "unlink", {"project_ids": [prj["id"]]})
    assert search(eng, project=prj["id"])["total"] == 1
    library.bulk(eng, ids, "set_kind", {"kind": "test_report"})
    assert search(eng, kind="test_report")["total"] == 3
    with pytest.raises(ApiError):
        library.bulk(view, ids, "add_tags", {"tags": ["x"]})                  # viewers can't change inputs
    r = library.bulk(eng2, ids, "delete", {})
    assert r["done"] == 0 and len(r["skipped"]) == 3                          # not theirs, not an approver
    assert library.bulk(eng, ids, "delete", {})["done"] == 3
    assert search(eng)["total"] == 0 and search(eng, deleted="1")["total"] == 3
    assert library.bulk(eng, ids, "restore", {})["done"] == 3 and search(eng)["total"] == 3
    assert db.scalar("SELECT COUNT(*) FROM input_files") == 3                 # nothing was ever removed


def test_files_can_be_added_removed_and_put_back_without_losing_them(lib):
    eng, eng2 = lib["eng"], lib["eng2"]
    iid = library.create(eng, {"title": "Benchmark with photos", "group": "1"},
                         [up("summary.txt", b"Competitor C summary"), up("photo1.txt", b"front view")])
    library.add_files(eng2, iid, [up("photo2.txt", b"side view")])
    library.worker.drain()
    d = library.detail(eng, iid)
    assert [f["filename"] for f in d["files"]] == ["summary.txt", "photo1.txt", "photo2.txt"]
    assert all(f["status"] == "done" for f in d["files"])
    library.set_file_removed(eng2, iid, 2, True)
    assert search(eng, q="front")["total"] == 0 and len(library.detail(eng2, iid)["files"]) == 2
    with pytest.raises(ApiError, match="put back"):
        library.set_file_removed(eng2, iid, 2, False)
    library.set_file_removed(eng, iid, 2, False)
    assert search(eng, q="front")["total"] == 1
    assert store.exists(db.scalar("SELECT blob_sha FROM input_files WHERE input_id = ? AND position = 2", (iid,)))
    single = library.create(eng, {"title": "One file"}, [up("one.txt", b"only")])
    with pytest.raises(ApiError, match="only file"):
        library.set_file_removed(eng, single, 1, True)          # an input never ends up empty


def test_validation_and_settings(lib):
    eng, admin = lib["eng"], lib["admin"]
    with pytest.raises(ApiError, match="file, a web link or a note"):
        library.create(eng, {"title": "Nothing"})
    with pytest.raises(ApiError, match="http"):
        library.create(eng, {"title": "Bad link", "url": "javascript:alert(1)"})
    with pytest.raises(ApiError, match="Unknown kind"):
        library.create(eng, {"title": "x", "note": "y", "kind": "nonsense"})
    with pytest.raises(ApiError, match="engineer"):
        library.create(lib["view"], {"title": "x", "note": "y"})
    iid = library.create(eng, {"url": "https://www.example.com/standard"})
    assert library.detail(eng, iid)["title"] == "www.example.com"
    s = library.save_settings(admin, {"kinds": [{"key": "competitor_benchmark", "label": "Benchmark"},
                                                {"label": "Field report"}], "ocr": False, "max_mb": 20})
    assert [k["key"] for k in s["kinds"]] == ["competitor_benchmark", "field_report", "other"]
    assert s["ocr"] is False and s["max_mb"] == 20
    library.create(eng, {"title": "Field visit", "note": "x", "kind": "field_report"})
    with pytest.raises(ApiError, match="still used"):
        library.save_settings(admin, {"kinds": [{"label": "Benchmark", "key": "competitor_benchmark"}]})
    assert library.reindex_all()["inputs"] == 2


def test_large_files_are_kept_but_not_read(lib):
    eng = lib["eng"]
    library.save_settings(lib["admin"], {"max_mb": 1})
    iid = library.create(eng, {"title": "Big scan"}, [up("big.txt", b"x" * (1024 * 1024 + 10))])
    library.worker.drain()
    f = library.detail(eng, iid)["files"][0]
    assert f["status"] == "skipped" and "Larger than 1 MB" in f["note"]
