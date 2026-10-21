# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Drop the Worked hours from Bulk Overtime.

A batch works on overtime alone; the hours worked behind it are read from the
attendance when it is refreshed and shown in Verify Check-ins, so the row no
longer keeps them. Migrate takes the fields off the doctypes; this takes the
columns out of the tables, and any customisation left pointing at them.
"""

import frappe

COLUMNS = {
	"Bulk Overtime Entry": ("working_hours", "manual_working_hours"),
	"Overtime Edit Company": ("working_hours",),
}


def execute():
	for doctype, fields in COLUMNS.items():
		for field in fields:
			if frappe.get_meta(doctype).has_field(field):
				# still defined (a Custom Field, or the doctype not synced yet)
				continue
			frappe.db.delete("Property Setter", {"doc_type": doctype, "field_name": field})
			if frappe.db.has_column(doctype, field):
				frappe.db.sql_ddl(f"alter table `tab{doctype}` drop column `{field}`")
	frappe.clear_cache(doctype="Bulk Overtime")
