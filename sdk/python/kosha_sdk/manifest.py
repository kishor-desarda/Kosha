# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""The add-in manifest (`kosha-addin.yaml`): what an add-in is, what it needs and what it adds."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ManifestError
from .permissions import PERMISSIONS
from .versions import check_range

MANIFEST_NAME = "kosha-addin.yaml"

KINDS = {
    "erp-connector", "file-type", "pack", "ai-provider", "sign-in", "notifier", "storage", "importer",
    "cad-addin", "office-addin", "web-extension",
}
SETTING_TYPES = {"string", "url", "secret", "bool", "int", "choice"}
ENTRY_POINTS = {"server", "web", "cad", "office", "frappe"}
_ID = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*){2,}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$")
_ENTRY = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


@dataclass(frozen=True)
class Setting:
    key: str
    type: str
    label: str
    required: bool = False
    default: Any = None
    hint: str = ""
    choices: tuple[str, ...] = ()

    def coerce(self, value: Any) -> Any:
        """Turn a value from a form or file into the declared type, or raise ManifestError."""
        if value is None or value == "":
            return self.default if value is None else ("" if self.type in ("string", "url", "secret") else self.default)
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if self.type == "int":
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ManifestError(f"{self.label} must be a whole number") from None
        if self.type == "choice" and str(value) not in self.choices:
            raise ManifestError(f"{self.label} must be one of: {', '.join(self.choices)}")
        if self.type == "url" and not re.match(r"^https?://", str(value).strip()):
            raise ManifestError(f"{self.label} must start with http:// or https://")
        return str(value).strip()


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    publisher: str
    licence: str
    kosha: str
    kinds: tuple[str, ...]
    entry_points: dict[str, str]
    permissions: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    settings: tuple[Setting, ...] = ()
    ui: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    help: str = ""
    path: Path | None = None

    def setting(self, key: str) -> Setting | None:
        return next((s for s in self.settings if s.key == key), None)

    def defaults(self) -> dict[str, Any]:
        return {s.key: s.default for s in self.settings}

    @property
    def runs_on_server(self) -> bool:
        return "server" in self.entry_points

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version, "publisher": self.publisher,
            "licence": self.licence, "kosha": self.kosha, "kind": list(self.kinds),
            "entry_points": dict(self.entry_points), "permissions": list(self.permissions),
            "events": list(self.events), "description": self.description, "help": self.help,
            "settings": [s.__dict__ | {"choices": list(s.choices)} for s in self.settings], "ui": self.ui,
        }


def _req(data: dict, key: str, kind: type = str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise ManifestError(f"'{key}' is required")
    if not isinstance(data[key], kind):
        raise ManifestError(f"'{key}' must be a {kind.__name__}")
    return data[key]


def parse_manifest(data: Any, path: Path | None = None) -> Manifest:
    """Check a manifest already read from YAML or JSON. Raises ManifestError naming the first problem."""
    if not isinstance(data, dict):
        raise ManifestError("the manifest must be a mapping of keys to values")
    mid = _req(data, "id")
    if not _ID.match(mid):
        raise ManifestError("'id' must be a reverse domain name in lower case, like org.example.my-addin")
    version = str(_req(data, "version", (str, int, float)))
    if not _SEMVER.match(version):
        raise ManifestError("'version' must be a semantic version like 1.2.0")
    kosha = str(_req(data, "kosha"))
    try:
        check_range(kosha)
    except ValueError as e:
        raise ManifestError(f"'kosha': {e}") from None
    kinds = data.get("kind")
    kinds = [kinds] if isinstance(kinds, str) else kinds
    if not kinds or not isinstance(kinds, list):
        raise ManifestError("'kind' must list at least one kind of add-in")
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise ManifestError(f"unknown kind {unknown[0]!r}; known kinds: {', '.join(sorted(KINDS))}")
    eps = data.get("entry_points") or {}
    if not isinstance(eps, dict):
        raise ManifestError("'entry_points' must map a place (server, web, cad ...) to what runs there")
    for place, target in eps.items():
        if place not in ENTRY_POINTS:
            raise ManifestError(f"unknown entry point {place!r}; known: {', '.join(sorted(ENTRY_POINTS))}")
        if place == "server" and not _ENTRY.match(str(target)):
            raise ManifestError("'entry_points.server' must look like package.module:ClassName")
    perms = data.get("permissions") or []
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        raise ManifestError("'permissions' must be a list of names such as items:read")
    bad = [p for p in perms if p not in PERMISSIONS]
    if bad:
        raise ManifestError(f"unknown permission {bad[0]!r}; see kosha_sdk.permissions")
    settings = []
    for key, spec in (data.get("settings") or {}).items():
        spec = spec or {}
        stype = spec.get("type", "string")
        if stype not in SETTING_TYPES:
            raise ManifestError(f"setting {key!r}: type must be one of {', '.join(sorted(SETTING_TYPES))}")
        if stype == "choice" and not spec.get("choices"):
            raise ManifestError(f"setting {key!r}: a choice needs 'choices'")
        if stype == "secret" and spec.get("default"):
            raise ManifestError(f"setting {key!r}: a secret cannot have a default value")
        settings.append(Setting(key=key, type=stype, label=spec.get("label") or key.replace("_", " ").capitalize(),
                                required=bool(spec.get("required", False)), default=spec.get("default"),
                                hint=spec.get("hint", ""), choices=tuple(str(c) for c in spec.get("choices") or ())))
    return Manifest(
        id=mid, name=_req(data, "name"), version=version, publisher=_req(data, "publisher"),
        licence=_req(data, "licence"), kosha=kosha, kinds=tuple(kinds), entry_points={k: str(v) for k, v in eps.items()},
        permissions=tuple(perms), events=tuple(data.get("events") or ()), settings=tuple(settings),
        ui=dict(data.get("ui") or {}), description=str(data.get("description") or ""), help=str(data.get("help") or ""),
        path=path,
    )


def load_manifest(path: str | Path) -> Manifest:
    """Read and check `kosha-addin.yaml` (a folder or the file itself)."""
    p = Path(path)
    if p.is_dir():
        p = p / MANIFEST_NAME
    if not p.is_file():
        raise ManifestError(f"no {MANIFEST_NAME} at {p}")
    try:
        import yaml  # PyYAML, MIT
    except ImportError as e:  # pragma: no cover
        raise ManifestError("PyYAML is needed to read add-in manifests (pip install PyYAML)") from e
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ManifestError(f"{p.name} is not valid YAML: {e}") from None
    return parse_manifest(data, p)
