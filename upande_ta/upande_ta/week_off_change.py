# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Change or remove one employee's week off from a date, clicked on the
Monthly Attendance Sheet.

Change: the day clicked becomes the employee's only week off, every week, from
that date to the end date given. Remove: the weekday clicked stops being a
week off for that window, and any other week off stays.

Either way the list they are on is replicated for the window — its public
holidays kept, its weekly offs rewritten — and assigned through the same
window rule as the Holiday Assignment Tool: the window owns its days, and the
day after it the employee returns to the list they would otherwise have been
on.
"""

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, get_link_to_form, getdate

from upande_ta.upande_ta.doctype.holiday_assignment_tool.holiday_assignment_tool import (
	assign_holiday_window,
)

#: A window longer than this is a slip of the date picker, not a week-off change.
MAX_WINDOW_DAYS = 366


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@frappe.whitelist(methods=["POST"])
def change_week_off(employee: str, from_date: str, to_date: str, weekdays=None) -> dict:
	"""``weekdays`` — the clicked day's by default — become the only week off
	from ``from_date`` to ``to_date``. Some staff take two."""
	start, end, days = _window(employee, from_date, to_date)

	chosen = _weekdays(weekdays) or [start.strftime("%A")]
	numbers = {WEEKDAYS.index(day) for day in chosen}
	weekday = " & ".join(chosen)

	wanted = {date: date.weekday() in numbers for date in days}
	# a public holiday stays what it is either way, so it cannot be a change
	if all(days[date]["public"] or days[date]["weekly_off"] == off for date, off in wanted.items()):
		frappe.throw(
			_("{0} is already their only week off from {1} to {2}.").format(
				weekday, frappe.format(start, "Date"), frappe.format(end, "Date")
			)
		)

	name = "{0} {1} Week Off {2} to {3}".format(employee, weekday, _day(start), _day(end))
	window_list = replicate(name, start, end, days, keep_off=lambda date: wanted[date])
	result = _assign(employee, window_list, start, end, weekday)
	result.update(_clear_absent_on_week_off(employee, window_list))
	return result


@frappe.whitelist(methods=["POST"])
def remove_week_off(employee: str, from_date: str, to_date: str) -> dict:
	"""The clicked weekday is no longer a week off from ``from_date`` to
	``to_date``; any other week off stays."""
	start, end, days = _window(employee, from_date, to_date)
	weekday = start.strftime("%A")

	if not any(day["weekly_off"] and date.weekday() == start.weekday() for date, day in days.items()):
		frappe.throw(
			_("{0} is not a week off from {1} to {2}.").format(
				weekday, frappe.format(start, "Date"), frappe.format(end, "Date")
			)
		)

	name = "{0} No {1} Week Off {2} to {3}".format(employee, weekday, _day(start), _day(end))
	window_list = replicate(
		name,
		start,
		end,
		days,
		keep_off=lambda date: days[date]["weekly_off"] and date.weekday() != start.weekday(),
	)
	return _assign(employee, window_list, start, end, weekday)


def _weekdays(value) -> list:
	"""Weekday names, in week order, from what the desk sends: a JSON array,
	a list, or one name."""
	if not value:
		return []
	if isinstance(value, str):
		value = frappe.parse_json(value) if value.strip().startswith("[") else [value]
	names = {str(day).strip().title() for day in value}
	unknown = names - set(WEEKDAYS)
	if unknown:
		frappe.throw(_("{0} is not a weekday.").format(", ".join(sorted(unknown))))
	return [day for day in WEEKDAYS if day in names]


def _window(employee, from_date, to_date):
	"""Checked dates, and what each day of them is now."""
	from upande_ta.upande_ta.overrides.monthly_attendance_sheet import week_off_change_disabled

	if week_off_change_disabled():
		frappe.throw(_("Week Off Change is disabled in Biometric Setting."))
	frappe.has_permission("Holiday List Assignment", "create", throw=True)
	frappe.has_permission("Holiday List", "create", throw=True)
	frappe.has_permission("Employee", doc=employee, throw=True)

	start, end = getdate(from_date), getdate(to_date)
	if end < start:
		frappe.throw(_("End Date cannot be before {0}.").format(frappe.format(start, "Date")))
	if date_diff(end, start) >= MAX_WINDOW_DAYS:
		frappe.throw(_("A week-off change cannot run longer than {0} days.").format(MAX_WINDOW_DAYS))

	days = days_in_force(employee, start, end)
	if not any(day["holiday_list"] for day in days.values()):
		frappe.throw(
			_("{0} has no holiday list from {1} to {2} to change.").format(
				frappe.bold(employee), frappe.format(start, "Date"), frappe.format(end, "Date")
			)
		)
	return start, end, days


def days_in_force(employee, start, end) -> dict:
	"""``{date: {holiday_list, weekly_off, public}}`` for every day of
	``start .. end``, as HRMS resolves it — the employee's assignments, their
	company's filling the gaps — so a window laid over an earlier change reads
	that change for the days it covers and the list beyond it for the rest."""
	from upande_ta.upande_ta.holiday_ranges import (
		fill_employee_holiday_list_date_gaps_with_company_holiday_list,
		get_assigned_holiday_lists_to_employee_and_company,
	)

	company = frappe.db.get_value("Employee", employee, "company")
	assigned = get_assigned_holiday_lists_to_employee_and_company([employee, company], start, end)
	ranges = fill_employee_holiday_list_date_gaps_with_company_holiday_list(
		assigned.get(employee, []), assigned.get(company, []), start, end
	)

	holidays = {}
	lists = {r["holiday_list"] for r in ranges}
	if lists:
		for h in frappe.get_all(
			"Holiday",
			filters={"parent": ["in", list(lists)], "holiday_date": ["between", [start, end]]},
			fields=["parent", "holiday_date", "weekly_off", "description"],
		):
			holidays[(h.parent, getdate(h.holiday_date))] = h

	days, date = {}, start
	while date <= end:
		holiday_list = next(
			(r["holiday_list"] for r in ranges if getdate(r["from_date"]) <= date <= getdate(r["to_date"])),
			None,
		)
		h = holidays.get((holiday_list, date))
		days[date] = {
			"holiday_list": holiday_list,
			"weekly_off": bool(h and h.weekly_off),
			"public": h.description if h and not h.weekly_off else None,
		}
		date = add_days(date, 1)
	return days


def _assign(employee, window_list, start, end, weekday) -> dict:
	from hrms.utils.holiday_list import get_assigned_holiday_list

	# read before the window is cleared, so an earlier change's own return
	# still counts
	after = add_days(end, 1)
	restore_to = get_assigned_holiday_list(employee, as_on=after)
	if restore_to and not _list_covers(restore_to, after):
		# nothing to hand back to past the end of that list's own period
		restore_to = None

	created, replaced = assign_holiday_window(employee, window_list, start, end, restore_to)
	return {
		"holiday_list": window_list,
		"weekday": weekday,
		"restored_to": restore_to,
		"created": [get_link_to_form("Holiday List Assignment", name) for name in created],
		"replaced": replaced,
	}


def _day(date) -> str:
	return date.strftime("%d-%m-%Y")


def replicate(name: str, start, end, days: dict, keep_off) -> str:
	"""A holiday list for ``start .. end``: each day's public holiday as it is
	now, and a weekly off on every date ``keep_off(date)`` accepts. A public
	holiday stays a public holiday whatever weekday it falls on."""
	holidays = []
	for date in sorted(days):
		if days[date]["public"]:
			holidays.append({"holiday_date": date, "description": days[date]["public"], "weekly_off": 0})
		elif keep_off(date):
			holidays.append({"holiday_date": date, "description": date.strftime("%A"), "weekly_off": 1})
	weekly = sorted({h["description"] for h in holidays if h["weekly_off"]})
	source = next((day["holiday_list"] for day in days.values() if day["holiday_list"]), None)

	if frappe.db.exists("Holiday List", name):
		doc = frappe.get_doc("Holiday List", name)
		doc.set("holidays", holidays)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": name,
				"from_date": start,
				"to_date": end,
				"weekly_off": weekly[0] if len(weekly) == 1 else None,
				"holidays": holidays,
			}
		)
	for field in ("country", "subdivision"):
		if source and doc.meta.has_field(field) and not doc.get(field):
			doc.set(field, frappe.db.get_value("Holiday List", source, field))
	doc.flags.ignore_permissions = True
	doc.save()
	return doc.name


def _clear_absent_on_week_off(employee: str, holiday_list: str) -> dict:
	"""Cancel and delete the Absent attendance on the new week off: it was
	written before that day was a rest day. One that cannot go — no permission,
	or something linked to it — is left and named."""
	absent = frappe.db.sql(
		"""
		select a.name, a.attendance_date
		from `tabAttendance` a
		join `tabHoliday` h on h.holiday_date = a.attendance_date
		where h.parent = %s and h.weekly_off = 1
			and a.employee = %s and a.docstatus < 2 and a.status = 'Absent'
		order by a.attendance_date
		""",
		(holiday_list, employee),
		as_dict=True,
	)
	removed, kept = [], []
	for row in absent:
		try:
			frappe.db.savepoint("week_off_absent")
			attendance = frappe.get_doc("Attendance", row.name)
			if attendance.docstatus == 1:
				attendance.cancel()
			frappe.delete_doc("Attendance", row.name, delete_permanently=True)
			removed.append(str(row.attendance_date))
		except Exception:
			frappe.db.rollback(save_point="week_off_absent")
			frappe.clear_last_message()
			kept.append(str(row.attendance_date))
	return {"absent_removed": removed, "absent_on_week_off": kept}


def _list_covers(holiday_list: str, date) -> bool:
	period = frappe.db.get_value("Holiday List", holiday_list, ["from_date", "to_date"])
	return bool(period and period[0] and period[1] and getdate(period[0]) <= date <= getdate(period[1]))
