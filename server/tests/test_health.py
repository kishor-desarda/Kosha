# SPDX-License-Identifier: AGPL-3.0-or-later
"""Health alerts and their timing (specification 16.5)."""
from collections import namedtuple

from kosha import db, health


def _admin():
    db.insert("users", {"username": "asha", "full_name": "Asha Example", "role": "admin", "password_hash": "x",
                        "created_at": db.now()})


def test_alerts_once_a_day(kdb, monkeypatch):
    _admin()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: usage(100, 85, 15))
    keys = {p["key"] for p in health.problems()}
    assert {"disk", "backup", "restore_test"} <= keys        # 85% full; no backup and no restore test yet
    health.send_alerts()
    health.send_alerts()                                      # the same alert at most once a day
    titles = [r["title"] for r in db.rows("SELECT title FROM notifications")]
    assert titles.count("The data disk is 85% full") == 1 and titles.count("Backups need attention") == 1


def test_queue_stuck_and_certificate(kdb, monkeypatch, tmp_path):
    db.execute("INSERT INTO erp_jobs(kind, entity_id, status, created_at, updated_at) "
               "VALUES ('item', 1, 'pending', '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')")
    monkeypatch.setattr(health, "_cert_days_left", lambda: 5)
    found = {p["key"]: p for p in health.problems()}
    assert found["erp_queue"]["title"].startswith("1 ERP transfer")
    assert found["certificate"]["severity"] == "error" and "5 days" in found["certificate"]["title"]


def test_run_due_spaces_the_checks(kdb, monkeypatch):
    calls = []
    monkeypatch.setattr(health, "check_fingerprints", lambda: calls.append("f") or {})
    monkeypatch.setattr(health, "check_consistency", lambda: calls.append("c") or {})
    monkeypatch.setattr(health, "send_alerts", lambda: calls.append("a") or 0)
    health.run_due()
    health.run_due()
    assert sorted(calls) == ["a", "c", "f"]
    runs = db.get_setting(health.RUNS)
    runs["consistency"] = "2000-01-01T00:00:00+00:00"
    db.set_setting(health.RUNS, runs)
    health.run_due()
    assert calls.count("c") == 2 and calls.count("f") == 1
