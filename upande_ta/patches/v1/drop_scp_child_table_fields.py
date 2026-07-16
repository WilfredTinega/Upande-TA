# Copyright (c) 2026, Upande LTD and contributors

"""Remove the superseded SCP child-table approach.

An earlier version added Stock Entry child-table fields (``employee_data`` /
``biometric_data`` / ``biometric_verified`` and their ``custom_``-prefixed
predecessors) backed by the "Employee Request" and "Biometric Data" child
DocTypes. These were replaced by the "Biometric Verification" section (see
``overrides/stock_entry.py``), so this patch removes the stale custom fields and
DocTypes from sites that already had them.

Idempotent and safe: a no-op on sites that never installed the old approach.
Custom fields are dropped first so the DocTypes (used as their Table options)
delete cleanly.
"""

import frappe


OLD_STOCK_ENTRY_FIELDS = (
	"custom_employee_data",
	"custom_biometric_data",
	"custom_biometric_verified",
	"employee_data",
	"biometric_data",
	"biometric_verified",
)

OLD_DOCTYPES = ("Employee Request", "Biometric Data")


def execute():
	# 1. Drop the old Stock Entry custom fields (both naming variants).
	for fieldname in OLD_STOCK_ENTRY_FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": "Stock Entry", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)

	# 2. Drop the old child DocTypes (also drops their DB tables).
	for doctype in OLD_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc(
				"DocType", doctype, ignore_permissions=True, force=True, ignore_missing=True
			)

	frappe.db.commit()
