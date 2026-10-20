# Copyright (c) 2026, Upande LTD and contributors

"""Attendance Insights' Biometric Devices panel shows when each device last
sent a punch: MAX(time) per device_id. Without an index that is a scan of
every check-in on every register load; with (device_id, time) it is one
index lookup per device."""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Employee Checkin"):
		return
	# add_index is a no-op when the index is already there
	frappe.db.add_index("Employee Checkin", ["device_id", "`time`"], index_name="checkin_device_time")
