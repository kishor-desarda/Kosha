# SPDX-License-Identifier: AGPL-3.0-or-later
"""A sales order booked in the ERP makes its project once (specification 6.2)."""
from kosha_sdk.erp import ErpEvent

from kosha import db
from kosha.api.eng import _apply_erp_events


def test_sales_order_event_makes_one_project(kdb):
    ev = ErpEvent(kind="sales_order", code="SO-26-0050", detail="Example Dairy|2027-01-20")
    assert _apply_erp_events([ev])["projects_created"] == 1
    assert _apply_erp_events([ev])["projects_created"] == 0
    p = db.one("SELECT * FROM projects WHERE sales_order = 'SO-26-0050'")
    assert p["customer"] == "Example Dairy" and p["due_date"] == "2027-01-20" and p["folder_id"]
    bad = ErpEvent(kind="sales_order", code="SO-26-0051", detail="Example Dairy|not a date")
    assert _apply_erp_events([bad])["projects_created"] == 0
    assert db.scalar("SELECT COUNT(*) FROM audit WHERE action = 'erp_sales_order_refused'") == 1
