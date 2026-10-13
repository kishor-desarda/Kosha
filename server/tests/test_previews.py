# SPDX-License-Identifier: AGPL-3.0-or-later
"""Previews of non-CAD files (specification 8): text, CSV, ZIP, e-mail, Word, Excel, PowerPoint, STEP and video.
Nothing in a file is run or unpacked; long files are cut and say so."""
import io
import json
import zipfile
from email.message import EmailMessage

import openpyxl

from kosha import previews
from kosha.cad import process as cadproc
from test_diff import _docx


def _summary(name, data):
    r = cadproc.process(name, data)
    assert r["viewer"] and r["viewer"][0] == "summary", r["meta"]
    assert r["preview"][0] == "svg" and r["preview"][1].startswith(b"<svg")
    return json.loads(r["viewer"][1])


def test_text_and_cnc_programs():
    s = _summary("notes.txt", "Line one\nLine two\twith tab\n".encode())
    assert s["type"] == "text" and s["lines"] == ["Line one", "Line two    with tab"] and s["title"] == "notes.txt"
    long = _summary("big.log", "\n".join(f"line {i}" for i in range(5000)).encode())
    assert len(long["lines"]) == previews.MAX_LINES and "first" in long["note"]
    assert _summary("o1000.nc", b"%\nO1000\nG21 G90\nM30\n%")["lines"][1] == "O1000"
    pretty = _summary("data.json", b'{"a":1,"b":[1,2]}')
    assert pretty["lines"][0] == "{" and '  "a": 1,' in pretty["lines"]


def test_csv_table():
    s = _summary("bom.csv", "Pos;Part;Qty\n1;MX-100-020;2\n2;MX-100-021;1\n".encode())
    assert s["type"] == "table" and s["rows"][1] == ["1", "MX-100-020", "2"]


def test_zip_is_listed_not_unpacked():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Pump/MX-100-020 Shaft.dxf", b"0\nEOF\n")
        z.writestr("../evil.txt", b"x")
    s = _summary("order.zip", buf.getvalue())
    assert s["type"] == "archive"
    assert [(e["name"], e["size"]) for e in s["entries"]] == [("Pump/MX-100-020 Shaft.dxf", 6), ("../evil.txt", 1)]
    assert "unsafe path" in s["entries"][1]["note"] and "2 files" in s["note"]
    r = cadproc.process("broken.zip", b"PK not really")
    assert r["viewer"] is None and "damaged" in r["meta"]["preview_note"]


def test_email_headers_text_and_attachments():
    m = EmailMessage()
    m["From"] = "Asha <asha@example.com>"
    m["To"] = "tom@example.com"
    m["Subject"] = "Shaft drawing rev B"
    m.set_content("Please check the hub seat.\nThanks")
    m.add_alternative("<p>Please <b>check</b></p><script>alert(1)</script>", subtype="html")
    m.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="MX-100-020.pdf")
    s = _summary("mail.eml", bytes(m))
    assert s["type"] == "email" and s["title"] == "Shaft drawing rev B"
    assert s["headers"]["From"] == "Asha <asha@example.com>" and s["lines"] == ["Please check the hub seat.", "Thanks"]
    assert s["attachments"] == [{"name": "MX-100-020.pdf", "size": 10}]
    h = EmailMessage()
    h["Subject"] = "Only HTML"
    h.set_content("<p>Hello<br>there</p><script>bad()</script>", subtype="html")
    assert _summary("h.eml", bytes(h))["lines"] == ["Hello", "there"]


def test_word_excel_and_powerpoint():
    assert _summary("spec.docx", _docx(["Scope", "Material C45"]))["lines"] == ["Scope", "Material C45"]
    wb = openpyxl.Workbook()
    wb.active.title = "BOM"
    wb.active.append(["Pos", "Part"])
    wb.active.append([1, "MX-100-020"])
    wb.create_sheet("Notes")
    buf = io.BytesIO()
    wb.save(buf)
    s = _summary("bom.xlsx", buf.getvalue())
    assert s["type"] == "table" and s["rows"] == [["Pos", "Part"], ["1", "MX-100-020"]]
    assert [x["title"] for x in s["sections"]] == ["BOM", "Notes"] and "2 sheets" in s["note"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, texts in ((1, ["Mixer MX-100", "New shaft"]), (2, ["Costs &amp; dates"])):
            z.writestr(f"ppt/slides/slide{n}.xml", "<p:sld>" + "".join(
                f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts) + "</p:sld>")
    s = _summary("review.pptx", buf.getvalue())
    assert s["sections"] == [{"title": "Slide 1: Mixer MX-100", "lines": ["New shaft"]},
                             {"title": "Slide 2: Costs & dates", "lines": []}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/slides/slide1.xml", '<!DOCTYPE x [<!ENTITY a "b">]><p:sld/>')
    assert "document type" in cadproc.process("bad.pptx", buf.getvalue())["meta"]["preview_note"]


def test_step_header():
    step = (b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Shaft'),'2;1');\n"
            b"FILE_NAME('MX-100-020.step','2026-10-01T10:00:00',('asha'),('Demo Engineering'),'x','CAD 2026','');\n"
            b"FILE_SCHEMA(('AP214IS'));\nENDSEC;\nDATA;\n#1=PRODUCT('MX-100-020','Shaft','',(#2));\n"
            b"#2=PRODUCT_CONTEXT('',#3,'mechanical');\nENDSEC;\nEND-ISO-10303-21;\n")
    r = cadproc.process("MX-100-020.step", step)
    s = json.loads(r["viewer"][1])
    assert s["type"] == "model" and "Author: asha" in s["lines"] and "Schema: AP214IS" in s["lines"]
    assert "- MX-100-020" in s["lines"] and r["meta"]["step_made_with"] == "CAD 2026"
    assert "not a STEP file" in cadproc.process("x.stp", b"hello")["meta"]["preview_note"]


def test_video_plays_from_the_file_itself():
    data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
    r = cadproc.process("test run.mp4", data)
    assert r["viewer"] == ("mp4", data) and r["preview"][0] == "svg" and b"Video" in r["preview"][1]
    assert cadproc.doc_type("test run.mp4") == "video" and cadproc.doc_type("a.eml") == "email"


def test_summaries_from_add_ins_are_cut_to_size():
    s = previews.clean({"type": "table", "rows": [list(range(100))] * 1000, "lines": ["x" * 5000], "evil": 1,
                        "headers": {f"k{i}": "v" for i in range(50)}})
    assert len(s["rows"]) == previews.MAX_ROWS and len(s["rows"][0]) == previews.MAX_COLS
    assert len(s["lines"][0]) == previews.MAX_CHARS and "evil" not in s and len(s["headers"]) == 12
    assert previews.clean({"type": "html", "lines": ["<script>"]}) is None
    assert b"&lt;script&gt;" in previews.card_svg({"type": "text", "lines": ["<script>"]}, "a<b>.txt")
