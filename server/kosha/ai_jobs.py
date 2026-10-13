# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AI engine's further jobs (specification 14.3): drawing check, duplicate explanation, title-block reading
(OCR) and speech to text.

Each job does what it can without AI first and asks the AI provider only for the rest:

* **Drawing check.** Kosha's own rules read the title block and the drawing's texts (part number, description,
  material, revision, drawn by, scale, a general-tolerance note, dimensions) and say what is missing or wrong.
  They work with AI switched off. The AI job gets those results and, when the admin allows it, the drawing's
  texts, and points out what a checker would still question.
* **Duplicate explanation.** The rules compare the look-alike parts (size, holes, material, numbers) and write a
  plain line; the AI job turns the facts into one sentence on why they look alike and what differs.
* **Title-block reading.** Only the title-block corner of a scanned drawing (a PDF page or a photo) is sent, never
  the whole sheet (14.5). The answer is a suggestion; accepting it fills in the description and material like a
  classification. When no AI provider answers and Tesseract is installed, the server reads it instead.
* **Speech to text.** A short recording becomes the text of a question; nothing is kept.
"""
from __future__ import annotations

import base64
import io
import json
import re
import struct
import time
import zlib
from typing import Any

from . import ai, db, store
from .web import ApiError, bad, not_found

from kosha_sdk import AddinError  # noqa: E402
from kosha_sdk.ai import Completion, Message  # noqa: E402

MAX_AUDIO = 10 * 1024 * 1024
TOLERANCE_NOTE = re.compile(r"ISO\s*2768|IS\s*2102|DIN\s*ISO\s*2768|TOLERANC|UNLESS\s+(OTHERWISE\s+)?(STATED|SPECIFIED|"
                            r"NOTED)|GENERAL\s+TOL", re.I)
DRAWINGS = {".dxf"}


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).upper()


def _rev(s: Any) -> str:
    """'Rev. 2', 'REV-2' and '2' are the same revision."""
    return re.sub(r"^(REVISION|REV)\b[\s.:\-]*", "", _norm(s)).strip()


def _latest(doc: dict) -> dict:
    v = db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?", (doc["id"], doc["latest_version"]))
    if not v:
        raise not_found("The file has no stored version.")
    return v


# ------------------------------------------------------------------------------------------ drawing check --

def _materials() -> list:
    try:
        from .packs import PackConfig
        return PackConfig.current().dictionary("materials")
    except Exception:       # no packs: nothing to check the material against
        return []


def drawing_check(doc_id: int) -> dict:
    """Kosha's own checks of a drawing before approval. Every check says what it found, in plain words."""
    from .lifecycle import doc_or_404
    doc = doc_or_404(doc_id)
    v = _latest(doc)
    meta = json.loads(v["meta_json"] or "{}")
    tb = {k: str(val).strip() for k, val in (meta.get("title_block") or {}).items() if str(val).strip()}
    item = db.one("SELECT * FROM items WHERE id = ?", (doc["item_id"],)) if doc["item_id"] else None
    ext = "." + doc["filename"].rsplit(".", 1)[-1].lower() if "." in doc["filename"] else ""
    texts: list[str] = []
    dims = 0
    read = ext in DRAWINGS
    if read:
        try:
            from .diff.drawing import read as read_drawing
            d = read_drawing(store.read_bytes(v["blob_sha"]))
            texts = [t.text for t in d.texts if t.text and not t.dim][:400]
            dims = sum(1 for t in d.texts if t.dim)
        except Exception:      # a drawing Kosha cannot read: the title-block checks still run
            read = False
    checks: list[dict] = []

    def add(key: str, label: str, ok: bool | None, text: str, severity: str = "missing") -> None:
        checks.append({"key": key, "label": label, "ok": ok, "text": text, "severity": "" if ok else severity})

    add("title_block", "Title block", bool(tb), "Filled in." if tb else
        "No title block values were found. Fill in the title block, or check that it is a block with attributes.")
    pn = tb.get("part_number", "")
    if not pn:
        add("part_number", "Part number", False, "The title block shows no part number.")
    elif doc["part_number"] and _norm(pn) != _norm(doc["part_number"]):
        add("part_number", "Part number", False, f"The title block says {pn}, but the file is {doc['part_number']}.",
            "problem")
    else:
        add("part_number", "Part number", True, f"{pn} matches the file.")
    add("description", "Description", bool(tb.get("description")),
        f"“{tb['description']}”." if tb.get("description") else "The title block shows no description.")
    mat = tb.get("material", "")
    need_mat = not item or item["make_buy"] != "buy"
    if not mat:
        add("material", "Material", False if need_mat else None,
            "The title block shows no material." if need_mat else "Bought-out part: no material needed.")
    elif item and item["material"] and _norm(item["material"]) != _norm(mat):
        add("material", "Material", False, f"The drawing says {mat}, but the part says {item['material']}.", "problem")
    else:
        known = _materials()
        if known and not any(_norm(mat) in (_norm(c.code), _norm(c.name)) for c in known):
            add("material", "Material", False, f"{mat} is not in the materials list. Check the spelling or ask the "
                "data steward to add it.", "problem")
        else:
            add("material", "Material", True, f"{mat}.")
    rev = tb.get("revision", "")
    if not rev:
        add("revision", "Revision", False, "The title block shows no revision.")
    elif doc["revision"] and _rev(rev) != _rev(doc["revision"]):
        add("revision", "Revision", False, f"The title block says {rev}, but the file is at revision "
            f"{doc['revision']}. Update the title block or the revision table.", "problem")
    else:
        add("revision", "Revision", True, f"{rev} matches the file.")
    add("drawn_by", "Drawn by", bool(tb.get("drawn_by")),
        f"{tb['drawn_by']}." if tb.get("drawn_by") else "The title block does not say who drew it.")
    add("scale", "Scale", bool(tb.get("scale")), f"{tb['scale']}." if tb.get("scale") else
        "The title block shows no scale.")
    if read:
        note = next((t for t in texts if TOLERANCE_NOTE.search(t)), "")
        add("tolerance", "General tolerance note", bool(note), f"“{ai._clip(note, 120)}”." if note else
            "No general tolerance note (for example “ISO 2768-m”) was found on the drawing.")
        add("dimensions", "Dimensions", dims > 0, f"{dims} dimension{'s' if dims != 1 else ''} found." if dims else
            "No dimensions were found. A drawing for manufacture usually needs them.")
    else:
        why = "Kosha reads the drawing's texts in DXF files only." if ext not in DRAWINGS else \
            "Kosha could not read the drawing's texts."
        add("tolerance", "General tolerance note", None, why)
        add("dimensions", "Dimensions", None, why)
    problems = [c for c in checks if c["ok"] is False]
    return {"document_id": doc["id"], "filename": doc["filename"], "revision": doc["revision"], "checks": checks,
            "problems": len(problems), "title_block": tb, "texts_read": read,
            "summary": "Every check passed." if not problems else
            f"{len(problems)} thing{'s' if len(problems) != 1 else ''} to look at before approval.",
            "_texts": texts}


