# SPDX-License-Identifier: AGPL-3.0-or-later
"""DWG -> DXF conversion for AutoCAD and GStarCAD drawings.

DWG is a closed format, so Kosha hands it to a converter installed on the
server:

* ODA File Converter (free, Windows/Linux, opendesign.com) – recommended.
  Set KOSHA_ODA_CONVERTER to the full path of ODAFileConverter.exe.
* LibreDWG `dwg2dxf` – set KOSHA_DWG2DXF to its path.

If neither is configured, DWG files are still stored, versioned and
downloadable; they just show "preview not available".
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config


class DwgUnavailable(Exception):
    pass


def converter_status() -> dict:
    oda = config.ODA_CONVERTER or shutil.which("ODAFileConverter") or ""
    lib = config.DWG2DXF or shutil.which("dwg2dxf") or ""
    return {
        "oda": oda if oda and Path(oda).exists() else "",
        "libredwg": lib if lib and Path(lib).exists() else "",
    }


def to_dxf(dwg_bytes: bytes, timeout: int = 180) -> bytes:
    st = converter_status()
    if not st["oda"] and not st["libredwg"]:
        raise DwgUnavailable(
            "DWG preview needs the free ODA File Converter on the server "
            "(set KOSHA_ODA_CONVERTER) or LibreDWG dwg2dxf (KOSHA_DWG2DXF)."
        )
    config.ensure_dirs()
    with tempfile.TemporaryDirectory(dir=config.WORK_DIR) as tmp:
        src_dir = Path(tmp) / "in"
        out_dir = Path(tmp) / "out"
        src_dir.mkdir()
        out_dir.mkdir()
        src = src_dir / "drawing.dwg"
        src.write_bytes(dwg_bytes)
        errors = []
        if st["oda"]:
            # ODAFileConverter <in dir> <out dir> <out ver> <out type> <recurse> <audit> [filter]
            cmd = [st["oda"], str(src_dir), str(out_dir), "ACAD2018", "DXF", "0", "1", "*.DWG"]
            env = dict(os.environ)
            env.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless on Linux servers
            try:
                subprocess.run(cmd, check=False, timeout=timeout, capture_output=True, env=env)
            except Exception as e:  # pragma: no cover
                errors.append(f"ODA: {e}")
            out = next(out_dir.glob("*.dxf"), None) or next(out_dir.glob("*.DXF"), None)
            if out and out.stat().st_size > 0:
                return out.read_bytes()
            errors.append("ODA File Converter produced no output")
        if st["libredwg"]:
            out = out_dir / "drawing.dxf"
            try:
                subprocess.run([st["libredwg"], "-y", "-o", str(out), str(src)], check=False,
                               timeout=timeout, capture_output=True)
            except Exception as e:  # pragma: no cover
                errors.append(f"dwg2dxf: {e}")
            if out.exists() and out.stat().st_size > 0:
                return out.read_bytes()
            errors.append("dwg2dxf produced no output")
        raise DwgUnavailable("; ".join(errors) or "DWG conversion failed")


def dwg_version(data: bytes) -> str:
    """Human name of the DWG release from the 6-byte header."""
    code = data[:6].decode("ascii", "ignore")
    return {
        "AC1015": "AutoCAD 2000", "AC1018": "AutoCAD 2004", "AC1021": "AutoCAD 2007",
        "AC1024": "AutoCAD 2010", "AC1027": "AutoCAD 2013", "AC1032": "AutoCAD 2018",
    }.get(code, code or "unknown")


def batch_to_dxf(paths: list, chunk: int = 200, timeout: int = 3600) -> dict:
    """Convert many DWG files with one converter run per chunk (much faster than
    one process per file). Returns {source path: dxf bytes} for files that converted."""
    st = converter_status()
    out: dict = {}
    if not st["oda"]:
        return out  # LibreDWG has no folder mode; files are converted one by one later
    config.ensure_dirs()
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    for start in range(0, len(paths), chunk):
        part = paths[start:start + chunk]
        with tempfile.TemporaryDirectory(dir=config.WORK_DIR) as tmp:
            src_dir = Path(tmp) / "in"
            dst_dir = Path(tmp) / "out"
            src_dir.mkdir()
            dst_dir.mkdir()
            names = {}
            for k, p in enumerate(part):
                name = f"f{k:05d}.dwg"
                try:
                    shutil.copyfile(p, src_dir / name)
                    names[name[:-4]] = p
                except OSError:
                    continue
            try:
                subprocess.run([st["oda"], str(src_dir), str(dst_dir), "ACAD2018", "DXF", "0", "1", "*.DWG"],
                               check=False, timeout=timeout, capture_output=True, env=env)
            except Exception:  # pragma: no cover
                continue
            for f in dst_dir.glob("*"):
                if f.suffix.lower() == ".dxf" and f.stem in names and f.stat().st_size > 0:
                    out[names[f.stem]] = f.read_bytes()
    return out
