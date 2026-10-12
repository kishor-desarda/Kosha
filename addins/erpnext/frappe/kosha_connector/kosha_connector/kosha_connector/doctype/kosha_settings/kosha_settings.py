# SPDX-License-Identifier: Apache-2.0
import frappe
from frappe.model.document import Document


class KoshaSettings(Document):
    def validate(self):
        if self.enabled and not (self.kosha_url or "").startswith(("https://", "http://")):
            frappe.throw("Enter Kosha's address, starting with https://")
        if self.enabled and not self.webhook_secret:
            frappe.throw("Set a webhook secret; Kosha refuses unsigned calls.")
