# SPDX-License-Identifier: AGPL-3.0-or-later
"""Previews of non-CAD files (specification 8): text and CSV, ZIP archives, e-mails, Word, Excel and PowerPoint
files, STEP models and videos, and any file type an add-in of kind ``file-type`` handles.

A preview is a **summary** (kept as JSON next to the file version and shown by the viewer) plus a small card
picture for lists. Reading never runs anything in the file: archives are listed, not unpacked; e-mails are
parsed as text, never shown as HTML; Office files are read through their XML with document type declarations
refused; long files are cut (MAX_LINES, MAX_ROWS, MAX_ENTRIES) and the summary says so.
"""
from __future__ import annotations

import csv
import email
import email.policy
import html
import io
import json
import logging
import re
import zipfile
from typing import Any

log = logging.getLogger("kosha.previews")

TEXT = {"txt", "md", "log", "json", "xml", "ini", "cfg", "yaml", "yml", "nc", "tap", "gcode", "eia"}
TABLE = {"csv", "tsv"}
ARCHIVE = {"zip"}
EMAIL = {"eml"}
OFFICE = {"docx", "xlsx", "xlsm", "pptx"}
MODEL = {"step", "stp"}
VIDEO = {"mp4": "video/mp4", "webm": "video/webm", "ogv": "video/ogg", "m4v": "video/mp4"}
HANDLED = TEXT | TABLE | ARCHIVE | EMAIL | OFFICE | MODEL | set(VIDEO)

MAX_LINES = 2000
MAX_CHARS = 400
MAX_ROWS = 300
MAX_COLS = 40
MAX_ENTRIES = 2000
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_PART = 64 * 1024 * 1024
TYPES = ("text", "table", "archive", "email", "document", "slides", "model")


def _decode(data: bytes) -> str:
    from .diff.text import _decode as dec
    return dec(data)


def _line(s: Any) -> str:
    s = str(s if s is not None else "").replace("\t", "    ").rstrip()
    return s if len(s) <= MAX_CHARS else s[: MAX_CHARS - 1] + "…"


def _lines(text: str) -> tuple[list[str], bool]:
    all_lines = text.splitlines()
    return [_line(x) for x in all_lines[:MAX_LINES]], len(all_lines) > MAX_LINES


def clean(summary: Any) -> dict | None:
    """Keep only the keys and sizes the viewer knows (also for summaries that come from add-ins)."""
    if not isinstance(summary, dict) or summary.get("type") not in TYPES:
        return None
    out: dict[str, Any] = {"type": summary["type"], "title": _line(summary.get("title") or "")[:200]}
    if isinstance(summary.get("lines"), list):
        out["lines"] = [_line(x) for x in summary["lines"][:MAX_LINES]]
    if isinstance(summary.get("rows"), list):
        out["rows"] = [[_line(c)[:120] for c in (r if isinstance(r, list) else [r])[:MAX_COLS]]
                       for r in summary["rows"][:MAX_ROWS]]
    if isinstance(summary.get("entries"), list):
        out["entries"] = [{"name": _line(e.get("name"))[:300], "size": int(e.get("size") or 0),
                           **({"note": _line(e["note"])[:120]} if e.get("note") else {})}
                          for e in summary["entries"][:MAX_ENTRIES] if isinstance(e, dict)]
    if isinstance(summary.get("headers"), dict):
        out["headers"] = {_line(k)[:40]: _line(v)[:300] for k, v in list(summary["headers"].items())[:12]}
    if isinstance(summary.get("attachments"), list):
        out["attachments"] = [{"name": _line(a.get("name"))[:200], "size": int(a.get("size") or 0)}
                              for a in summary["attachments"][:100] if isinstance(a, dict)]
    if isinstance(summary.get("sections"), list):       # slides, sheets
        out["sections"] = [{"title": _line(s.get("title"))[:200], "lines": [_line(x) for x in (s.get("lines") or [])[:60]]}
                           for s in summary["sections"][:200] if isinstance(s, dict)]
    if summary.get("note"):
        out["note"] = _line(summary["note"])
    return out


# ------------------------------------------------------------------------------------------------ readers --

