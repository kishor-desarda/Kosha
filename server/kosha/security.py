# SPDX-License-Identifier: AGPL-3.0-or-later
"""Password hashing (stdlib scrypt), the password policy, and signed session tokens (JWT).

A token names a user (``sub``) and a server-side session (``sid``); ``kosha.sessions`` decides whether that
session is still open, so signing out, a password change or an admin can end it at once.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import jwt

from . import config

_N, _R, _P = 2 ** 14, 8, 1

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
PASSWORD_HINT = "At least 10 characters. Not your user name and not a common password."

# Passwords that appear at the top of every leaked-password list (compared without regard to case). Short ones
# are already refused by the length rule, so only those of 10 or more characters are worth listing.
COMMON_PASSWORDS = frozenset("""
1234567890 0123456789 0987654321 9876543210 1234567891 1234512345 1122334455 1111111111 2222222222 0000000000
12345678910 123456789a a123456789 123456789q q123456789 1q2w3e4r5t 1q2w3e4r5t6y 1qaz2wsx3edc 1qazxsw23edc
zaq12wsxcde zaq1zaq1zaq1 qwertyuiop qwertyuiop123 qwerty1234 qwerty12345 qwerty123456 qwertyuiop1
asdfghjkl1 asdfghjkl123 zxcvbnm123 zxcvbnmasdf qwerasdfzxcv 1qaz2wsx3e abcdefghij abcdefghijk abcd123456
abc1234567 abcdef1234 aaaaaaaaaa password12 password123 password1234 password12345 password123456 password!1
password1! password01 passw0rd123 p@ssw0rd123 p@ssword123 p@ssw0rd1234 mypassword mypassword1 mypassword123
newpassword newpassword1 passwordpassword password2020 password2021 password2022 password2023 password2024
password2025 password2026 changeme123 changeme1234 changeit123 welcome123 welcome1234 welcome2024 welcome2025
welcome2026 letmein123 letmein1234 iloveyou12 iloveyou123 iloveyou1234 trustno1234 football123 baseball123
basketball soccer1234 superman123 batman1234 spiderman1 starwars123 sunshine123 princess123 princess12
dragon1234 monkey1234 master1234 shadow1234 michael123 jennifer12 whatever123 computer123 internet123
administrator admin12345 admin123456 admin1234567 adminadmin administrator1 administrator123 rootroot12
root123456 useruser12 user123456 test123456 testtest12 testing123 guest12345 default123 secret1234
secret12345 letmein!23 qwerty!234 summer2024 summer2025 summer2026 winter2024 winter2025 winter2026
spring2025 spring2026 autumn2025 autumn2026 january2026 october2026 december2025 company123 company1234
engineering engineering1 engineer123 solidworks solidworks1 autocad123 autocad2024 design1234 drawing123
kosha12345 koshaplm123 koshakosha kosha-admin admin-kosha kosha2026 plm1234567 plmplmplm1
""".split())


def password_problem(password: str, username: str = "") -> str | None:
    """Why a new password is not acceptable, in plain words; None when it is fine."""
    pw = password or ""
    if len(pw) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters for the password."
    if len(pw) > PASSWORD_MAX_LENGTH:
        return f"Use at most {PASSWORD_MAX_LENGTH} characters for the password."
    if username and pw.strip().casefold() == username.strip().casefold():
        return "The password can't be the same as the user name."
    if pw.casefold() in COMMON_PASSWORDS or len(set(pw)) <= 2:
        return "This password is too common and easy to guess. Choose another one, for example three unrelated words."
    return None


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


_dummy_hash: str | None = None


def waste_verify(password: str) -> None:
    """Spend the same time as a real password check, so the answer time does not reveal unknown user names."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(base64.b64encode(os.urandom(12)).decode())
    verify_password(password or "", _dummy_hash)


def make_token(user_id: int, hours: float | None = None, sid: str | None = None) -> str:
    hours = hours or config.TOKEN_HOURS
    now = int(time.time())
    payload = {"sub": str(user_id), "exp": now + int(hours * 3600), "iat": now}
    if sid:
        payload["sid"] = sid
    return jwt.encode(payload, config.secret_key(), algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """The token's claims when its signature and expiry are valid, else None."""
    try:
        payload = jwt.decode(token, config.secret_key(), algorithms=["HS256"])
        int(payload["sub"])
        return payload
    except Exception:
        return None


def verify_token(token: str) -> int | None:
    payload = decode_token(token)
    return int(payload["sub"]) if payload else None
