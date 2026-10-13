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
        import io
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()
    except Exception:
        log.exception("Could not watermark a PDF")
        return None
    finally:
        pdf.close()