def _text(ext: str, data: bytes) -> dict:
    head = data[:MAX_TEXT_BYTES]
    lines, cut = _lines(_decode(head))
    if ext == "json" and len(data) <= MAX_TEXT_BYTES:
        try:
            lines, cut = _lines(json.dumps(json.loads(_decode(data)), indent=2, ensure_ascii=False))
        except ValueError:
            pass
    note = f"The first {len(lines)} lines are shown." if cut or len(data) > MAX_TEXT_BYTES else ""
    return {"type": "text", "lines": lines, "note": note}


def _table(ext: str, data: bytes) -> dict:
    text = _decode(data[:MAX_TEXT_BYTES])
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if ext == "csv" else csv.excel_tab
    except csv.Error:
        dialect = csv.excel
    rows = []
    for i, r in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i >= MAX_ROWS:
            break
        rows.append(r)
    total = text.count("\n") + (0 if text.endswith("\n") else 1)
    return {"type": "table", "rows": rows,
            "note": f"The first {MAX_ROWS} of about {total} rows are shown." if total > MAX_ROWS else ""}


def _archive(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        entries = []
        for i in infos[:MAX_ENTRIES]:
            if i.is_dir():
                continue
            note = "protected with a password" if i.flag_bits & 0x1 else ""
            name = i.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                note = "unsafe path (would not be unpacked)"
            entries.append({"name": name, "size": i.file_size, "note": note})
        total = sum(i.file_size for i in infos)
    files = sum(1 for i in infos if not i.is_dir())
    note = f"{files} files, {total:,} bytes unpacked." + (f" The first {MAX_ENTRIES} are listed." if len(infos) > MAX_ENTRIES else "")
    return {"type": "archive", "entries": entries, "note": note}


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def _email(data: bytes) -> dict:
    msg = email.message_from_bytes(data[:MAX_TEXT_BYTES * 4], policy=email.policy.default)
    headers = {}
    for k in ("From", "To", "Cc", "Date", "Subject"):
        try:
            v = msg.get(k)
        except Exception:       # a broken header: shown as missing, never an error
            v = None
        if v:
            headers[k] = str(v)
    plain, rich, attachments = "", "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        fname = part.get_filename()
        if fname or part.get_content_disposition() == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append({"name": fname or "(no name)", "size": len(payload)})
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or (plain if ctype == "text/plain" else rich):
            continue
        try:
            text = part.get_content()
        except Exception:       # an unknown character set: read it as bytes
            text = _decode(part.get_payload(decode=True) or b"")
        if ctype == "text/plain":
            plain = text
        else:
            rich = _strip_html(text)
    body = plain or rich
    lines, cut = _lines(body.strip())
    return {"type": "email", "title": headers.get("Subject", ""), "headers": headers, "lines": lines,
            "attachments": attachments, "note": "The message is cut; download it to read all." if cut else ""}


def _zip_xml(zf: zipfile.ZipFile, name: str) -> str:
    info = zf.getinfo(name)
    if info.file_size > MAX_PART:
        raise ValueError("A part of the file is too large to read.")
    text = zf.read(info).decode("utf-8", errors="replace")
    if "<!DOCTYPE" in text[:2000].upper():
        raise ValueError("The file contains an XML document type declaration, which Kosha does not read.")
    return text


def _slides(data: bytes) -> dict:
    sections = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted((n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                       key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        for i, n in enumerate(names, 1):
            xml = _zip_xml(zf, n)
            paras = []
            for p in re.findall(r"<a:p\b.*?</a:p>", xml, flags=re.S):
                t = "".join(html.unescape(x) for x in re.findall(r"<a:t>(.*?)</a:t>", p, flags=re.S)).strip()
                if t:
                    paras.append(t)
            sections.append({"title": f"Slide {i}" + (f": {paras[0]}" if paras else ""), "lines": paras[1:]})
    return {"type": "slides", "sections": sections}


def _office(ext: str, data: bytes) -> dict:
    if ext == "docx":
        from .diff.text import docx_paragraphs
        lines, cut = _lines("\n".join(docx_paragraphs(data)))
        return {"type": "document", "lines": lines, "note": "The first lines are shown." if cut else ""}
    if ext == "pptx":
        return _slides(data)
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sections, rows = [], []
        for n, ws in enumerate(wb.worksheets):
            if n == 0:
                for i, r in enumerate(ws.iter_rows(values_only=True)):
                    if i >= MAX_ROWS:
                        break
                    rows.append(["" if c is None else c for c in r[:MAX_COLS]])
                while rows and not any(str(c).strip() for c in rows[-1]):
                    rows.pop()
            sections.append({"title": ws.title, "lines": []})
    finally:
        wb.close()
    note = f"Sheet “{sections[0]['title']}” is shown" + (f"; the workbook has {len(sections)} sheets." if len(sections) > 1
                                                          else ".") if sections else ""
    return {"type": "table", "rows": rows, "sections": sections, "note": note}


STEP_STRING = re.compile(r"'((?:[^']|'')*)'")


def _step(data: bytes) -> tuple[dict, dict]:
    """STEP header (who made it, with which program, which schema) and the product names, from the text."""
    head = data[:4 * 1024 * 1024].decode("latin-1", errors="replace")
    if "ISO-10303-21" not in head[:200]:
        raise ValueError("This is not a STEP file (ISO 10303-21).")
    meta: dict[str, Any] = {}
    lines = []
    m = re.search(r"FILE_NAME\s*\((.*?)\)\s*;", head, flags=re.S)
    if m:
        vals = [v.replace("''", "'") for v in STEP_STRING.findall(m.group(1))]
        for label, v in zip(("Name", "Saved", "Author", "Organisation", "Translator", "Made with"), vals):
            if v.strip():
                lines.append(f"{label}: {v.strip()}")
                if label in ("Made with", "Saved"):
                    meta["step_" + label.lower().replace(" ", "_")] = v.strip()
    m = re.search(r"FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)", head, flags=re.S)
    if m:
        schema = ", ".join(v for v in STEP_STRING.findall(m.group(1)))
        lines.append(f"Schema: {schema}")
        meta["step_schema"] = schema
    products = list(dict.fromkeys(v.replace("''", "'") for v in re.findall(
        r"PRODUCT\s*\(\s*'((?:[^']|'')*)'", data.decode("latin-1", errors="replace"))))
    if products:
        lines.append(f"Parts and assemblies ({len(products)}):")
        lines += [f"- {p}" for p in products[:200]]
        meta["step_products"] = len(products)
    entities = data.count(b"\n#") + (1 if data.startswith(b"#") else 0)
    lines.append(f"Entities: {entities:,}")
    return {"type": "model", "lines": lines, "note": "A 3D view of STEP models needs a converter add-in."}, meta


# ------------------------------------------------------------------------------------------------ cards --

def card_svg(summary: dict, filename: str) -> bytes:
    """A small picture for lists: the file type and its first lines."""
    label = {"text": "TEXT", "table": "TABLE", "archive": "ZIP", "email": "E-MAIL", "document": "WORD",
             "slides": "SLIDES", "model": "STEP"}.get(summary["type"], "FILE")
    rows: list[str] = []
    if summary.get("headers"):
        rows += [f"{k}: {v}" for k, v in summary["headers"].items() if k in ("From", "Subject")]
    rows += summary.get("lines", [])[:8]
    rows += [" | ".join(str(c) for c in r[:4]) for r in summary.get("rows", [])[:8]]
    rows += [e["name"] for e in summary.get("entries", [])[:8]]
    rows += [s["title"] for s in summary.get("sections", [])[:8] if summary["type"] == "slides"]
    rows = [r for r in rows if r.strip()][:9]
    esc = html.escape
    text = "".join(f'<text x="14" y="{58 + 17 * i}" font-size="12" fill="#3f4a5a">{esc(r[:46])}</text>'
                   for i, r in enumerate(rows))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220" viewBox="0 0 320 220">'
            f'<rect width="320" height="220" rx="8" fill="#f6f7f9" stroke="#d5dae1"/>'
            f'<rect x="10" y="12" width="{12 + 8 * len(label)}" height="22" rx="4" fill="#2f5d8a"/>'
            f'<text x="16" y="28" font-size="12" font-weight="700" fill="#ffffff" font-family="sans-serif">{label}</text>'
            f'<text x="{30 + 8 * len(label)}" y="28" font-size="12" fill="#2b3442" font-family="sans-serif">'
            f'{esc(filename[:30])}</text><g font-family="monospace">{text}</g></svg>').encode()


def video_card(filename: str, size: int) -> bytes:
    esc = html.escape
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220" viewBox="0 0 320 220">'
            f'<rect width="320" height="220" rx="8" fill="#1f2630"/><circle cx="160" cy="98" r="34" fill="#ffffff22"/>'
            f'<path d="M150 80 L178 98 L150 116 Z" fill="#ffffff"/>'
            f'<text x="160" y="170" text-anchor="middle" font-size="13" fill="#e6e9ee" font-family="sans-serif">'
            f'{esc(filename[:40])}</text><text x="160" y="190" text-anchor="middle" font-size="11" fill="#aab3c0" '
            f'font-family="sans-serif">Video, {size / 1048576:.1f} MB</text></svg>').encode()


# ------------------------------------------------------------------------------------------------- entry --

def build(out: dict, ext: str, data: bytes, filename: str) -> None:
    """Fill a processing result (see cad.process) with the preview of a non-CAD file. Never raises for bad files:
    the reason goes to ``meta.preview_note``."""
    if ext in VIDEO:
        out["viewer"] = (ext, data)
        out["preview"] = ("svg", video_card(filename, len(data)))
        return
    try:
        if ext in TEXT:
            summary = _text(ext, data)
        elif ext in TABLE:
            summary = _table(ext, data)
        elif ext in ARCHIVE:
            summary = _archive(data)
        elif ext in EMAIL:
            summary = _email(data)
        elif ext in OFFICE:
            summary = _office(ext, data)
        elif ext in MODEL:
            summary, meta = _step(data)
            out["meta"].update(meta)
        else:
            return
    except (zipfile.BadZipFile, KeyError):
        out["meta"]["preview_note"] = "The file is damaged or not what its name says, so Kosha cannot show it."
        return
    except Exception as e:      # noqa: BLE001 - never fail a check-in because of a preview
        log.info("No preview for %s: %s", filename, e)
        out["meta"]["preview_note"] = f"No preview: {e}"
        return
    set_summary(out, summary, filename)


def set_summary(out: dict, summary: dict, filename: str) -> None:
    s = clean(summary)
    if not s:
        return
    s["title"] = s["title"] or filename
    out["viewer"] = ("summary", json.dumps(s, ensure_ascii=False, default=str).encode())
    if not out.get("preview"):
        out["preview"] = ("svg", card_svg(s, filename))


def from_addins(out: dict, ext: str, data: bytes, filename: str) -> None:
    """Ask the enabled file-type add-ins that name this extension, through the guard. First answer wins."""
    from kosha_sdk import AddinError

    from .addins import registry
    for a in registry.by_kind("file-type"):
        exts = registry.attribute(a.id, "extensions", ()) or ()
        if ext not in {str(x).lower().lstrip(".") for x in exts}:
            continue
        try:
            p = registry.call(a.id, "preview", filename, data, timeout=30)
        except AddinError as e:
            out["meta"]["preview_note"] = f"The add-in {a.manifest.name} could not show this file: {e}"
            continue
        if p is None:
            continue
        summary, image, meta = getattr(p, "summary", None), getattr(p, "image", None), getattr(p, "meta", {}) or {}
        if image and isinstance(image, tuple) and len(image) == 2 and image[0] in ("png", "jpg", "svg") \
                and isinstance(image[1], bytes) and len(image[1]) <= 5 * 1024 * 1024:
            out["preview"] = image
        if summary:
            set_summary(out, summary, filename)
        elif out.get("preview") and not out.get("viewer"):
            out["viewer"] = out["preview"]
        for k, v in list(meta.items())[:12]:
            if isinstance(v, (str, int, float)):
                out["meta"][f"addin_{str(k)[:40]}"] = v if not isinstance(v, str) else v[:200]
        out["meta"]["preview_by"] = a.manifest.name
        out["meta"].pop("preview_note", None)
        return
