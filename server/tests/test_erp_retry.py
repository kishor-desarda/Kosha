# SPDX-License-Identifier: AGPL-3.0-or-later
"""Automatic ERP retries wait longer each time, and the nightly check runs only against a live connection."""
from kosha import db, erp, erp_reconcile


def test_retry_waits_grow_then_stop():
    assert [erp.retry_delay(n) for n in range(1, 6)] == [60, 240, 960, 3840, None]


def test_nightly_check_needs_a_live_connection(kdb):
    assert erp_reconcile.nightly() is None                      # switched off
    erp.save_settings({"enabled": True, "dry_run": True})
    assert erp_reconcile.nightly() is None                      # dry run: nothing is compared at night
    erp.save_settings({"dry_run": False, "nightly_reconcile": False})
    assert erp_reconcile.nightly() is None                      # the admin switched it off
    assert db.scalar("SELECT COUNT(*) FROM erp_recon_runs") == 0
