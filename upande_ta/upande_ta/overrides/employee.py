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