DRAWING_SYSTEM = (
    "You check engineering drawings before approval, like an experienced design checker. You get the part, the "
    "results of Kosha's own rule checks, and, when allowed, the texts and title block read from the drawing. "
    "Point out what a checker would still question that the rules did not already report: missing or unclear "
    "notes, surface finish, heat treatment, threads, welding symbols, inconsistent sizes, likely missing "
    "dimensions. Use only the facts given; never invent sizes. At most 6 findings. " + ai.JSON_RULE +
    ' Keys: "findings" (a list of objects with "severity" ("high", "medium" or "low"), "text" (one plain sentence) '
    'and "evidence" (the text or check it is based on)).'
)


def prepare_drawing_check(user: dict, doc_id: int) -> ai.Prepared:
    cfg = ai.settings()
    rules = drawing_check(doc_id)
    texts = rules.pop("_texts")
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    item = db.one("SELECT * FROM items WHERE id = ?", (doc["item_id"],)) if doc["item_id"] else None
    request: dict[str, Any] = {
        "part": {"part_number": doc["part_number"], "description": doc["description"], "revision": doc["revision"],
                 "file": doc["filename"]},
        "rule_checks": [{"check": c["label"], "result": "ok" if c["ok"] else "not checked" if c["ok"] is None
                         else "problem", "found": c["text"]} for c in rules["checks"]]}
    if cfg["send_properties"] and item:
        request["part"].update({"material": item["material"], "make_or_buy": item["make_buy"],
                                "type": item["item_type"]})
    if cfg["send_file_text"]:
        request["title_block"] = rules["title_block"]
        request["drawing_texts"] = [ai._clip(t, 120) for t in texts[:150]]
    messages = ai.conversation(DRAWING_SYSTEM, json.dumps(request, ensure_ascii=False, indent=1))

    def interpret(comp: Completion) -> dict:
        ans = ai._obj(comp.text)
        raw = ans.get("findings")
        if not isinstance(raw, list):
            raise ValueError("no list of findings")
        found = []
        for f in raw[:6]:
            if not isinstance(f, dict) or not str(f.get("text") or "").strip():
                continue
            sev = str(f.get("severity") or "").lower()
            found.append({"severity": sev if sev in ("high", "medium", "low") else "medium",
                          "text": ai._clip(f["text"], 300), "evidence": ai._clip(f.get("evidence") or "", 200)})
        return {"findings": found, "rules": {k: rules[k] for k in ("checks", "problems", "summary")},
                "nothing": not found}

    return ai.Prepared("drawing_check", "document", doc_id, doc["revision"], ai._hash(request), messages, interpret,
                       max_tokens=700, params={"id": doc_id}, local=bool(ai.local_reason(item, doc, cfg)))


