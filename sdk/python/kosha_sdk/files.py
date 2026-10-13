# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Add-ins about files: file-type handlers (previews for more formats), storage (a second copy of every stored
file somewhere else) and importers (files and part data from another system into a Kosha import).

    class Handler(FileTypeHandler):            # kind: file-type, permission files:preview
        extensions = ("ipt", "iam")
        def preview(self, filename, data):
            return FilePreview(summary={"type": "text", "title": filename, "lines": ["..."]})

    class Copies(StorageProvider):             # kind: storage, permission storage:copy
        def put(self, key, data): ...
        def get(self, key): ...
        def exists(self, key): ...

    class OldPdm(Importer):                    # kind: importer, permission import:provide
        def collect(self, dest, options):
            (dest / "PUMP-001 Casing.dxf").write_bytes(...)
            return [{"filename": "PUMP-001 Casing.dxf", "part_number": "PUMP-001", "description": "CASING"}]

Kosha calls every method through its add-in guard (time-out, failure count, automatic switch-off), so a slow
or broken add-in never stops a check-in. Raise AddinError with a plain message when something fails.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import AddinContext, Health

SUMMARY_TYPES = ("text", "table", "archive", "email", "document", "slides", "model")
IMAGE_KINDS = ("png", "jpg", "svg")


@dataclass
class FilePreview:
    """What a file-type handler shows for one file.

    `summary` is shown in Kosha's viewer, one of SUMMARY_TYPES: {"type": "text", "title": "...", "lines": [...]},
    {"type": "table", "rows": [[...], ...]}, {"type": "archive", "entries": [{"name", "size"}]},
    {"type": "email", "headers": {...}, "lines": [...], "attachments": [{"name", "size"}]}, and so on. Kosha
    keeps only these keys and cuts long lists. `image` is a thumbnail: ("png" | "jpg" | "svg", bytes).
    `meta` holds a few facts shown with the file (strings or numbers)."""
    summary: dict[str, Any] | None = None
    image: tuple[str, bytes] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class FileTypeHandler(ABC):
    """Previews for file types Kosha does not read itself. `extensions` lists them, without the dot."""

    extensions: tuple[str, ...] = ()

    def __init__(self, ctx: AddinContext):
        self.ctx = ctx

    def health(self) -> Health:
        return Health.up(f"Previews for {', '.join(self.extensions) or 'no file types'}")

    @abstractmethod
    def preview(self, filename: str, data: bytes) -> FilePreview | None:
        """The preview of one file, or None when there is nothing to show."""


class StorageProvider(ABC):
    """A second place for Kosha's stored files (another disk, a NAS, an object store). Kosha's own file store
    stays the master copy; this one is filled in the background and used to bring back a file that went
    missing or was damaged. Keys are the files' SHA-256 fingerprints, so a copy can always be checked."""

    def __init__(self, ctx: AddinContext):
        self.ctx = ctx

    @abstractmethod
    def health(self) -> Health:
        """Check that the place can be reached and written to."""

    @abstractmethod
    def put(self, key: str, data: bytes) -> None:
        """Keep `data` under `key`. Called again for the same key only when an earlier copy was not confirmed."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """The data kept under `key`. Raise AddinError when it is not there."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Whether something is kept under `key`."""


class Importer(ABC):
    """Brings files and part data from another system (an old PDM, a document server) into a Kosha import. The
    import then runs like a folder import: quality scores, review, sign-off and Undo batch all apply."""

    def __init__(self, ctx: AddinContext):
        self.ctx = ctx

    def health(self) -> Health:
        return Health.up("Ready to import")

    @abstractmethod
    def collect(self, dest: Path, options: dict[str, Any]) -> list[dict[str, Any]]:
        """Write the files into `dest` (sub-folders become Kosha folders) and return one row per part or file,
        with the keys of a part master spreadsheet: filename, part_number, description, material, revision,
        uom, item_type, make_buy, weight_kg. Rows may be empty when the files carry their own data."""
