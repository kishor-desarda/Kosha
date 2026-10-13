# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watermarks on PDFs that leave Kosha (specification 15.3).

Downloaded released PDFs, and every PDF opened through a share link, carry one line along the bottom of each page:
the person (or the share's recipient), the date and the company's wording (``watermark_text``, *Company
confidential* by default). Files still in work are downloaded unchanged, so they can be edited and checked in again.

Uses pdfium (the optional ``pypdfium2`` package, already used for PDF thumbnails). Without it, or for a PDF it cannot
open, the file goes out unchanged and the audit entry says so.
"""
from __future__ import annotations

import ctypes
import io
import logging
from datetime import date

from . import db

log = logging.getLogger("kosha.watermark")
STATES = ("released", "spares_only", "obsolete")


def enabled() -> bool:
    return bool(db.get_setting("watermark_downloads"))


def line_for(name: str) -> str:
    text = (db.get_setting("watermark_text") or "Company confidential").strip()
    return f"{name} · {date.today().strftime('%d-%b-%Y')} · {text}"


def stamp_pdf(data: bytes, line: str) -> bytes | None:
    """The PDF with ``line`` written along the bottom of every page; None when it cannot be done."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        return None
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:       # damaged or password-protected: sent unchanged, and the audit entry says so
        return None
    try:
        font = raw.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica")
        text = (line + "\x00").encode("utf-16-le")
        for i in range(len(pdf)):
            page = pdf[i]
            w, _h = page.get_size()
            size = max(7.0, min(11.0, w / 70))
            obj = raw.FPDFPageObj_CreateTextObj(pdf.raw, font, size)
            raw.FPDFText_SetText(obj, ctypes.cast(ctypes.c_char_p(text), ctypes.POINTER(raw.FPDF_WCHAR)))
            raw.FPDFPageObj_SetFillColor(obj, 200, 30, 30, 150)
            raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 18, 10)
            raw.FPDFPage_InsertObject(page.raw, obj)
            raw.FPDFPage_GenerateContent(page.raw)
            page.close()
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()
    except Exception:
        log.exception("Could not watermark a PDF")
        return None
    finally:
        pdf.close()


# ---------------------------------------------------- release outputs (8.3) --
# Small drawing helpers on pdfium's raw interface, used for the release stamp, the superseded band, the printed
# line and the release sheet. Colours are (red, green, blue, alpha) from 0 to 255.

def _text(pdf, page, font, line: str, size: float, x: float, y: float, rgba=(0, 0, 0, 255)):
    import pypdfium2.raw as raw
    obj = raw.FPDFPageObj_CreateTextObj(pdf.raw, font, size)
    text = (line + "\x00").encode("utf-16-le")
    raw.FPDFText_SetText(obj, ctypes.cast(ctypes.c_char_p(text), ctypes.POINTER(raw.FPDF_WCHAR)))
    raw.FPDFPageObj_SetFillColor(obj, *rgba)
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page.raw, obj)
    return obj


def _width(obj) -> float:
    import pypdfium2.raw as raw
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    raw.FPDFPageObj_GetBounds(obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top))
    return right.value - left.value


def _rect(page, x: float, y: float, w: float, h: float, fill=None, stroke=None):
    import pypdfium2.raw as raw
    obj = raw.FPDFPageObj_CreateNewRect(x, y, w, h)
    if fill:
        raw.FPDFPageObj_SetFillColor(obj, *fill)
    if stroke:
        raw.FPDFPageObj_SetStrokeColor(obj, *stroke)
        raw.FPDFPageObj_SetStrokeWidth(obj, 1.2)
    raw.FPDFPath_SetDrawMode(obj, raw.FPDF_FILLMODE_ALTERNATE if fill else 0, bool(stroke))
    raw.FPDFPage_InsertObject(page.raw, obj)
    return obj


