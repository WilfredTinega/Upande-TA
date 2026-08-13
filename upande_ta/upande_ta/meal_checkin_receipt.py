# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Attach a "Meal Receipt" PDF to a Meal Checkin record on creation.

Hooked via ``doc_events`` -> Meal Checkin -> after_insert. Physical printing
happens client-side on the kitchen terminal (see meal_checkin.js / the QZ Tray
Printer Settings page); this only keeps a PDF copy on the record.
"""

import frappe

MEALS = ("Breakfast", "Lunch", "Supper", "Dinner")


def attach_receipt(doc, method=None):
	if doc.log_type not in MEALS:
		return
	try:
		out = frappe.attach_print(
			"Meal Checkin",
			doc.name,
			file_name="Meal-Receipt-" + doc.name,
			print_format="Meal Receipt",
		)

		# Keep exactly one receipt: drop any previous ones for this record.
		for fname in frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "Meal Checkin",
				"attached_to_name": doc.name,
				"file_name": ["like", "Meal-Receipt-%"],
			},
			pluck="name",
		):
			frappe.delete_doc("File", fname, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": out["fname"],
				"attached_to_doctype": "Meal Checkin",
				"attached_to_name": doc.name,
				"is_private": 1,
				"content": out["fcontent"],
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Meal Checkin receipt failed: " + (doc.name or ""))
