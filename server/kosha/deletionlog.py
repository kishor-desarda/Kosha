# SPDX-License-Identifier: AGPL-3.0-or-later
"""Independent plain-text journal for permanent deletion operations.

The journal deliberately stores descriptive snapshots, never database IDs or foreign keys.
An unresolved PREPARED entry is reconciled against the database by part number and creation
timestamp. This is crash recovery, not tamper-proofing; integrity hardening is tracked separately.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid

from . import config, db

log = logging.getLogger("kosha.deletionlog")
_LOCK = threading.Lock()
LOG_NAME = "permanent-deletions.jsonl"
_TERMINAL = {"COMMITTED", "ABORTED"}


class DeletionLogError(RuntimeError):
    """The deletion journal is corrupt or cannot be reconciled safely."""


def log_path():
    return config.DATA_DIR / LOG_NAME


def _append(record: dict) -> None:
    """Append one durable UTF-8 JSONL record (plain text, one event per line)."""
    config.ensure_dirs()
    path = log_path()
    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with _LOCK:
        # A process can stop between writing the JSON bytes and its final newline.
        # Preserve a complete JSON record; discard only an incomplete trailing fragment.
        if path.exists():
            raw = path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                tail = raw.rsplit(b"\n", 1)[-1]
                try:
                    json.loads(tail.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    keep = len(raw) - len(tail)
                    with path.open("r+b") as existing:
                        existing.truncate(keep)
                else:
                    with path.open("ab") as existing:
                        existing.write(b"\n")
                        existing.flush()
                        os.fsync(existing.fileno())
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        fd = os.open(str(path), flags, 0o600)
        try:
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError("Could not append the complete deletion journal record.")
            os.fsync(fd)
        finally:
            os.close(fd)


def read_events() -> list[dict]:
    """Read complete records. An incomplete final line is ignored for crash recovery."""
    path = log_path()
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw:
        return []
    chunks = raw.split(b"\n")
    complete = chunks[:-1]
    tail = b"" if raw.endswith(b"\n") else chunks[-1]
    out = []
    for line in complete:
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeletionLogError("The permanent-deletion log contains a malformed complete record.") from exc
        if not isinstance(event, dict) or not event.get("event") or not event.get("operation_id"):
            raise DeletionLogError("The permanent-deletion log contains an invalid record.")
        out.append(event)
    if tail.strip():
        try:
            event = json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        else:
            if isinstance(event, dict) and event.get("event") and event.get("operation_id"):
                out.append(event)
    return out


def _item_snapshot(item: dict) -> dict:
    # created_at is descriptive recovery evidence, not a database identifier.
    fields = ("part_number", "description", "revision", "item_type", "material", "uom", "state", "created_at", "updated_at")
    return {key: item.get(key) for key in fields}


def _folder_path(folder_id) -> str:
    """Return a human-readable folder path, never a folder database ID."""
    parts = []
    seen = set()
    while folder_id and folder_id not in seen:
        seen.add(folder_id)
        folder = db.one("SELECT parent_id, name FROM folders WHERE id = ?", (folder_id,))
        if not folder:
            break
        parts.append(folder["name"])
        folder_id = folder["parent_id"]
    return "/".join(reversed(parts))


def _document_snapshot(document: dict) -> dict:
    fields = ("filename", "part_number", "description", "revision", "doc_type", "created_at")
    return {**{key: document.get(key) for key in fields},
            "folder_path": _folder_path(document.get("folder_id"))}


class DeletionJournal:
    """Context manager spanning the database transaction and its external journal."""

    def __init__(self):
        self.record: dict | None = None
        self.status: str | None = None
        self.operation_id: str | None = None

    def __enter__(self):
        return self

    def prepare(self, *, actor: str, reason: str, items: list[dict], documents: list[dict]) -> str:
        if self.record is not None:
            raise DeletionLogError("This deletion operation has already been prepared.")
        snapshots = [_item_snapshot(item) for item in items]
        if not snapshots:
            raise DeletionLogError("A permanent deletion journal requires at least one item.")
        document_snapshots = [_document_snapshot(doc) for doc in documents]
        record = {
            "event": "PREPARED",
            "operation_id": uuid.uuid4().hex,
            "timestamp": db.now(),
            "actor": str(actor or "Unknown user")[:120],
            "reason": str(reason or "User initiated permanent deletion")[:500],
            "item_count": len(snapshots),
            "document_count": len(document_snapshots),
            "items": snapshots,
            "documents": document_snapshots,
        }
        _append(record)
        self.record = record
        self.operation_id = record["operation_id"]
        return self.operation_id

    def __exit__(self, exc_type, exc, tb):
        if self.record is None:
            return False
        try:
            status = "COMMITTED" if exc_type is None else _classify(self.record)
            _append({
                "event": status,
                "operation_id": self.operation_id,
                "timestamp": db.now(),
                "note": "Database transaction committed." if status == "COMMITTED"
                        else "Database transaction did not commit.",
            })
            self.status = status
        except Exception:
            # Leave PREPARED in place. Startup reconciliation will retry rather than
            # risk claiming a result that the database state cannot support.
            self.status = "PENDING_RECOVERY"
            log.exception("Could not finalize permanent-deletion journal operation %s", self.operation_id)
        return False


def _classify(record: dict) -> str:
    """Infer the transaction outcome without a database-side operation link."""
    item_presence = []
    for item in record.get("items") or []:
        part_number = item.get("part_number")
        created_at = item.get("created_at")
        if not part_number or not created_at:
            raise DeletionLogError("A prepared deletion record lacks recovery details.")
        rows = db.rows(
            "SELECT created_at, updated_at, description, revision, item_type, material, uom, state "
            "FROM items WHERE part_number = ? COLLATE NOCASE", (part_number,)
        )
        snapshot_fields = ("created_at", "updated_at", "description", "revision", "item_type", "material", "uom", "state")
        if any(all(row[key] == item.get(key) for key in snapshot_fields) for row in rows):
            item_presence.append(True)
        elif rows:
            raise DeletionLogError(
                f"Cannot reconcile deletion of {part_number}: that part number now belongs to another record."
            )
        else:
            item_presence.append(False)

    document_presence = []
    for document in record.get("documents") or []:
        filename = document.get("filename")
        if not filename or not document.get("created_at"):
            raise DeletionLogError("A prepared deletion record lacks document recovery details.")
        rows = db.rows(
            "SELECT filename, part_number, description, revision, doc_type, created_at, folder_id "
            "FROM documents WHERE filename = ? COLLATE NOCASE", (filename,)
        )
        same_folder = [row for row in rows if _folder_path(row["folder_id"]) == document.get("folder_path")]
        fields = ("filename", "part_number", "description", "revision", "doc_type", "created_at")
        if any(all(row[key] == document.get(key) for key in fields) for row in same_folder):
            document_presence.append(True)
        elif same_folder:
            raise DeletionLogError(
                f"Cannot reconcile deletion of {filename}: the original document has changed."
            )
        else:
            document_presence.append(False)

    all_present = item_presence + document_presence
    if all_present and all(all_present):
        return "ABORTED"
    if all_present and not any(all_present):
        return "COMMITTED"
    raise DeletionLogError("A prepared deletion is only partially reflected in the database; manual review is required.")


def reconcile() -> int:
    """Resolve PREPARED records left by a crash, before the application starts serving requests."""
    prepared: dict[str, dict] = {}
    terminal: set[str] = set()
    for event in read_events():
        operation_id = event["operation_id"]
        if event["event"] == "PREPARED":
            prepared[operation_id] = event
        elif event["event"] in _TERMINAL:
            terminal.add(operation_id)
    recovered = 0
    for operation_id, record in prepared.items():
        if operation_id in terminal:
            continue
        status = _classify(record)
        _append({
            "event": status,
            "operation_id": operation_id,
            "timestamp": db.now(),
            "note": "Recovered during startup by checking the original part numbers and creation timestamps.",
        })
        recovered += 1
        log.warning("Reconciled interrupted permanent deletion %s as %s", operation_id, status)
    return recovered
