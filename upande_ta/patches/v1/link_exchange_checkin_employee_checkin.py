# Copyright (c) 2026, Upande LTD and contributors

"""Exchange Checkins of this instance's own employees made before they
carried a link: point them at the Employee Checkin made for the same punch."""

import frappe


def execute():
	if not frappe.db.has_column("Exchange Checkin", "employee_checkin"):
		return
	from upande_ta.upande_ta import exchange

	code = exchange.my_code()
	if not code:
		return
	field = (exchange.settings().payroll_number_field or "attendance_device_id").strip()
	for row in frappe.get_all(
		"Exchange Checkin",
		filters={"home_instance": code, "employee_checkin": ["is", "not set"]},
		fields=["name", "payroll_number", "time", "log_type"],
	):
		employee = frappe.db.get_value("Employee", {field: row.payroll_number}) or frappe.db.exists(
			"Employee", row.payroll_number or ""
		)
		if not employee:
			continue
		checkin = exchange.same_punch("Employee Checkin", "employee", employee, row.time, row.log_type)
		if checkin:
			frappe.db.set_value(
				"Exchange Checkin",
				row.name,
				{"employee": employee, "employee_checkin": checkin},
				update_modified=False,
			)
