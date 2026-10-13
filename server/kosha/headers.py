# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security headers on every response (plain ASGI middleware, so streamed responses are untouched).

The web client loads only its own scripts, styles and data from Kosha; it sets inline style attributes and uses
data: URIs for some images, and shows PDFs in a frame from Kosha itself. A response that already
carries one of these headers keeps its own value (file downloads and previews set a stricter policy).

The Office add-in pages (/static/office-addin/) are the one exception: Office loads office.js from Microsoft's
server and, in Office on the web, shows the task pane in a frame of an Office page. They get their own policy that
allows exactly that and nothing more.
"""
from __future__ import annotations

from . import config

APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
           "font-src 'self'; connect-src 'self'; frame-src 'self'; worker-src 'self'; object-src 'none'; "
           "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
OFFICE_PREFIX = "/static/office-addin/"
OFFICE_JS = "https://appsforoffice.microsoft.com"
OFFICE_HOSTS = ("https://*.office.com https://*.office365.com https://*.officeapps.live.com https://*.sharepoint.com "
                "https://*.microsoft365.com https://*.cloud.microsoft https://outlook.live.com")
OFFICE_CSP = (f"default-src 'self'; script-src 'self' {OFFICE_JS}; style-src 'self' 'unsafe-inline'; "
              f"img-src 'self' data:; font-src 'self'; connect-src 'self' {OFFICE_JS}; frame-src 'self' {OFFICE_JS}; "
              f"object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self' {OFFICE_HOSTS}")
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), display-capture=()"
HSTS = "max-age=31536000; includeSubDomains"


def security_headers(path: str, https: bool | None = None) -> list[tuple[bytes, bytes]]:
    """The headers Kosha adds to a response for ``path`` (unless the response already has them)."""
    api = path.startswith("/api/")
    office = path.startswith(OFFICE_PREFIX)
    out = [
        ("content-security-policy", API_CSP if api else OFFICE_CSP if office else APP_CSP),
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "same-origin"),
        ("permissions-policy", PERMISSIONS),
        ("cross-origin-opener-policy", "same-origin-allow-popups" if office else "same-origin"),
    ]
    if not office:      # X-Frame-Options cannot name the Office hosts; the policy's frame-ancestors does it there
        out.append(("x-frame-options", "DENY"))
    if api:
        out.append(("cache-control", "no-store"))     # answers carry business data: never keep them in caches
    if config.HTTPS if https is None else https:
        out.append(("strict-transport-security", HSTS))
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in out]


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers(scope.get("path", ""))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in extra if k not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
