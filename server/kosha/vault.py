# SPDX-License-Identifier: AGPL-3.0-or-later
"""Secrets kept encrypted in the database (specification 15.4): ERP, AI and directory keys of add-ins, the mail
server password and webhook signing secrets.

A sealed value looks like ``enc1:<base64>``. The key is derived from the installation's secret key (``secret.key`` in
the data folder, or ``KOSHA_SECRET``), which is kept apart from the database: a copy of the database alone does not
reveal the secrets. The cipher uses the standard library only: HMAC-SHA256 as a keystream in counter mode with a
random nonce, then HMAC-SHA256 over nonce and ciphertext (encrypt-then-MAC), so a changed value is refused rather
than read wrongly. Values stored before this was added are read as they are and sealed at the next start.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from . import config

log = logging.getLogger("kosha.vault")
PREFIX = "enc1:"
NONCE = 16
TAG = 32


class VaultError(ValueError):
    """A sealed value that cannot be opened (changed, or sealed with another installation's key)."""


def _keys() -> tuple[bytes, bytes]:
    root = hashlib.sha256(("kosha-vault|" + config.secret_key()).encode()).digest()
    return (hmac.new(root, b"encrypt", hashlib.sha256).digest(), hmac.new(root, b"authenticate", hashlib.sha256).digest())


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def sealed(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(value: str) -> str:
    if value in (None, "") or sealed(value):
        return value
    enc, mac = _keys()
    data = str(value).encode("utf-8")
    nonce = secrets.token_bytes(NONCE)
    ct = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
    tag = hmac.new(mac, nonce + ct, hashlib.sha256).digest()
    return PREFIX + base64.urlsafe_b64encode(nonce + ct + tag).decode()


def unseal(value):
    """The plain value (values that were never sealed come back as they are)."""
    if not sealed(value):
        return value
    enc, mac = _keys()
    try:
        raw = base64.urlsafe_b64decode(value[len(PREFIX):].encode())
    except ValueError:
        raise VaultError("A stored secret is damaged.") from None
    if len(raw) < NONCE + TAG:
        raise VaultError("A stored secret is damaged.")
    nonce, ct, tag = raw[:NONCE], raw[NONCE:-TAG], raw[-TAG:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + ct, hashlib.sha256).digest()):
        raise VaultError("A stored secret cannot be opened: it was changed, or it belongs to another installation "
                         "(the secret key does not match). Enter the secret again.")
    return bytes(a ^ b for a, b in zip(ct, _stream(enc, nonce, len(ct)))).decode("utf-8")


def seal_existing() -> int:
    """At start: seal the secrets stored before encryption was added. Returns how many were sealed."""
    from . import db
    n = 0
    smtp = db.get_setting("smtp") or {}
    if smtp.get("password") and not sealed(smtp["password"]):
        smtp["password"] = seal(smtp["password"])
        db.set_setting("smtp", smtp)
        n += 1
    from .addins import registry
    for a in registry.all():
        if not a.manifest:
            continue
        stored = db.get_setting(f"addin:{a.id}") or {}
        changed = False
        for s in a.manifest.settings:
            if s.type == "secret" and stored.get(s.key) and not sealed(stored[s.key]):
                stored[s.key] = seal(stored[s.key])
                changed = True
                n += 1
        if changed:
            db.set_setting(f"addin:{a.id}", stored)
    for h in db.rows("SELECT id, secret FROM webhooks WHERE secret NOT LIKE 'enc1:%'"):
        db.execute("UPDATE webhooks SET secret = ? WHERE id = ?", (seal(h["secret"]), h["id"]))
        n += 1
    if n:
        log.info("Sealed %d stored secret(s)", n)
    return n
