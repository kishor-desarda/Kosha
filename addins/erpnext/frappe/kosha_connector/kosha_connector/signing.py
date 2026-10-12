# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Signed calls from ERPNext to Kosha. The same file is in kosha_erpnext and the Frappe app (a test keeps them identical).

    X-Kosha-Timestamp: <unix seconds>
    X-Kosha-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<body>" with the shared secret>

A call older than five minutes is refused, so a recorded call cannot be replayed later.
"""
from __future__ import annotations

import hashlib
import hmac
import time

MAX_AGE_SECONDS = 300


def sign(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    ts = str(int(timestamp if timestamp is not None else time.time()))
    mac = hmac.new(secret.encode("utf-8"), ts.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Kosha-Timestamp": ts, "X-Kosha-Signature": f"sha256={mac}"}


def verify(secret: str, headers: dict[str, str], body: bytes, now: float | None = None) -> str:
    """Returns '' when genuine, otherwise the reason it was refused."""
    if not secret:
        return "No webhook secret is set in Kosha, so calls from the ERP are refused."
    h = {k.lower(): v for k, v in headers.items()}
    ts, sig = h.get("x-kosha-timestamp", ""), h.get("x-kosha-signature", "")
    if not ts.isdigit() or not sig.startswith("sha256="):
        return "The call is not signed."
    if abs((now if now is not None else time.time()) - int(ts)) > MAX_AGE_SECONDS:
        return "The call is too old (or the clocks differ by more than five minutes)."
    expected = sign(secret, body, int(ts))["X-Kosha-Signature"]
    return "" if hmac.compare_digest(expected, sig) else "The signature does not match."
