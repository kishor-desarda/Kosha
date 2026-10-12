# SPDX-License-Identifier: AGPL-3.0-or-later
"""Best-effort reading of SolidWorks files without SolidWorks installed.

The reliable path for previews, 3D meshes, custom properties and assembly
structure is Kosha SolidWorks add-in, which uploads them at check-in.
For files added through the browser, the server tries:

* the preview bitmap SolidWorks embeds in every file (PNG), and
* referenced component file names inside assemblies and drawings.
"""
from __future__ import annotations

import re
import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def extract_preview(data: bytes) -> bytes | None:
    """Return the largest valid PNG embedded in the file, if any."""
    best = None
    start = 0
    limit = len(data)
    while True:
        i = data.find(PNG_SIG, start)
        if i < 0:
            break
        j = data.find(b"IEND", i)
        if j < 0:
            break
        png = data[i:j + 8]
        if _valid_png(png) and (best is None or len(png) > len(best)):
            best = png
        start = j + 8
        if start >= limit:
            break
    if best is None:
        best = _bmp_from_ole_stream(data)
    return best


def _valid_png(png: bytes) -> bool:
    if len(png) < 64 or not png.startswith(PNG_SIG):
        return False
    try:
        length, ctype = struct.unpack(">I4s", png[8:16])
        if ctype != b"IHDR" or length != 13:
            return False
        w, h = struct.unpack(">II", png[16:24])
        return 8 <= w <= 4096 and 8 <= h <= 4096
    except struct.error:
        return False


def _bmp_from_ole_stream(data: bytes) -> bytes | None:
    # Older (pre-2015) files store "PreviewPNG" inside an OLE container; the PNG
    # bytes are contiguous within the stream so the scan above already finds
    # them. Nothing further to do without an OLE reader.
    return None


_REF_UTF16 = re.compile(
    rb"((?:[\x20-\x7e]\x00){1,255}?\.\x00[sS]\x00[lL]\x00[dD]\x00(?:[pP]\x00[rR]\x00[tT]|[aA]\x00[sS]\x00[mM])\x00)"
)
_REF_ASCII = re.compile(rb"([\x20-\x7e]{1,255}?\.(?:[sS][lL][dD][pP][rR][tT]|[sS][lL][dD][aA][sS][mM]))")


def referenced_files(data: bytes) -> list[str]:
    """File names of components referenced by an assembly or drawing."""
    names: dict[str, str] = {}
    for m in _REF_UTF16.finditer(data):
        _add(names, m.group(1).decode("utf-16-le", "ignore"))
    for m in _REF_ASCII.finditer(data):
        _add(names, m.group(1).decode("latin-1", "ignore"))
    # also look inside zlib-compressed blocks (newer file formats)
    if not names:
        for chunk in _zlib_chunks(data):
            for m in _REF_UTF16.finditer(chunk):
                _add(names, m.group(1).decode("utf-16-le", "ignore"))
    return sorted(names.values(), key=str.lower)


def _add(names: dict, raw: str) -> None:
    base = re.split(r"[\\/]", raw)[-1].strip()
    base = base.lstrip("~$")
    if len(base) < 8 or not re.match(r"^[\w\-. ()#&+]+$", base):
        return
    names.setdefault(base.lower(), base)


def _zlib_chunks(data: bytes, max_chunks: int = 400):
    count = 0
    for m in re.finditer(rb"\x78[\x01\x5e\x9c\xda]", data):
        if count >= max_chunks:
            break
        try:
            d = zlib.decompressobj()
            out = d.decompress(data[m.start(): m.start() + 4_000_000], 8_000_000)
            if len(out) > 32:
                count += 1
                yield out
        except zlib.error:
            continue
