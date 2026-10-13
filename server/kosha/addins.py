# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add-in loader, registry and guard.

Finds add-ins (the official ones shipped in `addins/`, folders the admin lists in
KOSHA_ADDIN_DIR, and Python packages that declare a `kosha.addins` entry point), checks
their manifests and Kosha version range, and runs every call into an add-in through one
guard: a time-out, a broad catch, a log line and a failure count. An add-in that keeps
failing is switched off automatically and the admin is told why; Kosha keeps working.

Official add-ins are enabled on first start. Any other add-in waits until an admin has
seen the permissions it asks for and enabled it.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__, config, db

from kosha_sdk import AddinContext, AddinError, Manifest, ManifestError, describe_permission, load_manifest  # noqa: E402
from kosha_sdk import events as sdk_events  # noqa: E402
from kosha_sdk.manifest import MANIFEST_NAME  # noqa: E402
from kosha_sdk.versions import satisfies  # noqa: E402

log = logging.getLogger("kosha.addins")
STATE_KEY = "addins"
SECRET_MASK = "••••••••"
OFFICIAL_PUBLISHER = "Kosha project"


class AddinUnavailable(AddinError):
    """The add-in is switched off, missing, too slow or crashed. The message says which."""


@dataclass
class LoadedAddin:
    manifest: Manifest | None
    folder: Path
    source: str                       # bundled | local | installed
    status: str = "disabled"          # enabled | disabled | awaiting_approval | incompatible | invalid | desktop
    message: str = ""
    failures: int = 0
    handlers: list = field(default_factory=list)
    instance: Any = None

    @property
    def id(self) -> str:
        return self.manifest.id if self.manifest else f"invalid:{self.folder.name}"

    def to_dict(self) -> dict:
        m = self.manifest
        return {
            "id": self.id, "name": m.name if m else self.folder.name, "version": m.version if m else "",
            "publisher": m.publisher if m else "", "licence": m.licence if m else "",
            "kinds": list(m.kinds) if m else [], "description": m.description if m else "",
            "source": self.source, "status": self.status, "message": self.message, "failures": self.failures,
            "official": bool(m and m.publisher == OFFICIAL_PUBLISHER and self.source == "bundled"),
            "runs_on": sorted(m.entry_points) if m else [],
            "permissions": [{"name": p, "label": describe_permission(p)} for p in (m.permissions if m else ())],
            "events": list(m.events) if m else [],
            "has_settings": bool(m and m.settings),
        }


