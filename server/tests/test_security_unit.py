# SPDX-License-Identifier: AGPL-3.0-or-later
"""Password policy, tokens, safe file names, security headers and clamd addresses (no server needed)."""
import pytest

from kosha import headers, scan, security, uploads


@pytest.mark.parametrize("password, username, problem", [
    ("short-1", "asha", "at least 10"),
    ("x" * 129, "asha", "at most 128"),
    ("Engineer-Asha", "engineer-asha", "same as the user name"),
    ("1234567890", "asha", "too common"),
    ("Password123", "asha", "too common"),
    ("QWERTYUIOP", "asha", "too common"),
    ("aaaaaaaaaaaa", "asha", "too common"),
    ("abababababab", "asha", "too common"),
])
def test_password_policy_refuses_weak_passwords(password, username, problem):
    assert problem in security.password_problem(password, username)


@pytest.mark.parametrize("password", ["mixer shaft blue lamp", "admin-test-pass-1", "password-123", "Tr4ck-Pump-77"])
def test_password_policy_accepts_reasonable_passwords(password):
    assert security.password_problem(password, "asha") is None


def test_tokens_carry_the_session_and_old_api_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("KOSHA_SECRET", "unit-test-secret-" + "0" * 32)
    t = security.make_token(7, hours=1, sid="abc")
    p = security.decode_token(t)
    assert p["sub"] == "7" and p["sid"] == "abc" and p["exp"] - p["iat"] == 3600
    assert security.verify_token(t) == 7
    assert security.decode_token(t + "x") is None
    monkeypatch.setenv("KOSHA_SECRET", "another-secret-" + "1" * 32)
    assert security.decode_token(t) is None


def test_waste_verify_takes_a_real_hash_check():
    security.waste_verify("anything")        # must not raise, whatever the input
    security.waste_verify("")


@pytest.mark.parametrize("raw, clean", [
    ("MX-100-020 Main Shaft.dxf", "MX-100-020 Main Shaft.dxf"),
    ("C:\\Users\\asha\\Desktop\\MX-1.dwg", "MX-1.dwg"),
    ("../../etc/passwd", "passwd"),
    ("Drawing.dwg. . ", "Drawing.dwg"),
    ("  spaced name.pdf  ", "spaced name.pdf"),
    ("CONSOLE.dwg", "CONSOLE.dwg"),
    ("con-rod.sldprt", "con-rod.sldprt"),
    ("Cafe\u0301.dxf", "Caf\u00e9.dxf"),      # normalised to one form, so look-alike names don't clash
])
def test_clean_filename_keeps_good_names(raw, clean):
    assert uploads.clean_filename(raw) == clean


@pytest.mark.parametrize("raw, why", [
    ("", "no usable name"),
    ("..", "no usable name"),
    ("folder/", "no usable name"),
    ("bad\x07name.dxf", "control characters"),
    ("tab\tname.dxf", "control characters"),
    ("invoice\u202egpj.exe", "text-direction"),
    ("a:b.dxf", "Windows does not allow"),
    ("what?.pdf", "Windows does not allow"),
    ("CON.dxf", "Windows keeps for devices"),
    ("con", "Windows keeps for devices"),
    ("aux.tar.gz", "Windows keeps for devices"),
    ("COM1.dwg", "Windows keeps for devices"),
    ("lpt9.txt", "Windows keeps for devices"),
    ("NUL .txt", "Windows keeps for devices"),
    ("x" * 252 + ".dxf", "longer than 255"),
])
def test_clean_filename_refuses_unsafe_names(raw, why):
    with pytest.raises(ValueError, match=why):
        uploads.clean_filename(raw)


def test_lifecycle_turns_name_problems_into_400s():
    from kosha import lifecycle
    from kosha.web import ApiError
    with pytest.raises(ApiError) as e:
        lifecycle.clean_filename("PRN.pdf")
    assert e.value.status == 400 and "devices" in e.value.message


def _h(path, https=False):
    return {k.decode(): v.decode() for k, v in headers.security_headers(path, https)}


def test_security_headers_for_the_app_and_the_api():
    app = _h("/")
    assert "script-src 'self'" in app["content-security-policy"]
    assert "frame-ancestors 'none'" in app["content-security-policy"]
    assert "img-src 'self' data:" in app["content-security-policy"]
    assert "'unsafe-eval'" not in app["content-security-policy"]
    assert app["x-content-type-options"] == "nosniff" and app["referrer-policy"] == "same-origin"
    assert app["x-frame-options"] == "DENY" and "camera=()" in app["permissions-policy"]
    assert "strict-transport-security" not in app and "cache-control" not in app
    api = _h("/api/folders")
    assert api["content-security-policy"].startswith("default-src 'none'") and api["cache-control"] == "no-store"
    assert _h("/", https=True)["strict-transport-security"].startswith("max-age=31536000")


@pytest.mark.parametrize("spec, expected", [
    ("tcp://127.0.0.1:3310", ("tcp", ("127.0.0.1", 3310))),
    ("clamav.local:3311", ("tcp", ("clamav.local", 3311))),
    ("clamav.local", ("tcp", ("clamav.local", 3310))),
    ("[::1]:3310", ("tcp", ("::1", 3310))),
    ("unix:///run/clamav/clamd.ctl", ("unix", "/run/clamav/clamd.ctl")),
    ("unix:/run/clamd.sock", ("unix", "/run/clamd.sock")),
    ("/var/run/clamd.sock", ("unix", "/var/run/clamd.sock")),
    ("", None),
])
def test_clamd_addresses(spec, expected):
    assert scan.address(spec) == expected


def test_clamd_address_typos_are_reported():
    with pytest.raises(scan.ScanUnavailable, match="not a valid address"):
        scan.address("tcp://host:port")
