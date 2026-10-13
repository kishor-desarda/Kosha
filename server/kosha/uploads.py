# SPDX-License-Identifier: AGPL-3.0-or-later
"""Upload safety: a size limit enforced while the body arrives, safe file names, and the optional virus scan.

Used by the routing layer (``web.py``) for every request with a body, before any handler sees an uploaded file.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from starlette.requests import Request

from . import config, db, scan

log = logging.getLogger("kosha.uploads")

# Room for multipart boundaries and form fields on top of the file data itself.
MULTIPART_OVERHEAD = 1024 * 1024

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                    *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10)),
                    "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BIDI = re.compile("[‎‏‪-‮⁦-⁩]")
_WINDOWS_CHARS = re.compile(r'[<>:"|?*]')


class UploadRefused(Exception):
    """An upload Kosha will not take; ``web.py`` answers with ``status`` and the plain ``message``."""

    def __init__(self, status: int, message: str, code: str, extra: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.extra = status, message, code, extra or {}


# ------------------------------------------------------------- file names --

def clean_filename(name: str) -> str:
    """A file name that is safe on every operating system Kosha's users have.

    Any folder part is dropped (browsers and CAD add-ins sometimes send a full path) and trailing dots and spaces
    are removed, as Windows does. Control characters, text-direction characters (which can disguise a file type),
    the characters Windows forbids and the names Windows reserves for devices (CON, NUL, COM1 ...) are refused
    with a ValueError that says so plainly.
    """
    name = unicodedata.normalize("NFC", str(name or ""))
    name = name.replace("\\", "/").split("/")[-1]
    name = name.strip().rstrip(". ").strip()
    if not name or name in (".", ".."):
        raise ValueError("The file has no usable name. Rename it and try again.")
    if _CONTROL.search(name):
        raise ValueError("The file name contains control characters. Rename the file and try again.")
    if _BIDI.search(name):
        raise ValueError("The file name contains hidden text-direction characters, which can disguise what kind "
                         "of file it is. Rename the file and try again.")
    if _WINDOWS_CHARS.search(name):
        raise ValueError("The file name contains characters Windows does not allow: < > : \" | ? *")
    stem = name.split(".")[0].strip().upper()
    if stem in WINDOWS_RESERVED:
        raise ValueError(f"“{name}” uses a name Windows keeps for devices ({stem}), so it could not be "
                         "opened on Windows PCs. Rename the file and try again.")
    if len(name) > 255:
        raise ValueError("The file name is longer than 255 characters. Shorten it and try again.")
    return name


# ------------------------------------------------------------------- size --

def _mb(n: float) -> str:
    return f"{n:g} MB"


def limit_for(content_type: str) -> int:
    if content_type.startswith("multipart/form-data"):
        return int(config.MAX_UPLOAD_MB * 1024 * 1024) + MULTIPART_OVERHEAD
    return int(config.MAX_JSON_MB * 1024 * 1024)


def _too_large(content_type: str, declared: int | None = None) -> UploadRefused:
    limit = config.MAX_UPLOAD_MB if content_type.startswith("multipart/form-data") else config.MAX_JSON_MB
    size = f" ({declared / 1048576:.1f} MB)" if declared else ""
    return UploadRefused(413, f"This upload{size} is larger than the {_mb(limit)} this server accepts in one request, "
                              "so nothing was saved. Send fewer or smaller files at once, or ask your admin to raise "
                              "the limit (KOSHA_MAX_UPLOAD_MB).", "too_large", {"limit_mb": limit})


def limited(request: Request) -> Request:
    """The same request, but reading its body stops with a 413 as soon as it passes the size limit.

    A declared Content-Length over the limit is refused before a single byte of the body is read."""
    ctype = request.headers.get("content-type", "")
    limit = limit_for(ctype)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(ctype, int(declared))
    receive = request.receive
    seen = 0

    async def limited_receive():
        nonlocal seen
        message = await receive()
        if message.get("type") == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise _too_large(ctype)
        return message

    return Request(request.scope, limited_receive)


# ------------------------------------------------------------- virus scan --

def scan_files(files: dict, user_id: int | None, path: str) -> None:
    """Scan every uploaded file when a scanner is configured. Refuses the whole request when one is infected or
    when the scanner gives no reliable answer (fail closed). Runs in a worker thread."""
    if not scan.configured():
        return
    for field, items in files.items():
        for f in items:
            name = getattr(f, "filename", "") or field
            try:
                f.file.seek(0)
                result = scan.scan_stream(f.file)
            except scan.ScanUnavailable as e:
                db.audit(user_id, "upload_scan_failed", "upload", None, f"{name} ({path}): {e}")
                log.error("Upload of %r refused: %s", name, e)
                raise UploadRefused(503, f"Kosha could not check “{name}” for viruses: {e}. The file was not "
                                         "added and nothing was changed. Try again in a few minutes; if it keeps "
                                         "happening, tell your Kosha administrator.", "scan_unavailable")
            finally:
                try:
                    f.file.seek(0)
                except Exception:
                    pass
            if not result.clean:
                db.audit(user_id, "upload_infected", "upload", None, f"{name} ({path}): {result.signature}")
                log.warning("Infected upload %r refused: %s", name, result.signature)
                raise UploadRefused(422, f"“{name}” contains a virus ({result.signature}), so Kosha did not "
                                         "add it. Nothing in this upload was saved. Do not open the file; tell your "
                                         "IT department.", "infected", {"filename": name, "signature": result.signature})
