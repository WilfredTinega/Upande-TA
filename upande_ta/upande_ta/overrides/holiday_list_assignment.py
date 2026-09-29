# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""A week off assigned from a date in the past lands on days auto-attendance
may already have marked Absent. ``overrides/shift_type.py`` stops new ones
being marked; this cancels the ones already there, once, when the assignment
is submitted.

Only what auto-attendance itself wrote is touched: an Absent owned by
Administrator, on a shift with auto attendance enabled, with no check-in
behind it. An Absent HR marked by hand is their decision and stays.
"""

import frappe
from frappe.utils import add_days, getdate, today


def cancel_auto_absent_on_week_off(doc, method=None):
	if doc.applicable_for != "Employee" or not doc.assigned_to or not doc.from_date:
		return

	start, end = getdate(doc.from_date), getdate(today())
	if start > end:
		return
	list_end = frappe.db.get_value("Holiday List", doc.holiday_list, "to_date")
	if list_end:
		end = min(end, getdate(list_end))
	later = frappe.db.get_value(
		"Holiday List Assignment",
		{
			"assigned_to": doc.assigned_to,
			"docstatus": 1,
			"from_date": [">", doc.from_date],
			"name": ["!=", doc.name],
		},
		"from_date",
		order_by="from_date asc",
	)
	if later:
		end = min(end, add_days(getdate(later), -1))
	if start > end:
		return

	absent = frappe.db.sql(
		"""
		select a.name
		from `tabAttendance` a
		join `tabShift Type` st on st.name = a.shift and st.enable_auto_attendance = 1
		where a.employee = %(employee)s
			and a.attendance_date between %(start)s and %(end)s
			and a.status = 'Absent' and a.docstatus = 1 and a.owner = 'Administrator'
			and not exists (select 1 from `tabEmployee Checkin` c where c.attendance = a.name)
		""",
		{"employee": doc.assigned_to, "start": start, "end": end},
		as_dict=True,
	)
	if not absent:
		return

	from upande_ta.upande_ta.week_off_change import days_in_force

	days = days_in_force(doc.assigned_to, start, end)
	for row in absent:
		attendance = frappe.get_doc("Attendance", row.name)
		day = days.get(getdate(attendance.attendance_date)) or {}
		if not (day.get("weekly_off") or day.get("public")):
			continue
		try:
			frappe.db.savepoint("auto_absent_on_week_off")
			attendance.flags.ignore_permissions = True
			attendance.add_comment(
				"Comment",
				frappe._("Cancelled: {0} is a week off or holiday under Holiday List Assignment {1}.").format(
					frappe.format(attendance.attendance_date, "Date"), doc.name
				),
			)
			attendance.cancel()
		except Exception:
			frappe.db.rollback(save_point="auto_absent_on_week_off")
			frappe.clear_last_message()
			frappe.log_error(
				title=f"Could not cancel Absent {row.name} on a week off",
				message=frappe.get_traceback(),
			)
