# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""The Roster's week offs, judged by the holiday list in force on each day.

Stock HRMS reads one holiday list per employee, the one in force on the
month's last day, for the whole month. A week off moved part way through the
month (a Holiday List Assignment window, as the Monthly Attendance Sheet
writes) therefore showed on the wrong days, or not at all."""

import frappe
from frappe.utils import getdate
from hrms.api import roster


@frappe.whitelist()
def get_events(month_start, month_end, employee_filters, shift_filters) -> dict[str, list[dict]]:
	from upande_ta.upande_ta.api.biometric_templates import effective_off_days

	employee_filters = frappe.parse_json(employee_filters) or {}
	shift_filters = frappe.parse_json(shift_filters) or {}
	events = roster.get_events(month_start, month_end, employee_filters, shift_filters)

	people = frappe.get_list("Employee", filters=employee_filters, fields=["name", "company", "holiday_list"])
	off = effective_off_days(people, getdate(month_start), getdate(month_end))
	for person in people:
		kept = [event for event in events.get(person.name, []) if "holiday" not in event]
		kept.extend(
			{
				"holiday": row.get("name"),
				"holiday_date": day,
				"description": row.get("description"),
				"weekly_off": row.get("weekly_off"),
			}
			for day, row in sorted((off.get(person.name) or {}).items())
		)
		if kept:
			events[person.name] = kept
		else:
			events.pop(person.name, None)
	return events