# ------------------------------------------------------------------------------------ duplicate explanation --

def _fmt(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " × ".join(str(round(x, 1)) if isinstance(x, float) else str(x) for x in v if x is not None) or "—"
    return str(v) if v not in (None, "") else "—"


def duplicate_facts(group_id: int) -> tuple[dict, list[dict], str]:
    """The group, a plain record of each member, and Kosha's own line on what is alike and what differs."""
    from .similarity import group_detail
    g = group_detail(group_id)
    if not g:
        raise not_found("That group of look-alike parts does not exist.")
    members: list[dict] = []
    if g["items"]:
        for m in g["items"][:6]:
            it = m["item"]
            members.append({"part_number": it["part_number"], "description": it["description"],
                            "material": it["material"] or "", "make_or_buy": it["make_buy"], "unit": it["uom"],
                            "used_in": m["where_used"], "similarity": round(m["score"] * 100)})
    else:
        for m in g["members"][:6]:
            d = m["document"]
            members.append({"part_number": d.get("part_number") or "", "file": d["filename"],
                            "description": d.get("description") or "", "material": m["material"] or "",
                            "revision": d.get("revision") or "", "size": _fmt(m["size"]),
                            "holes": ", ".join(str(x) for x in m["diameters"]) or "—", "used_in": m["where_used"],
                            "similarity": round(m["score"] * 100)})
    same, differ = [], []
    for key, label in (("size", "size"), ("holes", "holes Ø"), ("material", "material"), ("description", "description"),
                       ("part_number", "part number")):
        vals = [m.get(key) for m in members if key in m]
        if not vals:
            continue
        if len({_norm(v) for v in vals}) == 1 and vals[0] not in ("", "—"):
            same.append(f"same {label} ({vals[0]})")
        elif len({_norm(v) for v in vals}) > 1:
            differ.append(f"{label} differs ({' / '.join(str(v) or '—' for v in vals[:3])})")
    line = (g["kind_label"] + ": " + ", ".join(same or ["similar shape"]) +
            ("; " + "; ".join(differ) if differ else "; nothing else differs in what Kosha measures") + ".")
    return g, members, line


DUPLICATE_SYSTEM = (
    "You help an engineering company remove duplicate parts. You get two or more parts that Kosha found to look "
    "alike, with the facts it measured. In one plain sentence of at most 30 words, say why they look alike and "
    "what differs, so an engineer can decide whether one can replace the other. Use only the facts given. "
    + ai.JSON_RULE + ' Keys: "line" (the sentence) and "interchangeable" ("likely", "unlikely" or "unclear").'
)


def prepare_duplicate(user: dict, group_id: int) -> ai.Prepared:
    g, members, rules = duplicate_facts(group_id)
    request = {"kind": g["kind_label"], "parts": members, "kosha_says": rules}
    messages = ai.conversation(DUPLICATE_SYSTEM, json.dumps(request, ensure_ascii=False, indent=1))

    def interpret(comp: Completion) -> dict:
        ans = ai._obj(comp.text)
        line = ans.get("line")
        if not isinstance(line, str) or not line.strip():
            raise ValueError("no line in the answer")
        inter = str(ans.get("interchangeable") or "unclear").lower()
        return {"line": ai._clip(line, 300), "interchangeable": inter if inter in ("likely", "unlikely") else "unclear",
                "rules": rules}

    local = any(ai.local_reason(db.one("SELECT * FROM items WHERE id = ?", (m["item"]["id"],)), None)
                for m in g["items"]) or any(
        ai.local_reason(None, db.one("SELECT * FROM documents WHERE id = ?", (m["document"]["id"],)))
        for m in g["members"])
    return ai.Prepared("duplicates", "cp_group", group_id, "", ai._hash(request), messages, interpret, max_tokens=200,
                       params={"group": group_id}, local=local)


# ------------------------------------------------------------------------------------- title-block reading --

def png(width: int, height: int, rows: list[bytes], channels: int = 3) -> bytes:
    """A PNG file from raw rows (RGB or grey), written without an image library."""
    color = {1: 0, 3: 2}[channels]
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# The title block sits in the bottom-right corner of almost every drawing (ISO 7200): that part is sent.
CORNER = (0.45, 0.35)      # share of the width and of the height
MAX_PIXELS = 1600


def _pdf_corner(data: bytes) -> bytes:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[0]
        try:
            w, h = page.get_size()
            scale = min(200 / 72, MAX_PIXELS / max(1.0, w * CORNER[0]))
            bm = page.render(scale=scale, crop=(w * (1 - CORNER[0]), 0, 0, h * (1 - CORNER[1])))
            mode = getattr(bm, "mode", "BGR")
            n = {"BGR": 3, "BGRA": 4, "BGRX": 4, "L": 1}.get(mode, 3)
            buf = bytes(bm.buffer)
            rows = []
            for y in range(bm.height):
                row = buf[y * bm.stride: y * bm.stride + bm.width * n]
                if n == 1:
                    rows.append(row)
                    continue
                rgb = bytearray(bm.width * 3)
                rgb[0::3], rgb[1::3], rgb[2::3] = row[2::n], row[1::n], row[0::n]
                rows.append(bytes(rgb))
            return png(bm.width, bm.height, rows, 1 if n == 1 else 3)
        finally:
            page.close()
    finally:
        pdf.close()


def _image_corner(data: bytes) -> bytes:
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    w, h = img.size
    img = img.crop((int(w * (1 - CORNER[0])), int(h * (1 - CORNER[1])), w, h))
    img.thumbnail((MAX_PIXELS, MAX_PIXELS))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def title_block_image(doc: dict) -> bytes:
    """The bottom-right corner of the first page as a PNG. Raises ApiError when the file is not a scan."""
    ext = doc["filename"].rsplit(".", 1)[-1].lower() if "." in doc["filename"] else ""
    data = store.read_bytes(_latest(doc)["blob_sha"])
    try:
        if ext == "pdf":
            return _pdf_corner(data)
        if ext in ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"):
            try:
                return _image_corner(data)
            except ImportError:
                raise ApiError(409, "Reading photos needs the Pillow package on the server. PDF scans work without "
                                    "it; ask your administrator.", "ocr_unavailable") from None
    except ApiError:
        raise
    except ImportError:
        raise ApiError(409, "Reading scanned PDFs needs the pypdfium2 package on the server; ask your administrator.",
                       "ocr_unavailable") from None
    except Exception as e:
        raise ApiError(409, f"Kosha could not open this file to read its title block ({e.__class__.__name__}). "
                            "Nothing was changed.", "ocr_unreadable") from None
    raise bad("Title-block reading works for scanned drawings: PDF files and photos (PNG, JPEG, TIFF).")


OCR_SYSTEM = (
    "You read the title block of an engineering drawing from an image of its bottom-right corner. Copy the values "
    "exactly as printed; when a value is not readable or not there, use null. Never guess. " + ai.JSON_RULE +
    ' Keys: "part_number", "description", "material", "revision", "drawn_by", "scale", "date".'
)
OCR_KEYS = ("part_number", "description", "material", "revision", "drawn_by", "scale", "date")
LABELS = {"part_number": r"(?:PART|DRG|DWG|DRAWING|ITEM)\s*(?:NO|NUMBER|#)\.?", "description": r"(?:DESCRIPTION|TITLE)",
          "material": r"(?:MATERIAL|MATL|MOC)", "revision": r"(?:REV(?:ISION)?)\.?", "drawn_by": r"(?:DRAWN|DRN)(?:\s*BY)?",
          "scale": r"SCALE", "date": r"DATE"}


def parse_title_text(text: str) -> dict:
    """Title-block values from OCR text ("MATERIAL: C45" or the label on one line, the value on the next)."""
    lines = [" ".join(x.split()) for x in (text or "").splitlines() if x.strip()]
    out: dict[str, str] = {}
    for key, label in LABELS.items():
        rx = re.compile(rf"^{label}\s*[:\-=]?\s*(.*)$", re.I)
        for i, line in enumerate(lines):
            m = rx.match(line)
            if m:
                val = m.group(1).strip() or (lines[i + 1] if i + 1 < len(lines) else "")
                if val and not any(re.match(rf"^{lb}\b", val, re.I) for lb in LABELS.values()):
                    out[key] = val[:120]
                break
    return out


def _ocr_fields(read: dict, doc: dict, item: dict | None) -> dict:
    """Turn what was read into a suggestion: description and material can be accepted; the rest is compared."""
    fields, notes = [], []
    current = {"description": (item or doc)["description"] or "", "material": item["material"] if item else ""}
    for key, label in (("description", "Description"), ("material", "Material")):
        v = read.get(key)
        if not v or (key == "material" and not item):
            continue
        if _norm(v) != _norm(current[key]):
            fields.append({"key": key, "label": label, "current": current[key], "suggested": v, "note": "",
                           "reason": "Read from the title block."})
    if read.get("part_number") and doc["part_number"] and _norm(read["part_number"]) != _norm(doc["part_number"]):
        notes.append(f"The title block says part number {read['part_number']}, but the file is {doc['part_number']}.")
    if read.get("revision") and doc["revision"] and _rev(read["revision"]) != _rev(doc["revision"]):
        notes.append(f"The title block says revision {read['revision']}, but the file is at {doc['revision']}.")
    return {"read": {k: read.get(k) or "" for k in OCR_KEYS}, "fields": fields, "checks": notes,
            "standard_part": None, "ignored": [], "target": {"item_id": item["id"] if item else None,
                                                            "document_id": doc["id"]},
            "nothing": not fields and not notes}


def _tesseract(image: bytes) -> str:
    from .inputs import ocr_status
    ok, why = ocr_status()
    if not ok:
        raise RuntimeError(why)
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(io.BytesIO(image)), timeout=60)


