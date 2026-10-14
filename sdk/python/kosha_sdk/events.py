# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Event hooks. Handlers run in a background worker after the change is committed.

    @on_event("revision.released")
    def tell_purchasing(ctx, event): ...

Handlers must be idempotent: after a crash the same event may be delivered again.
"""
from __future__ import annotations

from typing import Any, Callable

EVENTS = {
    "revision.released": "a file revision was released",
    "bom.released": "a BOM was released",
    "mbom.released": "a manufacturing BOM (with its process plan) was released",
    "change.implemented": "a change order was implemented",
    "item.created": "a new item was created",
    "deviation.approved": "a deviation (a temporary departure from the released design) was approved",
    "change.submitted": "a change order was submitted for approval",
    "erp.job_failed": "a transfer to the ERP failed (it is kept for a retry)",
}

Handler = Callable[[Any, dict], None]
_HANDLERS: list[tuple[str, Handler]] = []


def on_event(name: str) -> Callable[[Handler], Handler]:
    if name not in EVENTS:
        raise ValueError(f"unknown event {name!r}; known: {', '.join(sorted(EVENTS))}")

    def wrap(fn: Handler) -> Handler:
        _HANDLERS.append((name, fn))
        return fn
    return wrap


def registered() -> list[tuple[str, Handler]]:
    """All handlers registered so far (the server attributes them to add-ins by module)."""
    return list(_HANDLERS)
