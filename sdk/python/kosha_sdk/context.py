# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""What the server hands to an add-in: its settings, a logger, a work folder and safe HTTP."""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .http import HttpClient


@dataclass(frozen=True)
class Health:
    """Result of a health check, shown on the admin's add-in page."""
    ok: bool
    message: str = ""

    @classmethod
    def up(cls, message: str = "Working") -> "Health":
        return cls(True, message)

    @classmethod
    def down(cls, message: str) -> "Health":
        return cls(False, message)


@dataclass
class AddinContext:
    """Created by the server (or by tests) for one add-in.

    Add-ins use the context instead of global state: settings come from the admin's form,
    files go under `workdir`, HTTP goes through `http()` so time-outs and retries are always set.
    """
    addin_id: str
    settings: dict[str, Any] = field(default_factory=dict)
    workdir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("kosha.addin"))
    notify_fn: Callable[..., None] | None = None

    def http(self, base_url: str, *, headers: dict[str, str] | None = None, timeout_s: float = 10.0,
             verify_tls: bool = True, retries: int = 3) -> HttpClient:
        return HttpClient(base_url, headers=headers, timeout_s=timeout_s, verify_tls=verify_tls, retries=retries)

    def notify(self, **kwargs: Any) -> None:
        """Send an in-app notification through the server, when the add-in has `notify:send`."""
        if self.notify_fn is None:
            self.log.info("notification (no server attached): %s", kwargs)
            return
        self.notify_fn(addin_id=self.addin_id, **kwargs)