class Registry:
    def __init__(self) -> None:
        self._addins: dict[str, LoadedAddin] = {}
        self._listeners: dict[str, list] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="addin")

    # ------------------------------------------------------------ discovery
    def discover(self) -> list[LoadedAddin]:
        found: list[tuple[Path, str]] = []
        if config.BUNDLED_ADDIN_DIR.is_dir():
            found += [(p.parent, "bundled") for p in sorted(config.BUNDLED_ADDIN_DIR.glob(f"*/{MANIFEST_NAME}"))]
        for d in config.ADDIN_DIRS:
            found += [(p.parent, "local") for p in sorted(Path(d).glob(f"*/{MANIFEST_NAME}"))]
        found += [(p, "installed") for p in self._installed_folders()]
        state = self._state()
        with self._lock:
            self._addins.clear()
            for folder, source in found:
                a = self._load_one(folder, source, state)
                if a.id in self._addins:
                    log.warning("Add-in %s found twice; keeping %s", a.id, self._addins[a.id].folder)
                    continue
                self._addins[a.id] = a
        log.info("Add-ins: %s", ", ".join(f"{a.id} ({a.status})" for a in self._addins.values()) or "none")
        return self.all()

    @staticmethod
    def _installed_folders() -> list[Path]:
        out = []
        try:
            eps = importlib.metadata.entry_points(group="kosha.addins")
        except Exception:  # pragma: no cover - broken environment metadata
            log.exception("Could not read installed add-ins")
            return out
        for ep in eps:
            try:
                spec = importlib.util.find_spec(ep.value.split(":")[0])
                if spec and spec.origin:
                    pkg = Path(spec.origin).parent
                    out.append(pkg if (pkg / MANIFEST_NAME).exists() else pkg.parent)
            except Exception:
                log.exception("Installed add-in %s could not be located", ep.name)
        return out

    def _load_one(self, folder: Path, source: str, state: dict) -> LoadedAddin:
        try:
            m = load_manifest(folder)
        except ManifestError as e:
            return LoadedAddin(None, folder, source, "invalid", f"Manifest problem: {e}")
        a = LoadedAddin(m, folder, source)
        st = state.get(m.id, {})
        a.failures = int(st.get("failures", 0))
        if not satisfies(__version__, m.kosha):
            a.status, a.message = "incompatible", f"Made for Kosha {m.kosha}; this server is {__version__}."
            return a
        if not m.runs_on_server:
            a.status, a.message = "desktop", "Runs on users' PCs; install it there (see its README)."
            return a
        official = m.publisher == OFFICIAL_PUBLISHER and source == "bundled"
        approved = set(st.get("approved_permissions", m.permissions if official else ()))
        enabled = st.get("enabled", official)
        if not set(m.permissions) <= approved:
            a.status, a.message = "awaiting_approval", "An admin must review its permissions and enable it."
        elif enabled:
            a.status = "enabled"
        else:
            a.status, a.message = "disabled", st.get("disabled_reason", "")
        return a

    # ------------------------------------------------------------- queries
    def all(self) -> list[LoadedAddin]:
        with self._lock:
            return list(self._addins.values())

    def get(self, addin_id: str) -> LoadedAddin | None:
        with self._lock:
            return self._addins.get(addin_id)

    def by_kind(self, kind: str, enabled_only: bool = True) -> list[LoadedAddin]:
        return [a for a in self.all() if a.manifest and kind in a.manifest.kinds
                and (a.status == "enabled" or not enabled_only)]

    # --------------------------------------------------------------- state
    @staticmethod
    def _state() -> dict:
        return db.get_setting(STATE_KEY) or {}

    def _save_state(self, addin_id: str, **changes) -> None:
        state = self._state()
        state.setdefault(addin_id, {}).update(changes)
        db.set_setting(STATE_KEY, state)

    def enable(self, addin_id: str, user_id: int | None) -> LoadedAddin:
        a = self._require(addin_id)
        if a.status in ("incompatible", "invalid", "desktop"):
            raise AddinUnavailable(a.message or "This add-in cannot run on the server.")
        with self._lock:
            self._save_state(a.id, enabled=True, approved_permissions=list(a.manifest.permissions), failures=0,
                             disabled_reason="")
            a.status, a.message, a.failures, a.instance = "enabled", "", 0, None
        db.audit(user_id, "addin_enable", "addin", None, f"{a.id}: {', '.join(a.manifest.permissions)}")
        return a

    def disable(self, addin_id: str, user_id: int | None, reason: str = "Switched off by an admin.") -> LoadedAddin:
        a = self._require(addin_id)
        with self._lock:
            self._save_state(a.id, enabled=False, disabled_reason=reason)
            if a.status == "enabled":
                a.status = "disabled"
            a.message, a.instance = reason, None
        db.audit(user_id, "addin_disable", "addin", None, f"{a.id}: {reason}")
        return a

    def _require(self, addin_id: str) -> LoadedAddin:
        a = self.get(addin_id)
        if not a or not a.manifest:
            raise AddinUnavailable(f"No add-in called {addin_id} is installed.")
        return a

    # ------------------------------------------------------------ settings
    def settings(self, addin_id: str) -> dict:
        a = self._require(addin_id)
        values = a.manifest.defaults()
        values.update(db.get_setting(f"addin:{addin_id}") or {})
        return values

    def masked_settings(self, addin_id: str) -> dict:
        a = self._require(addin_id)
        values = self.settings(addin_id)
        for s in a.manifest.settings:
            if s.type == "secret" and values.get(s.key):
                values[s.key] = SECRET_MASK
        return values

    def save_settings(self, addin_id: str, changes: dict, user_id: int | None) -> dict:
        a = self._require(addin_id)
        values = self.settings(addin_id)
        for key, raw in changes.items():
            s = a.manifest.setting(key)
            if s is None or (s.type == "secret" and raw == SECRET_MASK):
                continue
            try:
                values[key] = s.coerce(raw)
            except ManifestError as e:
                raise AddinError(str(e)) from None
        db.set_setting(f"addin:{addin_id}", values)
        a.instance = None   # rebuilt with the new settings on next use
        db.audit(user_id, "addin_settings", "addin", None, a.id)
        return self.masked_settings(addin_id)

    # ---------------------------------------------------------------- calls
    def _instance(self, a: LoadedAddin) -> Any:
        if a.instance is not None:
            return a.instance
        target = a.manifest.entry_points["server"]
        module_name, cls_name = target.split(":")
        if a.source != "installed" and str(a.folder) not in sys.path:
            sys.path.insert(0, str(a.folder))
        before = len(sdk_events.registered())
        module = importlib.import_module(module_name)
        a.handlers = [h for h in sdk_events.registered()[before:] if h[0] in a.manifest.events]
        cls = getattr(module, cls_name)
        workdir = config.DATA_DIR / "addins" / a.id
        workdir.mkdir(parents=True, exist_ok=True)
        ctx = AddinContext(addin_id=a.id, settings=self.settings(a.id), workdir=workdir,
                           log=logging.getLogger(f"kosha.addin.{a.id}"))
        a.instance = cls(ctx)
        return a.instance

    def call(self, addin_id: str, method: str, *args: Any, timeout: float | None = None, **kwargs: Any) -> Any:
        """Call one method of an enabled server add-in, safely. Raises AddinError with a plain message."""
        a = self._require(addin_id)
        name = a.manifest.name
        if a.status != "enabled":
            raise AddinUnavailable(f"The {name} add-in is switched off. {a.message}".strip())
        limit = timeout or config.ADDIN_TIMEOUT_SECONDS
        try:
            inst = self._instance(a)
            fn = getattr(inst, method, None)
            if fn is None:
                raise AddinUnavailable(f"{name} does not offer '{method}'.")
            future = self._pool.submit(fn, *args, **kwargs)
            result = future.result(timeout=limit)
        except FutureTimeout:
            self._failed(a, f"{method} did not finish within {limit:.0f} s")
            raise AddinUnavailable(f"{name} did not answer within {limit:.0f} seconds. Nothing was changed in Kosha; "
                                   "the job can be retried.") from None
        except AddinError:
            self._succeeded(a)          # an expected, explained failure: the add-in itself is working
            raise
        except Exception as e:
            log.exception("Add-in %s crashed in %s", a.id, method)
            self._failed(a, f"{method}: {e.__class__.__name__}: {e}")
            raise AddinUnavailable(f"{name} failed unexpectedly ({e.__class__.__name__}). Nothing was changed in "
                                   "Kosha; the admin can see the details in the log.") from None
        self._succeeded(a)
        return result

    def _succeeded(self, a: LoadedAddin) -> None:
        if a.failures:
            a.failures = 0
            self._save_state(a.id, failures=0)

    def _failed(self, a: LoadedAddin, what: str) -> None:
        with self._lock:
            a.failures += 1
            self._save_state(a.id, failures=a.failures)
            if a.failures >= config.ADDIN_MAX_FAILURES and a.status == "enabled":
                reason = f"Switched off automatically after {a.failures} failures in a row. Last: {what}"
                log.warning("Add-in %s: %s", a.id, reason)
                self.disable(a.id, None, reason)

    # --------------------------------------------------------------- events
    def listen(self, event: str, fn) -> None:
        """Let a core feature (notices, for example) react to the same events as add-ins, in the background."""
        with self._lock:
            fns = self._listeners.setdefault(event, [])
            if fn not in fns:
                fns.append(fn)

    def emit(self, event: str, payload: dict) -> None:
        """Run add-in handlers for an event in the background, after the change is committed."""
        for fn in list(self._listeners.get(event, ())):
            self._pool.submit(self._run_listener, fn, event, dict(payload))
        for a in self.all():
            if a.status != "enabled" or event not in a.manifest.events:
                continue
            try:
                self._instance(a)
            except Exception:
                log.exception("Add-in %s could not load for event %s", a.id, event)
                continue
            for name, handler in a.handlers:
                if name == event:
                    self._pool.submit(self._run_handler, a, handler, event, dict(payload))

    @staticmethod
    def _run_listener(fn, event: str, payload: dict) -> None:
        try:
            fn({"event": event, **payload})
        except Exception:
            log.exception("Kosha's own handler for %s failed; the change itself is safe", event)

    def _run_handler(self, a: LoadedAddin, handler, event: str, payload: dict) -> None:
        try:
            handler(a.instance.ctx if hasattr(a.instance, "ctx") else None, {"event": event, **payload})
            self._succeeded(a)
        except AddinError as e:
            log.info("Add-in %s declined event %s: %s", a.id, event, e)
        except Exception as e:
            log.exception("Add-in %s failed on event %s", a.id, event)
            self._failed(a, f"event {event}: {e.__class__.__name__}")


registry = Registry()
