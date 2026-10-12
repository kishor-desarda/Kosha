# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn an uploaded file into previews, viewer data, metadata and a signature."""
from __future__ import annotations

import hashlib
import io
import logging
import math

from . import dwg, dxf, mesh, solidworks

log = logging.getLogger("kosha.cad")

DOC_TYPES = {
    "sldprt": "part", "sldasm": "assembly", "slddrw": "drawing",
    "dwg": "drawing", "dxf": "drawing",
    "pdf": "pdf",
    "step": "model", "stp": "model", "igs": "model", "iges": "model", "x_t": "model", "stl": "model",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "bmp": "image", "webp": "image",
    "xlsx": "document", "xls": "document", "docx": "document", "doc": "document", "txt": "document",
    "csv": "document", "pptx": "document",
}

CAD_LABEL = {
    "sldprt": "SolidWorks part", "sldasm": "SolidWorks assembly", "slddrw": "SolidWorks drawing",
    "dwg": "DWG drawing (AutoCAD / GStarCAD)", "dxf": "DXF drawing", "pdf": "PDF", "step": "STEP model",
    "stp": "STEP model", "stl": "STL mesh", "igs": "IGES model", "iges": "IGES model",
}

# DXF already converted in bulk (see importer); keyed by SHA-256 of the DWG bytes
DWG_CACHE: dict[str, bytes] = {}

IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
              "bmp": "image/bmp", "webp": "image/webp"}


def ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def doc_type(filename: str) -> str:
    return DOC_TYPES.get(ext_of(filename), "other")


def process(filename: str, data: bytes) -> dict:
    """Returns {preview:(kind,bytes)|None, viewer:(kind,bytes)|None, meta:{}, signature:{}|None}."""
    ext = ext_of(filename)
    out: dict = {"preview": None, "viewer": None, "meta": {}, "signature": None}
    try:
        if ext == "dxf":
            _dxf(out, data, filename)
        elif ext == "dwg":
            out["meta"]["dwg_version"] = dwg.dwg_version(data)
            try:
                key = hashlib.sha256(data).hexdigest()
                dxf_bytes = DWG_CACHE.pop(key, None) or dwg.to_dxf(data)
                _dxf(out, dxf_bytes, filename)
            except dwg.DwgUnavailable as e:
                out["meta"]["preview_note"] = str(e)
        elif ext in ("sldprt", "sldasm", "slddrw"):
            png = solidworks.extract_preview(data)
            if png:
                out["preview"] = ("png", png)
                out["viewer"] = ("png", png)
            else:
                out["meta"]["preview_note"] = ("No embedded preview found. Check the file in with the "
                                               "Kosha SolidWorks add-in to get a 3D preview.")
            if ext in ("sldasm", "slddrw"):
                refs = solidworks.referenced_files(data)
                out["meta"]["references"] = refs
        elif ext == "stl":
            st = mesh.stats(data)
            if st:
                out["meta"]["mesh"] = st
                out["signature"] = mesh_signature(st)
            out["viewer"] = ("stl", data)
            svg = mesh_to_svg(data)
            if svg:
                out["preview"] = ("svg", svg.encode())
        elif ext == "pdf":
            out["viewer"] = ("pdf", data)
            png = _pdf_thumb(data)
            if png:
                out["preview"] = ("png", png)
        elif ext in IMAGE_MIME:
            out["preview"] = (ext, data)
            out["viewer"] = (ext, data)
    except dxf.DxfError as e:
        out["meta"]["preview_note"] = str(e)
    except Exception as e:  # never fail a check-in because of a preview
        log.exception("Preview failed for %s", filename)
        out["meta"]["preview_note"] = f"Preview could not be generated ({e.__class__.__name__})."
    return out


def _dxf(out: dict, data: bytes, name: str) -> None:
    r = dxf.analyse(data, name)
    svg = r["svg"].encode("utf-8")
    out["preview"] = ("svg", svg)
    out["viewer"] = ("svg", svg)
    out["meta"].update({
        "title_block": r["title_block"],
        "attributes": dict(list(r["attributes"].items())[:60]),
        "units": r["units"],
        "entities": r["entity_count"],
    })
    if r["truncated"]:
        out["meta"]["preview_note"] = "Very large drawing: preview shows the first 400,000 entities."
    out["signature"] = r["signature"]


def mesh_signature(st: dict) -> dict:
    return {"v": 1, "kind": "mesh", "bbox": st["bbox"], "area": st["area"], "volume": st["volume"]}


def _pdf_thumb(data: bytes) -> bytes | None:
    try:
        import pypdfium2 as pdfium  # optional dependency
    except Exception:
        return None
    try:
        pdf = pdfium.PdfDocument(data)
        page = pdf[0]
        w, h = page.get_size()
        scale = 480 / max(w, h)
        img = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def mesh_to_svg(data: bytes, max_tris: int = 12000) -> str | None:
    """Flat-shaded isometric thumbnail of a mesh."""
    try:
        tris = mesh.read_stl(data)
    except Exception:
        return None
    if not tris:
        return None
    if len(tris) > max_tris:
        step = len(tris) / max_tris
        tris = [tris[int(k * step)] for k in range(max_tris)]
    # isometric camera
    ax, az = math.radians(35.264), math.radians(45)
    ca, sa, cz, sz = math.cos(ax), math.sin(ax), math.cos(az), math.sin(az)

    def proj(p):
        x, y, z = p
        x1 = x * cz - y * sz
        y1 = x * sz + y * cz
        # tilt
        y2 = y1 * ca - z * sa
        z2 = y1 * sa + z * ca
        return x1, z2, y2  # screen x, screen y(up), depth

    light = (0.35, 0.55, 0.76)
    faces = []
    for t in tris:
        a, b, c = (proj(p) for p in t)
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / L, ny / L, nz / L
        if nz > 0:  # facing away (depth axis points into the screen)
            nx, ny, nz = -nx, -ny, -nz
        shade = max(0.0, nx * light[0] + ny * light[1] - nz * light[2])
        depth = (a[2] + b[2] + c[2]) / 3
        faces.append((depth, a, b, c, shade))
    xs = [p[0] for f in faces for p in f[1:4]]
    ys = [p[1] for f in faces for p in f[1:4]]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w, h = maxx - minx or 1, maxy - miny or 1
    pad = max(w, h) * 0.06
    faces.sort(key=lambda f: -f[0])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx - pad:.2f} {-maxy - pad:.2f} {w + 2 * pad:.2f} {h + 2 * pad:.2f}">',
             '<g stroke-linejoin="round">']
    for _, a, b, c, s in faces:
        g = int(70 + 150 * s)
        col = f"rgb({g},{min(255, g + 6)},{min(255, g + 12)})"
        parts.append(f'<path d="M{a[0]:.2f} {-a[1]:.2f}L{b[0]:.2f} {-b[1]:.2f}L{c[0]:.2f} {-c[1]:.2f}Z" fill="{col}" stroke="{col}" stroke-width="0.3"/>')
    parts.append("</g></svg>")
    return "".join(parts)