def _each_page(data: bytes, draw) -> bytes | None:
    """Run ``draw(pdf, page, font, width, height)`` on every page; the new PDF, or None when it cannot be done."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        return None
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:
        return None
    try:
        font = raw.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica-Bold")
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            draw(pdf, page, font, w, h)
            raw.FPDFPage_GenerateContent(page.raw)
            page.close()
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()
    except Exception:
        log.exception("Could not draw on a PDF")
        return None
    finally:
        pdf.close()


GREEN = (20, 120, 60, 255)
RED = (200, 30, 30, 255)


def release_stamp(data: bytes, line: str) -> bytes | None:
    """A green framed stamp at the top right of every page: *Released — Rev B — 10-Oct-2026*."""
    def draw(pdf, page, font, w, h):
        size = max(8.0, min(14.0, w / 50))
        t = _text(pdf, page, font, line, size, 0, 0, GREEN)
        tw = _width(t)
        x, y = w - tw - 24, h - 18 - size
        import pypdfium2.raw as raw
        raw.FPDFPageObj_Transform(t, 1, 0, 0, 1, x, y)
        _rect(page, x - 6, y - 5, tw + 12, size + 9, stroke=GREEN)
    return _each_page(data, draw)


def superseded_band(data: bytes, line: str) -> bytes | None:
    """A red see-through band across the middle of every page (title block and stamp stay readable):
    *Superseded — use Rev C*."""
    def draw(pdf, page, font, w, h):
        size = max(12.0, min(28.0, w / 30))
        band = size * 2.2
        _rect(page, 0, (h - band) / 2, w, band, fill=(200, 30, 30, 130))
        t = _text(pdf, page, font, line, size, 0, 0, (255, 255, 255, 255))
        import pypdfium2.raw as raw
        raw.FPDFPageObj_Transform(t, 1, 0, 0, 1, max(12, (w - _width(t)) / 2), h / 2 - size * 0.35)
    return _each_page(data, draw)


def printed_line(data: bytes, line: str) -> bytes | None:
    """One small line at the top left of every page: *Uncontrolled when printed — 13-Oct-2026*."""
    def draw(pdf, page, font, w, h):
        _text(pdf, page, font, line, max(7.0, min(10.0, w / 80)), 18, h - 14, RED)
    return _each_page(data, draw)


def sheet(title: str, rows: list[tuple[str, str]], table: list[list[str]] | None = None,
          heads: list[str] | None = None, footer: str = "") -> bytes | None:
    """A new A4 PDF: a title, label/value rows, an optional table and a footer line (the release sheet)."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        return None
    pdf = pdfium.PdfDocument.new()
    try:
        bold = raw.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica-Bold")
        plain = raw.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica")
        w, h = 595, 842
        pages = []

        def new_page():
            p = pdf.new_page(w, h)
            pages.append(p)
            return p
        page = new_page()
        y = h - 60
        _text(pdf, page, bold, title, 18, 48, y)
        y -= 34
        for label, value in rows:
            _text(pdf, page, bold, label, 10, 48, y, (90, 90, 90, 255))
            _text(pdf, page, plain, str(value)[:90], 10, 190, y)
            y -= 17
        if table:
            y -= 14
            cols = [48, 230, 290, 340, 420]
            for x, head in zip(cols, heads or []):
                _text(pdf, page, bold, head, 9, x, y, (90, 90, 90, 255))
            y -= 15
            for row in table:
                if y < 70:
                    page = new_page()
                    y = h - 60
                for x, cell in zip(cols, row):
                    _text(pdf, page, plain, str(cell)[:40 if x == 48 else 28], 9, x, y)
                y -= 14
        for p in pages:
            if footer:
                _text(pdf, p, plain, footer, 8, 48, 30, (90, 90, 90, 255))
            raw.FPDFPage_GenerateContent(p.raw)
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()
    except Exception:
        log.exception("Could not write a release sheet")
        return None
    finally:
        for p in locals().get("pages", []):
            p.close()
        pdf.close()
