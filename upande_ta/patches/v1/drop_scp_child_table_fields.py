# Copyright (c) 2026, Upande LTD and contributors

"""Remove the superseded SCP child-table approach — but ONLY artifacts this app
actually owns (``module == "Upande TA"``).

An earlier iteration of upande_ta briefly shipped ``Employee Request`` /
``Biometric Data`` child DocTypes and Stock Entry Table fields, replaced by the
"Biometric Verification" section. This drops any such DocTypes that belong to
upande_ta.

IMPORTANT: it is strictly module-scoped. Other apps (e.g. upande_kaitet) ship
DocTypes with the SAME names and their own Stock Entry customizations — those
must never be touched here. Upande-TA-owned custom fields no longer in the spec
are pruned by ``overrides.stock_entry.ensure_biometric_stock_entry_fields``
(module-scoped reconciliation), so this patch only handles DocTypes.
"""

import frappe


OLD_DOCTYPES = ("Employee Request", "Biometric Data")
MODULE = "Upande TA"


def execute():
	for doctype in OLD_DOCTYPES:
		if frappe.db.get_value("DocType", doctype, "module") == MODULE:
			frappe.delete_doc(
				"DocType", doctype, ignore_permissions=True, force=True, ignore_missing=True
			)

	frappe.db.commit()