def prepare_ocr(user: dict, doc_id: int) -> ai.Prepared:
    from .lifecycle import doc_or_404
    doc = doc_or_404(doc_id)
    item = db.one("SELECT * FROM items WHERE id = ?", (doc["item_id"],)) if doc["item_id"] else None
    image = title_block_image(doc)
    url = "data:image/png;base64," + base64.b64encode(image).decode()
    messages = [Message("system", OCR_SYSTEM),
                Message("user", "Read the title block in this image.", images=(url,))]

    def interpret(comp: Completion) -> dict:
        ans = ai._obj(comp.text)
        read = {k: " ".join(str(ans[k]).split())[:120] for k in OCR_KEYS
                if isinstance(ans.get(k), (str, int, float)) and str(ans[k]).strip().lower() not in ("", "null")}
        if not read:
            raise ValueError("nothing was read")
        return _ocr_fields(read, doc, item)

    def on_unreachable() -> dict:
        out = _ocr_fields(parse_title_text(_tesseract(image)), doc, item)
        out["note"] = "Read on the Kosha server with Tesseract, because the AI service could not be reached."
        return out

    h = ai._hash(_latest(doc)["blob_sha"], doc["description"], item["material"] if item else "")
    return ai.Prepared("ocr", "document", doc_id, doc["revision"], h, messages, interpret, max_tokens=300,
                       params={"id": doc_id, "image_bytes": len(image)}, on_unreachable=on_unreachable,
                       local=bool(ai.local_reason(item, doc)))


