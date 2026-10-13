# SPDX-License-Identifier: Apache-2.0
app_name = "kosha_connector"
app_title = "Kosha Connector"
app_publisher = "Kosha contributors"
app_description = "Kosha PLM connector for ERPNext"
app_email = "noreply@example.com"
app_license = "Apache-2.0"
required_apps = ["erpnext"]

after_install = "kosha_connector.install.after_install"
before_uninstall = "kosha_connector.install.before_uninstall"

doctype_js = {"Item": "public/js/item.js"}

doc_events = {
    "Item": {"on_update": "kosha_connector.notify.item_changed"},
    "BOM": {"on_cancel": "kosha_connector.notify.bom_cancelled"},
    "Stock Ledger Entry": {"on_submit": "kosha_connector.notify.stock_changed"},
    "Sales Order": {"on_submit": "kosha_connector.notify.sales_order_submitted"},
    "Purchase Order": {"on_submit": "kosha_connector.notify.purchase_order_submitted",
                       "on_cancel": "kosha_connector.notify.purchase_order_cancelled"},
    "Request for Quotation": {"on_submit": "kosha_connector.notify.rfq_submitted"},
    "Subcontracting Order": {"on_submit": "kosha_connector.notify.subcontracting_order_submitted"},
    "Customer": {"on_update": "kosha_connector.notify.customer_changed"},
    "Supplier": {"on_update": "kosha_connector.notify.supplier_changed"},
    "Item Price": {"on_update": "kosha_connector.notify.price_list_changed"},
}
