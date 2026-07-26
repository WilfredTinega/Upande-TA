# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe

CUSTOM_COST_CENTER_CF = "Employee-custom_cost_center"


def ensure_custom_cost_center_optional():
	"""Guarantee ``Employee.custom_cost_center`` is never mandatory.

	Wired into ``after_migrate`` (runs on every ``bench migrate`` — and so on
	every Frappe Cloud deploy). It defensively forces the field optional so that
	nothing — a Customize Form edit, a Property Setter, or a fixture re-applied on
	migrate — can leave it required and block saving/submitting Employees.

	Clears three possible sources of "mandatory":
	  1. the Custom Field's own ``reqd`` flag,
	  2. its ``mandatory_depends_on`` expression,
	  3. any ``Property Setter`` that sets ``reqd = 1`` for this field.

	Uses raw ``db.set_value`` / deletes (not ``doc.save``) and clears the Employee
	cache so it takes effect immediately. Must never raise — a failure here should
	not abort the migrate.
	"""
	try:
		changed = False

		if frappe.db.exists("Custom Field", CUSTOM_COST_CENTER_CF):
			cf = frappe.db.get_value(
				"Custom Field", CUSTOM_COST_CENTER_CF, ["reqd", "mandatory_depends_on"], as_dict=True
			)
			if cf and cf.reqd:
				frappe.db.set_value("Custom Field", CUSTOM_COST_CENTER_CF, "reqd", 0)
				changed = True
			if cf and cf.mandatory_depends_on:
				frappe.db.set_value(
					"Custom Field", CUSTOM_COST_CENTER_CF, "mandatory_depends_on", ""
				)
				changed = True

		# A Property Setter making it reqd would override the Custom Field flag.
		reqd_setters = frappe.get_all(
			"Property Setter",
			filters={"doc_type": "Employee", "field_name": "custom_cost_center", "property": "reqd"},
			pluck="name",
		)
		for ps in reqd_setters:
			frappe.delete_doc("Property Setter", ps, ignore_permissions=True, force=True)
			changed = True

		if changed:
			frappe.clear_cache(doctype="Employee")
	except Exception:
		frappe.log_error(
			title="upande_ta ensure_custom_cost_center_optional failed",
			message=frappe.get_traceback(),
		)


def set_attendance_device_id(doc, method=None):
	if doc.attendance_device_id or not doc.name:
		return

	if method == "after_insert":
		doc.db_set("attendance_device_id", doc.name, update_modified=False)
	else:
		doc.attendance_device_id = doc.name


def sync_attendance_device_id_change(doc, method=None):
	"""When an employee's device PIN (attendance_device_id / payroll number)
	changes, re-key their biometric enrollments and re-sync the devices."""
	before = doc.get_doc_before_save()
	if not before:
		return

	old_pin = (before.attendance_device_id or "").strip()
	new_pin = (doc.attendance_device_id or "").strip()
	if not old_pin or old_pin == new_pin:
		return

	from upande_ta.upande_ta.doctype.biometric_user.biometric_user import handle_pin_change

	handle_pin_change(doc.name, old_pin, new_pin)
