# Copyright (c) 2026, Upande LTD and contributors

"""Stock Entry custom fields for the upande_scp store-keeper transfer flow.

These are created programmatically on install/migrate and removed on uninstall
(NOT shipped as fixtures), mirroring the Leave Type ``abbreviation`` field.
They bind the two child DocTypes shipped by this app into Stock Entry:

    Stock Entry.employee_data      Table -> Employee Request
    Stock Entry.biometric_data     Table -> Biometric Data
    Stock Entry.biometric_verified   Check (set on biometric-authorized submit)
"""

import frappe


SCP_STOCK_ENTRY_FIELDS = ("employee_data", "biometric_data", "biometric_verified")


def ensure_scp_stock_entry_fields():
	"""Create the Stock Entry custom fields the SCP store-keeper flow reads/writes.

	Idempotent (``update=True``). No-op if ERPNext's Stock Entry table isn't
	present (e.g. ERPNext not yet installed at this point).
	"""
	if not frappe.db.table_exists("Stock Entry"):
		return

	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Stock Entry": [
				{
					"fieldname": "employee_data",
					"label": "Employee Data",
					"fieldtype": "Table",
					"options": "Employee Request",
					"insert_after": "items",
					"module": "Upande TA",
				},
				{
					"fieldname": "biometric_data",
					"label": "Biometric Data",
					"fieldtype": "Table",
					"options": "Biometric Data",
					"insert_after": "employee_data",
					"module": "Upande TA",
				},
				{
					"fieldname": "biometric_verified",
					"label": "Biometric Verified",
					"fieldtype": "Check",
					"default": "0",
					"read_only": 1,
					"insert_after": "biometric_data",
					"module": "Upande TA",
				},
			]
		},
		update=True,
	)


def remove_scp_stock_entry_fields():
	"""Delete the Stock Entry custom fields on uninstall."""
	for fieldname in SCP_STOCK_ENTRY_FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": "Stock Entry", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
