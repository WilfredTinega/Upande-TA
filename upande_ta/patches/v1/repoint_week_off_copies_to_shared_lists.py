# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Undo the per-employee Holiday Lists the old Change Week Off made.

Before v1.2.0 every week-off change on the Monthly Attendance Sheet copied the
employee's list as ``"<employee> <Weekday> Week Off <from> to <to>"`` — 473 of
504 lists on kaitet-group. Each copy is replaced by the shared list keeping the
same week offs through its period ("Sunday Week Off 2026"), found the way the
sheet now finds it:

- every Holiday List Assignment on the copy, submitted or cancelled, keeps its
  own dates and moves to the shared list, with a comment naming the copy;
- Leave Ledger Entries that counted against the copy move with them;
- the copy is deleted.

The copies took their public holidays from the list the employee was on, and a
shared list records one landing on its own week off as a plain week off, so a
move from Saturday to Sunday lost Mazingira, Jamhuri and Boxing Day. Those come
back with the shared list, and the Absents auto-attendance marked on them are
cancelled as a newly submitted assignment would cancel them.

A copy with no shared list to match (one running into a year with no list yet)
is left as it is.
"""

import frappe
from frappe.utils import add_days, getdate


def execute():
	from upande_ta.upande_ta.overrides.holiday_list_assignment import cancel_auto_absent_on_week_off
	from upande_ta.upande_ta.week_off_change import matching_list

	for copy in _copies():
		weekdays = _weekdays_if_uniform(copy)
		if weekdays is None:
			continue
		target = matching_list(copy.from_date, copy.to_date, weekdays)
		if not target:
			continue

		try:
			frappe.db.savepoint("week_off_copy")
			assignments = frappe.get_all(
				"Holiday List Assignment", filters={"holiday_list": copy.name}, fields=["name", "docstatus"]
			)
			for row in assignments:
				frappe.db.set_value(
					"Holiday List Assignment", row.name, "holiday_list", target, update_modified=False
				)
				frappe.get_doc("Holiday List Assignment", row.name).add_comment(
					"Comment",
					f"Holiday List moved from the per-employee copy {copy.name} to {target}.",
				)
			frappe.db.sql(
				"update `tabLeave Ledger Entry` set holiday_list = %s where holiday_list = %s",
				(target, copy.name),
			)
			frappe.delete_doc("Holiday List", copy.name, ignore_permissions=True, force=True)
		except Exception:
			frappe.db.rollback(save_point="week_off_copy")
			frappe.log_error(
				title=f"Week-off copy {copy.name} not replaced",
				message=frappe.get_traceback(),
			)
			continue

		for row in assignments:
			if row.docstatus != 1:
				continue
			try:
				frappe.db.savepoint("week_off_copy_absent")
				cancel_auto_absent_on_week_off(frappe.get_doc("Holiday List Assignment", row.name))
			except Exception:
				frappe.db.rollback(save_point="week_off_copy_absent")
				frappe.log_error(
					title=f"Absents on {row.name} not cleared",
					message=frappe.get_traceback(),
				)

	frappe.cache.delete_value("holidays_between_dates")


def _copies() -> list:
	lists = frappe.get_all(
		"Holiday List",
		filters={"name": ["like", "% Week Off % to %"]},
		fields=["name", "from_date", "to_date"],
	)
	first_words = {row.name.split(" ", 1)[0] for row in lists}
	employees = set(frappe.get_all("Employee", filters={"name": ["in", list(first_words)]}, pluck="name"))
	return [row for row in lists if row.name.split(" ", 1)[0] in employees and row.from_date and row.to_date]


def _weekdays_if_uniform(copy) -> set | None:
	"""The weekdays the copy keeps off, when every one of them is a holiday
	all through its period — otherwise no single shared list can stand in."""
	holidays = {
		getdate(h.holiday_date): h.weekly_off
		for h in frappe.get_all(
			"Holiday", filters={"parent": copy.name}, fields=["holiday_date", "weekly_off"]
		)
	}
	weekdays = {date.weekday() for date, weekly_off in holidays.items() if weekly_off}
	date, end = getdate(copy.from_date), getdate(copy.to_date)
	while date <= end:
		if date.weekday() in weekdays and date not in holidays:
			return None
		date = add_days(date, 1)
	return weekdays
