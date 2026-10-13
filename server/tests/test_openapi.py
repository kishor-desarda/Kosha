# SPDX-License-Identifier: AGPL-3.0-or-later
"""The API description is public, read-only, generated from the live routes and self-contained."""
import http.client
import urllib.parse

from harness import Client
from kosha import apidoc


def test_openapi_json_describes_the_running_server(kosha):
    anon = Client(kosha.base)
    spec = anon.call("GET", "/api/openapi.json")
    assert spec["openapi"].startswith("3.1") and spec["info"]["title"] == "Kosha API"
    paths = spec["paths"]
    for path, method in (("/api/auth/login", "post"), ("/api/auth/logout", "post"), ("/api/users/{id}", "patch"),
                         ("/api/documents/upload", "post"), ("/api/admin/security", "put"),
                         ("/api/openapi.json", "get"), ("/api/health", "get")):
        assert method in paths.get(path, {}), (path, method)
    assert paths["/api/admin/security"]["put"]["x-kosha-role"] == "admin"
    assert paths["/api/documents/{id}/checkin"]["post"]["x-kosha-role"] == "engineer"
    assert paths["/api/folders"]["get"]["x-kosha-role"] == "viewer"
    assert paths["/api/health"]["get"]["security"] == []
    assert "throttled" in paths["/api/auth/login"]["post"]["description"]
    assert {t["name"] for t in spec["tags"]} >= {"auth", "documents", "boms", "admin"}
    # every operation in the description answers (no 404s from made-up paths)
    admin = kosha.client()
    for path in ("/api/folders", "/api/settings", "/api/admin/security", "/api/meta/types"):
        assert "get" in paths[path]
        admin.call("GET", path)


def test_docs_page_is_readable_and_self_contained(kosha):
    page, ctype = Client(kosha.base).call("GET", "/api/docs", raw=True)
    html = page.decode()
    assert ctype.startswith("text/html")
    assert "<script" not in html and "src=" not in html and "https://" not in html and "http://" not in html
    for text in ("Kosha API", "Sign-in and sessions", "Files and versions", "/api/documents/{id}",
                 "Who may call it: admin", "/api/openapi.json"):
        assert text in html, text
    u = urllib.parse.urlparse(kosha.base)
    con = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    con.request("GET", "/api/docs")
    resp = con.getresponse()
    resp.read()
    con.close()
    assert resp.getheader("Content-Security-Policy").startswith("default-src 'none'; style-src 'unsafe-inline'")


def test_the_description_cannot_be_changed(kosha):
    Client(kosha.base).call("POST", "/api/openapi.json", body={}, expect=405)
    Client(kosha.base).call("PUT", "/api/docs", body={}, expect=405)


def test_api_description_covers_every_registered_route():
    from kosha import web
    from kosha.app import app
    spec = apidoc.openapi(app.routes)
    registered = {(r.endpoint.kosha["method"].lower(), r.path) for r in web.routes}
    described = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert {(m, apidoc._PARAM.sub(r"{\1}", p)) for m, p in registered} <= described
    assert ("get", "/api/health") in described
    login = spec["paths"]["/api/auth/login"]["post"]
    assert login["security"] == [] and login["x-kosha-role"] == "public" and "Sign in" in login["summary"]
    users = spec["paths"]["/api/users"]["post"]
    assert users["x-kosha-role"] == "admin" and "403" in users["responses"]
    doc = spec["paths"]["/api/documents/{id}"]["get"]
    assert doc["parameters"] == [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}]
    upload = spec["paths"]["/api/documents/upload"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    assert len({op["operationId"] for ops in spec["paths"].values() for op in ops.values()}) == len(described)
    page = apidoc.html(spec)
    assert "<script" not in page and "http://" not in page and "https://" not in page
    assert "Sign-in and sessions" in page and "/api/documents/{id}" in page
