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


def auto_add_to_devices_on_import(doc, method=None):
	if not frappe.flags.in_import:
		return
	if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
		return

	settings = frappe.get_single("Biometric Setting")
	device_sns = [d.device_sn for d in (settings.devices or []) if d.device_sn]
	if not device_sns:
		return

	if not (doc.attendance_device_id or "").strip():
		return

	try:
		from upande_ta.upande_ta.doctype.biometric_user.biometric_user import (
			add_employees_to_devices,
		)
		add_employees_to_devices([doc.name], device_sns)
	except Exception as e:
		frappe.log_error(
			f"Auto-add imported employee {doc.name} to devices failed: {e}",
			"Biometric Auto-Add (Import)",
		)
