# SPDX-License-Identifier: Apache-2.0
"""Adds and removes the Kosha fields. Business data (items, BOMs, stock) is never touched."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELDS = {
    "Item": [
        {"fieldname": "kosha_section", "label": "Kosha", "fieldtype": "Section Break", "insert_after": "description",
         "collapsible": 1},
        {"fieldname": "custom_kosha_revision", "label": "Kosha revision", "fieldtype": "Data", "read_only": 1,
         "insert_after": "kosha_section"},
        {"fieldname": "custom_kosha_material", "label": "Material (from Kosha)", "fieldtype": "Data", "read_only": 1,
         "insert_after": "custom_kosha_revision"},
    ],
    "BOM": [
        {"fieldname": "custom_kosha_revision", "label": "Kosha revision", "fieldtype": "Data", "read_only": 1,
         "insert_after": "item"},
    ],
}


def after_install():
    create_custom_fields(FIELDS, update=True)


def before_uninstall():
    for doctype, fields in FIELDS.items():
        for f in fields:
            name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": f["fieldname"]})
            if name:
                frappe.delete_doc("Custom Field", name, ignore_permissions=True)
