# SPDX-License-Identifier: AGPL-3.0-or-later
"""The end-to-end harness itself works: empty and demo installations, sign-in, roles."""


def test_empty_installation(kosha):
    admin = kosha.client()
    assert admin.call("GET", "/api/auth/me")["user"]["username"] == "admin"
    viewer = kosha.add_user("vera", "viewer")
    viewer.call("POST", "/api/numbering/next", {"prefix": "MX-1-"}, expect=403)


def test_demo_installation(demo_kosha):
    asha = demo_kosha.client("asha")
    found = asha.call("GET", "/api/search?q=MX-100-020")
    assert found["items"] and found["items"][0]["part_number"] == "MX-100-020"
