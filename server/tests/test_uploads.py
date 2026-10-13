# SPDX-License-Identifier: AGPL-3.0-or-later
"""Upload safety end to end: virus scan through clamd (fail closed), size limit, safe file names, headers."""
import http.client
import json
import urllib.parse

import pytest

from fake_clamd import SIGNATURE, VIRUS_NAME, FakeClamd
from harness import KoshaServer


@pytest.fixture(scope="module")
def clamd():
    c = FakeClamd().start()
    yield c
    c.stop()


@pytest.fixture(scope="module")
def guarded(clamd):
    """A server that scans uploads and accepts at most 0.25 MB per request."""
    s = KoshaServer(env={"KOSHA_CLAMD": clamd.address, "KOSHA_MAX_UPLOAD_MB": "0.25"}).start()
    yield s
    s.stop()


def _folder(client):
    return client.call("GET", "/api/folders")["folders"][0]["id"]


def _docs(client, folder):
    return {d["filename"] for d in client.call("GET", f"/api/folders/{folder}/contents")["documents"]}


def test_clean_uploads_are_scanned_and_kept(guarded, clamd):
    eng = guarded.add_user("erin", "engineer")
    folder = _folder(eng)
    before = len(clamd.scanned)
    r = eng.call("POST", "/api/documents/upload", files={"file": ("MX-300-001 Plate.dxf", b"0\nEOF\n")},
                 fields={"folder_id": folder})
    assert not r["errors"] and r["documents"][0]["filename"] == "MX-300-001 Plate.dxf"
    assert len(clamd.scanned) == before + 1


def test_infected_uploads_are_refused_and_audited(guarded):
    eng = guarded.client("erin", "password-123")
    folder = _folder(eng)
    r = eng.call("POST", "/api/documents/upload", files={"file": ("MX-300-002 Bracket.dxf", b"0\n" + SIGNATURE)},
                 fields={"folder_id": folder}, expect=422)
    assert r["code"] == "infected" and VIRUS_NAME in r["error"] and "MX-300-002 Bracket.dxf" in r["error"]
    assert "Nothing in this upload was saved" in r["error"]
    assert "MX-300-002 Bracket.dxf" not in _docs(eng, folder)
    entries = guarded.client().call("GET", "/api/audit?entity=upload")["entries"]
    assert entries[0]["action"] == "upload_infected" and VIRUS_NAME in entries[0]["details"]
    assert entries[0]["user"] == "Erin"


def test_upload_size_limit(guarded):
    eng = guarded.client("erin", "password-123")
    folder = _folder(eng)
    # a declared size over the limit is refused before the body is read
    u = urllib.parse.urlparse(guarded.base)
    con = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    con.putrequest("POST", "/api/documents/upload")
    con.putheader("Authorization", f"Bearer {eng.token}")
    con.putheader("Content-Type", "multipart/form-data; boundary=xyz")
    con.putheader("Content-Length", str(50 * 1024 * 1024))
    con.endheaders()
    resp = con.getresponse()
    body = json.loads(resp.read())
    con.close()
    assert resp.status == 413 and body["code"] == "too_large" and "0.25 MB" in body["error"]
    # a body without a declared size is cut off as soon as it passes the limit
    con = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    con.putrequest("POST", "/api/documents/upload")
    con.putheader("Authorization", f"Bearer {eng.token}")
    con.putheader("Content-Type", "multipart/form-data; boundary=xyz")
    con.putheader("Transfer-Encoding", "chunked")
    con.endheaders()
    head = (b"--xyz\r\nContent-Disposition: form-data; name=\"folder_id\"\r\n\r\n%d\r\n--xyz\r\n"
            b"Content-Disposition: form-data; name=\"file\"; filename=\"big.dxf\"\r\n\r\n" % folder)
    try:
        for chunk in [head] + [b"x" * 65536] * 22:         # 1.4 MB
            con.send(b"%x\r\n%s\r\n" % (len(chunk), chunk))
        con.send(b"0\r\n\r\n")
    except OSError:
        pass                                               # the server may stop reading early
    resp = con.getresponse()
    assert resp.status == 413 and json.loads(resp.read())["code"] == "too_large"
    con.close()
    assert "big.dxf" not in _docs(eng, folder)
    # a JSON body has its own (larger) limit; normal requests are untouched
    eng.call("POST", "/api/documents/upload", files={"file": ("MX-300-003 Small.dxf", b"0\nEOF\n" * 1000)},
             fields={"folder_id": folder})


def test_viewers_are_refused_before_their_upload_is_read(guarded):
    viewer = guarded.add_user("vic", "viewer")
    r = viewer.call("POST", "/api/documents/upload", files={"file": ("x.dxf", b"0\n" + SIGNATURE)},
                    fields={"folder_id": 1}, expect=403)
    assert r["code"] == "forbidden"
    assert all(e["action"] != "upload_infected" or "x.dxf" not in e["details"]
               for e in guarded.client().call("GET", "/api/audit?entity=upload")["entries"])


