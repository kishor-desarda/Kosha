# SPDX-License-Identifier: AGPL-3.0-or-later
"""Password hashing (stdlib scrypt) and signed session tokens (JWT)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import jwt

from . import config

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def make_token(user_id: int, hours: int | None = None) -> str:
    hours = hours or config.TOKEN_HOURS
    payload = {"sub": str(user_id), "exp": int(time.time()) + hours * 3600, "iat": int(time.time())}
    return jwt.encode(payload, config.secret_key(), algorithm="HS256")


def verify_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, config.secret_key(), algorithms=["HS256"])
        return int(payload["sub"])
    except Exception:
        return None
