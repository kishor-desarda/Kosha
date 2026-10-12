// SPDX-License-Identifier: Apache-2.0
// Adds "View in Kosha" to the Item form.
frappe.ui.form.on('Item', {
  refresh(frm) {
    if (frm.is_new()) return;
    frappe.db.get_single_value('Kosha Settings', 'kosha_url').then(url => {
      if (!url) return;
      frm.add_custom_button(__('View in Kosha'), () => {
        window.open(`${url.replace(/\/$/, '')}/#/search?q=${encodeURIComponent(frm.doc.item_code)}`, '_blank', 'noopener');
      });
    });
  },
});
