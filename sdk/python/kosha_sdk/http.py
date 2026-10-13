# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""HTTP client with the safety rules of the development guide built in.

* every call has a time-out (10 s by default);
* only temporary failures are retried (network errors, HTTP 429 and 5xx), with growing,
  randomised waits and a limited number of tries;
* a validation error (other 4xx) is never retried;
* changing calls can carry an idempotency key, so a retry never creates a second record;
* forms (``form=``, as OAuth and OpenID Connect token endpoints expect) and per-call headers.

Uses only the Python standard library, so add-ins stay light.
"""
from __future__ import annotations

import json
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .errors import AddinError

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(AddinError):
    """The other system answered with an error, or could not be reached."""


@dataclass
class Response:
    status: int
    text: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.text) if self.text else {}


class HttpClient:
    def __init__(self, base_url: str, *, headers: dict[str, str] | None = None, timeout_s: float = 10.0,
                 verify_tls: bool = True, retries: int = 3, sleep=time.sleep):
        if not base_url:
            raise AddinError("The address of the other system is not set.")
        self.base = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout_s = timeout_s
        self.retries = max(0, retries)
        self._sleep = sleep
        self._ctx = None
        if self.base.startswith("https") and not verify_tls:
            self._ctx = ssl._create_unverified_context()  # only when the admin switched verification off

    def request(self, method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None,
                idempotency_key: str | None = None, allow_404: bool = False, form: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None) -> Response | None:
        url = self.base + path
        if params:
            url += ("&" if "?" in path else "?") + urllib.parse.urlencode(
                {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in params.items()})
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
            content_type = "application/json"
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Accept", "application/json")
            for k, v in {**self.headers, **(headers or {})}.items():
                req.add_header(k, v)
            if data is not None:
                req.add_header("Content-Type", content_type)
            if idempotency_key:
                req.add_header("Idempotency-Key", idempotency_key)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s, context=self._ctx) as r:
                    return Response(r.status, r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 404 and allow_404:
                    return None
                if e.code in RETRY_STATUS and attempt <= self.retries:
                    self._wait(attempt, e.headers.get("Retry-After"))
                    continue
                raise HttpError(f"HTTP {e.code}", status=e.code, detail=body) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt <= self.retries:
                    self._wait(attempt, None)
                    continue
                reason = getattr(e, "reason", e)
                raise HttpError(f"Cannot reach {self.base}: {reason}") from None

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        if retry_after and retry_after.isdigit():
            delay = min(float(retry_after), 30.0)
        else:
            delay = min(0.5 * (2 ** (attempt - 1)), 8.0) * random.uniform(0.5, 1.5)
        self._sleep(delay)

    def get(self, path: str, **kw) -> Response | None:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any = None, **kw) -> Response | None:
        return self.request("POST", path, json_body=json_body, **kw)

    def put(self, path: str, json_body: Any = None, **kw) -> Response | None:
        return self.request("PUT", path, json_body=json_body, **kw)
