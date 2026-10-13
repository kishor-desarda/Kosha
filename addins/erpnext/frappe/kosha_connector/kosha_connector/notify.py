# SPDX-License-Identifier: Apache-2.0
"""Tell Kosha what changed in ERPNext. Sent from the background queue after the transaction commits,
so a slow or unreachable Kosha never delays or breaks work in ERPNext."""
import json

import frappe
import requests

from .signing import sign

TIMEOUT_SECONDS = 10


def _settings():
    s = frappe.get_cached_doc("Kosha Settings")
    return s if s.enabled and s.kosha_url else None


def _queue(kind, code, detail=""):
    if not code or not _settings():
        return
    frappe.enqueue("kosha_connector.notify.send", queue="short", enqueue_after_commit=True,
                   events=[{"kind": kind, "code": code, "detail": detail}])


def item_changed(doc, method=None):
    _queue("item_changed", doc.item_code)


def bom_cancelled(doc, method=None):
    _queue("bom_cancelled", doc.item, f"{doc.name} cancelled")


def stock_changed(doc, method=None):
    _queue("stock_changed", doc.item_code)


def sales_order_submitted(doc, method=None):
    """A booked sales order: Kosha makes its project with the standard folders (customer | delivery date)."""
    _queue("sales_order", doc.name, f"{doc.customer_name or doc.customer or ''}|{doc.delivery_date or ''}")


def _ordered_codes(doc):
    return ",".join(dict.fromkeys(r.item_code for r in (doc.get("items") or []) if r.item_code))


def purchase_order_submitted(doc, method=None):
    """A purchase order: Kosha records the revision of each part and offers the vendor package (supplier | codes)."""
    _queue("purchase_order", doc.name, f"{doc.supplier_name or doc.supplier or ''}|{_ordered_codes(doc)}")


def purchase_order_cancelled(doc, method=None):
    _queue("purchase_order", doc.name, f"{doc.supplier_name or doc.supplier or ''}||cancelled")


def rfq_submitted(doc, method=None):
    suppliers = ", ".join(s.supplier for s in (doc.get("suppliers") or []) if s.supplier)
    _queue("rfq", doc.name, f"{suppliers}|{_ordered_codes(doc)}")


def subcontracting_order_submitted(doc, method=None):
    _queue("subcontracting_order", doc.name, f"{doc.supplier_name or doc.supplier or ''}|"
           + ",".join(dict.fromkeys(r.item_code for r in (doc.get("items") or []) if r.item_code)))


def customer_changed(doc, method=None):
    _queue("customer", doc.customer_name or doc.name, "disabled" if doc.disabled else "")


def supplier_changed(doc, method=None):
    _queue("supplier", doc.supplier_name or doc.name, "disabled" if doc.disabled else "")


def price_list_changed(doc, method=None):
    _queue("price_list", doc.price_list or doc.name)


def send(events):
    """Runs in a worker. A failure is logged and retried by the queue; ERPNext data is never affected."""
    s = _settings()
    if not s:
        return
    body = json.dumps({"events": events}).encode("utf-8")
    secret = s.get_password("webhook_secret", raise_exception=False) or ""
    headers = {"Content-Type": "application/json", **sign(secret, body)}
    url = s.kosha_url.rstrip("/") + "/api/erp/webhook"
    r = requests.post(url, data=body, headers=headers, timeout=TIMEOUT_SECONDS)
    if r.status_code >= 400:
        frappe.log_error(title="Kosha notice refused", message=f"{r.status_code}: {r.text[:500]}")
