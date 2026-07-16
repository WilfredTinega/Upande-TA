# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe


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