# ---------------------------------------------------------------------------------------------- speech --

def transcribe(user: dict, audio: bytes, mime: str = "audio/webm", language: str = "") -> dict:
    """Speech to text for a spoken question or note. Nothing is stored; the call is logged without content."""
    cfg = ai.require("voice")
    if not audio:
        raise bad("The recording is empty. Hold the microphone button while you speak.")
    if len(audio) > MAX_AUDIO:
        raise bad("The recording is longer than Kosha accepts (10 MB). Keep a spoken question short.")
    mime = (mime or "audio/webm").split(";")[0].strip().lower()
    if not mime.startswith("audio/"):
        raise bad("Send a sound recording (audio/webm, audio/ogg, audio/wav or audio/mpeg).")
    language = language if language in ("", "en", "hi", "mr", "ta", "te", "gu", "bn", "kn") else ""
    ai.check_budget(cfg)
    routes = ai.route("voice", cfg)
    job = cfg["jobs"]["voice"]
    message = ""
    for provider, model, fallback in routes:
        t0 = time.monotonic()
        try:
            text = ai.call_provider(provider, model, "transcribe", audio, mime=mime, language=language,
                                    timeout=int(job["timeout_s"] or cfg["timeout_s"]))
            if not isinstance(text, str):
                raise AddinError("The AI provider answered in a form Kosha does not understand.")
        except AddinError as e:
            ai.log_call("voice", provider, model, user, ms=int((time.monotonic() - t0) * 1000), ok=False,
                        fallback=fallback, error=str(e))
            message = str(e)
            continue
        # speech is priced per minute by most services; the log keeps the size as "tokens" (one per kilobyte)
        ai.log_call("voice", provider, model or "speech", user, usage={"prompt_tokens": len(audio) // 1024},
                    ms=int((time.monotonic() - t0) * 1000), fallback=fallback)
        text = " ".join(text.split())[:1000]
        if not text:
            raise ApiError(422, "Nothing could be heard in the recording. Try again a little closer to the "
                                "microphone, or type the question.", "voice_empty")
        return {"text": text}
    raise ApiError(503, "The speech service could not be reached; nothing was lost. Type the question instead, or "
                        "try again later.", "ai_unreachable", {"detail": message} if user.get("role") == "admin"
                   else None)