def test_file_names_are_made_safe(guarded):
    eng = guarded.client("erin", "password-123")
    folder = _folder(eng)
    r = eng.call("POST", "/api/documents/upload", fields={"folder_id": folder}, files={"file": ("..\\..\\MX-300-004 Pin.dxf. ", b"0\nEOF\n")})
    assert not r["errors"] and r["documents"][0]["filename"] == "MX-300-004 Pin.dxf"
    for bad, why in (("CON.dxf", "devices"), ("LPT1.pdf", "devices"), ("pay‮fdp.exe", "text-direction")):
        r = eng.call("POST", "/api/documents/upload", fields={"folder_id": folder}, files={"file": (bad, b"data")})
        assert not r["documents"] and why in r["errors"][0]["error"], r
    doc = next(d for d in eng.call("GET", f"/api/folders/{folder}/contents")["documents"] if d["filename"] == "MX-300-001 Plate.dxf")
    eng.call("POST", f"/api/documents/{doc['id']}/checkout")
    r = eng.call("PATCH", f"/api/documents/{doc['id']}", {"filename": "AUX.dxf"}, expect=400)
    assert "devices" in r["error"]


def test_stored_files_carry_strict_headers(guarded):
    eng = guarded.client("erin", "password-123")
    folder = _folder(eng)
    doc = next(d for d in eng.call("GET", f"/api/folders/{folder}/contents")["documents"] if d["filename"] == "MX-300-001 Plate.dxf")
    u = urllib.parse.urlparse(guarded.base)
    con = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    con.request("GET", f"/api/documents/{doc['id']}/file", headers={"Authorization": f"Bearer {eng.token}"})
    resp = con.getresponse()
    resp.read()
    con.close()
    assert resp.status == 200
    assert resp.getheader("Content-Security-Policy").startswith("default-src 'none'")
    assert "frame-ancestors 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("X-Frame-Options") == "SAMEORIGIN" and resp.getheader("X-Content-Type-Options") == "nosniff"
    assert resp.getheader("Cache-Control") == "no-store"


def test_unreachable_scanner_refuses_uploads(guarded, clamd):
    """Runs last in this module: it stops the fake scanner."""
    eng = guarded.client("erin", "password-123")
    folder = _folder(eng)
    clamd.stop()
    r = eng.call("POST", "/api/documents/upload", files={"file": ("MX-300-009 Cover.dxf", b"0\nEOF\n")},
                 fields={"folder_id": folder}, expect=503)
    assert r["code"] == "scan_unavailable" and "not reachable" in r["error"] and "nothing was changed" in r["error"]
    assert "MX-300-009 Cover.dxf" not in _docs(eng, folder)
    admin = guarded.client()
    assert admin.call("GET", "/api/audit?entity=upload")["entries"][0]["action"] == "upload_scan_failed"
    eng.call("POST", "/api/folders", {"parent_id": folder, "name": "Still works"})   # requests without files are fine


def test_without_a_scanner_uploads_are_not_scanned(kosha):
    eng = kosha.add_user("ned", "engineer")
    r = eng.call("POST", "/api/documents/upload", files={"file": ("MX-300-010 Tab.dxf", b"0\n" + SIGNATURE)},
                 fields={"folder_id": _folder(eng)})
    assert not r["errors"]


def test_security_headers_on_pages_and_api(kosha):
    u = urllib.parse.urlparse(kosha.base)
    for path, csp_start in (("/", "default-src 'self'"), ("/api/health", "default-src 'none'"),
                            ("/#/files", "default-src 'self'"), ("/static/js/main.js", "default-src 'self'")):
        con = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
        con.request("GET", path)
        resp = con.getresponse()
        resp.read()
        con.close()
        assert resp.status == 200, path
        assert resp.getheader("Content-Security-Policy").startswith(csp_start), path
        assert resp.getheader("X-Frame-Options") == "DENY" and resp.getheader("Referrer-Policy") == "same-origin"
        assert resp.getheader("X-Content-Type-Options") == "nosniff" and resp.getheader("Permissions-Policy")
        assert resp.getheader("Strict-Transport-Security") is None


def test_hsts_only_when_https_is_declared():
    s = KoshaServer(env={"KOSHA_HTTPS": "1"}).start()
    try:
        u = urllib.parse.urlparse(s.base)
        con = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
        con.request("GET", "/")
        resp = con.getresponse()
        resp.read()
        con.close()
        assert resp.getheader("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"
    finally:
        s.stop()


def test_a_folder_that_is_not_a_number_is_refused_in_plain_words(kosha):
    """A browser once sent its click event as the folder ("[object PointerEvent]"): plain words, nothing added."""
    eng = kosha.add_user("frank", "engineer")
    r = eng.call("POST", "/api/documents/upload", files={"file": ("MX-300-004 Cover.dxf", b"0\nEOF\n")},
                 fields={"folder_id": "[object PointerEvent]"}, expect=400)
    assert "could not tell which folder" in r["error"] and "nothing was added" in r["error"]
    assert "invalid literal" not in r["error"]
    assert not eng.call("GET", "/api/search?q=MX-300-004")["documents"]
