# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each user's theme, kept on the server (POST /api/auth/theme), and the installation's default (Admin › Users)."""
from harness import Client


def test_a_new_user_follows_the_installation_default_which_is_same_as_my_computer(kosha):
    tom = kosha.add_user("tom", "engineer")
    me = tom.call("GET", "/api/auth/me")
    assert me["user"]["ui_theme"] == "" and me["default_theme"] == "system"
    assert tom.call("GET", "/api/settings")["default_theme"] == "system"


def test_users_choose_their_theme_and_get_it_back_at_every_sign_in(kosha):
    sofia = kosha.add_user("sofia", "viewer", password="password-123")      # viewers may choose too
    for theme in ("dark", "contrast", "light", "system"):
        assert sofia.call("POST", "/api/auth/theme", {"theme": theme}) == {"theme": theme, "default_theme": "system"}
        assert sofia.call("GET", "/api/auth/me")["user"]["ui_theme"] == theme
    sofia.call("POST", "/api/auth/theme", {"theme": "dark"})
    again = Client(kosha.base).call("POST", "/api/auth/login", {"username": "sofia", "password": "password-123"})
    assert again["user"]["ui_theme"] == "dark" and again["default_theme"] == "system"
    # back to the installation's default
    assert sofia.call("POST", "/api/auth/theme", {"theme": ""})["theme"] == ""
    assert sofia.call("GET", "/api/auth/me")["user"]["ui_theme"] == ""
    # only the own account changes
    admin = kosha.client()
    assert admin.call("GET", "/api/auth/me")["user"]["ui_theme"] == ""


def test_unknown_themes_are_refused_in_plain_words(kosha):
    asha = kosha.add_user("asha", "engineer")
    for bad in ("purple", None, 3, "DARK"):
        r = asha.call("POST", "/api/auth/theme", {"theme": bad}, expect=400)
        assert r["code"] == "bad_theme" and "light, dark or contrast" in r["error"], r
    assert asha.call("GET", "/api/auth/me")["user"]["ui_theme"] == ""
    Client(kosha.base).call("POST", "/api/auth/theme", {"theme": "dark"}, expect=401)


def test_admins_set_the_installation_default_and_a_users_theme(kosha):
    admin = kosha.client()
    daniel = kosha.add_user("daniel", "approver")
    daniel.call("PUT", "/api/settings", {"default_theme": "dark"}, expect=403)      # only admins
    assert admin.call("PUT", "/api/settings", {"default_theme": "dark"})["default_theme"] == "dark"
    assert daniel.call("GET", "/api/auth/me")["default_theme"] == "dark"
    assert daniel.call("POST", "/api/auth/theme", {"theme": "light"})["default_theme"] == "dark"
    r = admin.call("PUT", "/api/settings", {"default_theme": ""}, expect=400)       # the default must be a theme
    assert r["code"] == "bad_theme"
    admin.call("PUT", "/api/settings", {"default_theme": "system"})

    users = {u["username"]: u for u in admin.call("GET", "/api/users")["users"]}
    assert users["daniel"]["ui_theme"] == "light"
    uid = users["daniel"]["id"]
    assert admin.call("PATCH", f"/api/users/{uid}", {"ui_theme": "contrast"})["user"]["ui_theme"] == "contrast"
    admin.call("PATCH", f"/api/users/{uid}", {"ui_theme": "neon"}, expect=400)
    new = admin.call("POST", "/api/users", {"username": "mira", "password": "mixer-shaft-blue-7",
                                            "ui_theme": "dark"})["user"]
    assert new["ui_theme"] == "dark"
    admin.call("POST", "/api/users", {"username": "kai", "password": "mixer-shaft-blue-7", "ui_theme": "x"},
               expect=400)
