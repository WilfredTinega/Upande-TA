# Copyright (c) 2026, Upande LTD and contributors

"""Drop the Stock Entry biometric-verification integration.

``overrides/stock_entry.py`` used to create a "Biometric Verification" section on
Stock Entry plus a ``require_biometric`` flag on Stock Entry Type, reconciling
them on every migrate. That module is gone, so nothing prunes those custom
fields anymore — this patch removes them once.

Strictly module-scoped (``module == "Upande TA"``): other apps (e.g.
upande_kaitet) ship their own Stock Entry customizations that must not be
touched.

Stock Entry is cleaned BEFORE Stock Entry Type: ``Stock Entry.requires_biometric``
carries ``fetch_from: stock_entry_type.require_biometric``, and a field whose
fetch_from points at a deleted field raises on every Stock Entry creation.
"""

import frappe


MODULE = "Upande TA"
# Order matters — see the module docstring.
MANAGED_DOCTYPES = ("Stock Entry", "Stock Entry Type")


def execute():
	removed = 0
	for doctype in MANAGED_DOCTYPES:
		for name in frappe.get_all(
			"Custom Field", filters={"dt": doctype, "module": MODULE}, pluck="name"
		):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
			removed += 1
		frappe.db.commit()

	print(f"[remove_stock_entry_biometric] removed {removed} custom field(s)")
