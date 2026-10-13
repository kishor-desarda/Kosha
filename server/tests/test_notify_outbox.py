# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notification outbox and the built-in e-mail notifier: who gets what, idempotency, retries with growing waits,
the delivery log, and that a failing channel never reaches the code that queued the notice."""
import json
import time

import pytest

from fake_smtp import FakeSmtp, self_signed_context
from kosha import addins, config, db, mailer
from kosha import notify_outbox as outbox
from kosha_sdk.notify import Notice, NotifyError, Recipient


@pytest.fixture
def kdb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kosha.db")
    db.close_thread_connection()
    db.init()
    monkeypatch.setattr(outbox, "_custom", {})
    yield
    db.close_thread_connection()


@pytest.fixture
def smtp():
    s = FakeSmtp(user="kosha", password="mail-pass-1").start()
    yield s
    s.stop()


def _user(name, role="engineer", email=None):
    return db.insert("users", {"username": name, "full_name": name.title() + " Example", "role": role,
                               "email": f"{name}@example.com" if email is None else email,
                               "password_hash": "x", "created_at": db.now()})


def _mail(smtp, **extra):
    mailer.save({"enabled": True, "host": "127.0.0.1", "port": smtp.port, "security": "none", "username": "kosha",
                 "password": "mail-pass-1", "from_address": "kosha@example.com", "timeout_s": 5, **extra})


class Chat:
    """A chat channel that records posts, or fails while `fail` is set."""

    def __init__(self):
        self.posts: list[Notice] = []
        self.fail = None

    def __call__(self, notice):
        if self.fail:
            raise self.fail
        self.posts.append(notice)


def _due_now():
    db.execute("UPDATE notify_outbox SET next_attempt_at = ? WHERE status = 'pending'", (db.now(),))


# --------------------------------------------------------------------------- queueing

def test_email_is_queued_once_per_person_and_only_when_ready(kdb, smtp):
    a, b, c = _user("asha"), _user("daniel", "approver"), _user("nomail", email="")
    assert outbox.enqueue([a, b], "Hello", kind="approvals") == 0          # no mail server yet: nothing queued
    _mail(smtp)
    assert outbox.enqueue([a, b, c, b], "ECO-0001 needs your approval", "Body", "#/change/1", "approvals",
                          key="k1") == 2
    rows = db.rows("SELECT channel, user_id, address FROM notify_outbox ORDER BY id")
    assert rows == [{"channel": "email", "user_id": a, "address": "asha@example.com"},
                    {"channel": "email", "user_id": b, "address": "daniel@example.com"}]


def test_same_key_is_stored_once(kdb, smtp):
    _mail(smtp)
    a = _user("asha")
    assert outbox.enqueue([a], "T", kind="approvals", key="same") == 1
    assert outbox.enqueue([a], "T", kind="approvals", key="same") == 0
    assert outbox.enqueue([a], "T", kind="approvals") == 1                 # no key: every call is a new notice
    assert db.scalar("SELECT COUNT(*) FROM notify_outbox") == 2


def test_chat_channels_get_one_post_naming_everyone(kdb):
    chat = Chat()
    outbox.register_channel("chat", "Team chat", chat, per_person=False)
    a, b = _user("asha"), _user("daniel", "approver")
    outbox.save_prefs(a, {"approvals": ["chat"]})
    outbox.save_prefs(b, {"approvals": ["chat", "email", "no-such-channel"]})
    assert outbox.prefs(b)["approvals"] == ["chat", "email"]               # unknown channels are dropped
    assert outbox.enqueue([a, b], "ECO-0002 needs your approval", kind="approvals", key="k2") == 1
    row = db.one("SELECT * FROM notify_outbox")
    assert [p["name"] for p in json.loads(row["recipients_json"])] == ["Asha Example", "Daniel Example"]
    assert outbox.process_due() == 1 and chat.posts[0].for_line() == "For: Asha Example, Daniel Example"


def test_preferences_and_opt_in(kdb):
    chat = Chat()
    outbox.register_channel("chat", "Team chat", chat, per_person=False)
    a, b, gone = _user("asha"), _user("tom", "viewer"), _user("gone")
    db.execute("UPDATE users SET active = 0 WHERE id = ?", (gone,))
    assert outbox.prefs(a)["releases"] == [] and outbox.prefs(a)["approvals"] == ["email"]
    outbox.save_prefs(b, {"releases": ["chat"]})
    outbox.save_prefs(gone, {"releases": ["chat"]})
    assert outbox.users_wanting("releases") == [b]
    outbox.save_prefs(a, {"approvals": []})
    assert outbox.enqueue([a], "Nothing for me", kind="approvals") == 0
    with pytest.raises(ValueError, match="Unknown kind"):
        outbox.save_prefs(a, {"weather": ["chat"]})


def test_direct_channel_and_relative_links(kdb):
    chat = Chat()
    outbox.register_channel("chat", "Team chat", chat, per_person=False)
    assert outbox.enqueue("chat", "Nightly check", "All good", "#/erp", "general") == 1
    assert outbox.enqueue("not-set-up", "Nightly check") == 0
    outbox.process_due()
    assert chat.posts[0].link == "#/erp"                                   # Kosha's own channels keep relative links
    assert outbox.absolute_link("#/erp") == ""                             # others get nothing without Kosha's address
    outbox.save_settings({"base_url": "https://plm.example.com/"})
    assert outbox.absolute_link("#/erp") == "https://plm.example.com/#/erp"
    with pytest.raises(ValueError):
        outbox.save_settings({"base_url": "plm.example.com"})


def test_enqueue_never_raises(kdb, monkeypatch, caplog):
    a = _user("asha")
    assert outbox.enqueue([a], "") == 0                                    # no title
    monkeypatch.setattr(outbox, "channels", lambda: 1 / 0)
    assert outbox.enqueue([a], "Boom", kind="approvals") == 0
    assert "the action that caused it is not affected" in caplog.text


# --------------------------------------------------------------------------- delivery

def test_email_has_plain_text_html_and_a_stable_message_id(kdb, smtp):
    _mail(smtp)
    outbox.save_settings({"base_url": "https://plm.example.com"})
    b = _user("daniel", "approver")
    outbox.enqueue([b], "ECO-0003 needs your approval: <Wider> base", "Asha asks you.\nPriority: high.", "#/change/3",
                   "approvals", key="eco3")
    assert outbox.process_due() == 1
    m = smtp.wait_for(1)[0]
    msg = m["message"]
    assert m["to"] == ["daniel@example.com"] and m["from"] == "kosha@example.com"
    assert msg["Subject"] == "[Kosha] ECO-0003 needs your approval: <Wider> base"
    assert msg["Auto-Submitted"] == "auto-generated" and msg["X-Kosha-Kind"] == "approvals"
    plain = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "Open in Kosha: https://plm.example.com/#/change/3" in plain and "Priority: high." in plain
    assert "&lt;Wider&gt;" in html and '<a href="https://plm.example.com/#/change/3">' in html
    again = mailer.build_message(Notice("x", key="eco3"), Recipient("D", "daniel@example.com"), mailer.settings())
    assert again["Message-ID"] == msg["Message-ID"]                       # mail programs can drop a repeat
    row = db.one("SELECT * FROM notify_outbox")
    assert row["status"] == "sent" and row["attempts"] == 1 and row["sent_at"]
    assert db.one("SELECT ok, message FROM notify_attempts") == {"ok": 1, "message": "Delivered"}


def test_failures_retry_with_growing_waits_then_stop(kdb, monkeypatch):
    chat = Chat()
    chat.fail = NotifyError("The channel is down.")
    outbox.register_channel("chat", "Team chat", chat, per_person=False)
    outbox.enqueue("chat", "Will fail")
    waits = []
    for attempt in range(1, outbox.MAX_ATTEMPTS + 1):
        _due_now()
        assert outbox.process_due() == 1
        row = db.one("SELECT * FROM notify_outbox")
        assert row["attempts"] == attempt and row["last_error"] == "The channel is down."
        if attempt < outbox.MAX_ATTEMPTS:
            assert row["status"] == "pending" and row["next_attempt_at"] > db.now()
            waits.append(outbox.backoff_seconds(attempt))
    assert row["status"] == "failed"
    assert waits == sorted(waits) and waits[0] > 20                       # each wait is longer than the one before
    assert outbox.backoff_seconds(1) < 40 and outbox.backoff_seconds(10) <= 6 * 3600 * 1.15
    assert db.scalar("SELECT COUNT(*) FROM notify_attempts WHERE ok = 0") == outbox.MAX_ATTEMPTS
    _due_now()
    assert outbox.process_due() == 0                                       # failed notices are not retried by themselves
    chat.fail = None
    assert outbox.retry(row["id"])["status"] == "sent"                     # until the admin presses "Send again"
    log = outbox.log_rows()
    assert log["counts"]["sent"] == 1 and log["rows"][0]["attempt_log"][0]["ok"] == 1


def test_crashing_channel_is_contained(kdb):
    def crash(notice):
        raise RuntimeError("bug in a channel")
    outbox.register_channel("crashy", "Crashy", crash)
    a = _user("asha")
    outbox.save_prefs(a, {"approvals": ["crashy"]})
    outbox.enqueue([a], "T", kind="approvals")
    assert outbox.process_due() == 1
    row = db.one("SELECT * FROM notify_outbox")
    assert row["status"] == "pending" and "bug in a channel" in row["last_error"]


def test_stuck_deliveries_are_picked_up_again(kdb):
    chat = Chat()
    outbox.register_channel("chat", "Team chat", chat, per_person=False)
    outbox.enqueue("chat", "Interrupted")
    db.execute("UPDATE notify_outbox SET status = 'sending', updated_at = '2000-01-01T00:00:00+00:00'")
    assert outbox.process_due() == 1 and chat.posts


def test_addin_channel_goes_through_the_guard(kdb, monkeypatch):
    calls = []

    def fake_call(addin_id, method, *args, timeout=None):
        calls.append((addin_id, method, timeout))
        raise addins.AddinUnavailable("The Slack add-in is switched off.")
    monkeypatch.setattr(outbox.registry, "call", fake_call)
    db.insert("notify_outbox", {"dedupe_key": "x", "channel": "org.kosha.slack", "title": "T", "status": "pending",
                                "next_attempt_at": db.now(), "created_at": db.now(), "updated_at": db.now()})
    outbox.process_due()
    assert calls == [("org.kosha.slack", "send", outbox.SEND_TIMEOUT_SECONDS)]
    assert "switched off" in db.scalar("SELECT last_error FROM notify_outbox")


# --------------------------------------------------------------------------- the mailer

def test_mail_settings_are_checked_and_the_password_masked(kdb):
    shown = mailer.save({"host": " mail.example.com ", "port": "465", "security": "ssl", "password": "s3cret",
                         "from_address": "kosha@example.com", "enabled": "yes"})
    assert shown["password"] == addins.SECRET_MASK and shown["port"] == 465 and shown["host"] == "mail.example.com"
    mailer.save({"password": addins.SECRET_MASK})
    assert mailer.settings()["password"] == "s3cret" and mailer.settings()["enabled"] is True
    for bad, words in (({"port": "x"}, "whole number"), ({"port": 0}, "between"), ({"security": "maybe"}, "Security"),
                       ({"from_address": "not-an-address"}, "does not look like")):
        with pytest.raises(ValueError, match=words):
            mailer.save(bad)
    assert mailer.configured() == (True, "")
    mailer.save({"enabled": False})
    assert "switched off" in mailer.configured()[1]


def _send(**cfg):
    full = {"host": "127.0.0.1", "from_address": "kosha@example.com", "timeout_s": 3, **cfg}
    mailer.EmailNotifier(cfg=full).send(Notice("Test", recipients=(Recipient("Daniel", "daniel@example.com"),)))


def test_mail_errors_are_plain_words(kdb, smtp):
    with pytest.raises(NotifyError, match="does not offer STARTTLS"):
        _send(port=smtp.port, security="starttls")
    with pytest.raises(NotifyError, match="refused the user name or password"):
        _send(port=smtp.port, security="none", username="kosha", password="wrong")
    smtp.mode = "refuse_rcpt"
    with pytest.raises(NotifyError, match="refused the address daniel@example.com"):
        _send(port=smtp.port, security="none", username="kosha", password="mail-pass-1")
    from harness import free_port
    with pytest.raises(NotifyError, match="Cannot reach the mail server"):
        _send(port=free_port(), security="none")
    with pytest.raises(NotifyError, match="no valid e-mail address"):
        mailer.EmailNotifier(cfg={"host": "h", "from_address": "k@example.com"}).send(Notice("T", recipients=(Recipient("X"),)))
    smtp.mode, smtp.hang_seconds = "hang", 3
    t0 = time.time()
    with pytest.raises(NotifyError, match="did not answer within 1 seconds"):
        _send(port=smtp.port, security="none", timeout_s=1)
    assert time.time() - t0 < 2.5


def test_health_reports_the_connection(kdb, smtp):
    ok = mailer.EmailNotifier(cfg={"host": "127.0.0.1", "port": smtp.port, "security": "none", "username": "kosha",
                                   "password": "mail-pass-1", "from_address": "kosha@example.com"}).health()
    assert ok.ok and f"127.0.0.1:{smtp.port}" in ok.message and "as kosha" in ok.message
    assert not mailer.EmailNotifier(cfg={}).health().ok


@pytest.mark.parametrize("implicit", [False, True])
def test_tls(kdb, implicit):
    ctx = self_signed_context()
    if ctx is None:
        pytest.skip("the openssl tool is needed to make a test certificate")
    s = FakeSmtp(tls=ctx, implicit_tls=implicit).start()
    try:
        security = "ssl" if implicit else "starttls"
        with pytest.raises(NotifyError, match="secure connection"):           # a self-signed certificate is refused
            _send(port=s.port, security=security)
        _send(port=s.port, security=security, verify_tls=False)               # unless the admin switched checking off
        assert s.wait_for(1)[0]["tls"] is True
    finally:
        s.stop()


# --------------------------------------------------------------------------- events reach listeners

def test_listeners_run_in_the_background_and_cannot_break_the_caller(kdb):
    r = addins.Registry()
    seen = []
    r.listen("change.submitted", lambda e: seen.append(e))
    r.listen("change.submitted", lambda e: 1 / 0)
    r.emit("change.submitted", {"change_id": 7})                          # returns at once; the failure is only logged
    for _ in range(50):
        if seen:
            break
        time.sleep(0.02)
    assert seen == [{"event": "change.submitted", "change_id": 7}]
