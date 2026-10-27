# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Change or remove one employee's week off from a date, clicked on the
Monthly Attendance Sheet.

Change: the day clicked becomes the employee's only week off, every week, from
that date to the end date given. Remove: the weekday clicked stops being a
week off for that window, and any other week off stays.

Either way the existing list keeping those week offs all through the window —
"Friday Week Off 2026" — is assigned as it is, public holidays and all. No
holiday list is ever made here: with none to assign, the change is refused. It
is assigned
through the same window rule as the Holiday Assignment Tool: the window owns
its days, and the day after it the employee returns to the list they would
otherwise have been on.
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

	window_list, end = existing_list(start, end, numbers, days)
	result = _assign(employee, window_list, start, end, weekday)
	result.update(_clear_absent_on_week_off(employee, window_list, start, end))
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

	# the week offs of the list in force on the day clicked, less that weekday
	kept = {
		date.weekday()
		for date, day in days.items()
		if day["weekly_off"] and day["holiday_list"] == days[start]["holiday_list"]
	} - {start.weekday()}
	window_list, end = existing_list(start, end, kept, days)
	return _assign(employee, window_list, start, end, weekday)


@frappe.whitelist()
def holiday_list_on(employee: str, date: str) -> str | None:
	"""The holiday list in force for the employee on ``date``, if any."""
	frappe.has_permission("Employee", doc=employee, throw=True)
	date = getdate(date)
	return days_in_force(employee, date, date)[date]["holiday_list"]


@frappe.whitelist(methods=["POST"])
def assign_holiday_list(employee: str, holiday_list: str, from_date: str) -> dict:
	"""Put an employee who has no holiday list on ``holiday_list`` from
	``from_date`` on."""
	from upande_ta.upande_ta.overrides.monthly_attendance_sheet import week_off_change_disabled

	if week_off_change_disabled():
		frappe.throw(_("Week Off Change is disabled in Biometric Setting."))
	frappe.has_permission("Holiday List Assignment", "create", throw=True)
	frappe.has_permission("Employee", doc=employee, throw=True)

	start = getdate(from_date)
	if not _list_covers(holiday_list, start):
		frappe.throw(
			_("{0} does not cover {1}.").format(frappe.bold(holiday_list), frappe.format(start, "Date"))
		)
	created, replaced = assign_holiday_window(employee, holiday_list, start, None, None)
	return {
		"holiday_list": holiday_list,
		"created": [get_link_to_form("Holiday List Assignment", name) for name in created],
		"replaced": replaced,
	}


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


def existing_list(start, end, weekdays: set, days: dict) -> tuple[str, object]:
	"""``(holiday_list, end)``: the list keeping exactly ``weekdays`` off from
	``start`` — the one already in force when it does. Lists run a year, so a
	window past the last one's end is assigned up to that end."""
	in_force = {day["holiday_list"] for day in days.values() if day["holiday_list"]}
	found = matching_list(start, end, weekdays, prefer=in_force)
	if not found and end.year > start.year:
		end = start.replace(month=12, day=31)
		found = matching_list(start, end, weekdays, prefer=in_force)
	if not found:
		names = [WEEKDAYS[number] for number in sorted(weekdays)]
		frappe.throw(
			_("No holiday list has {0} as the week off from {1} to {2}.").format(
				" & ".join(names) if names else _("no day"),
				frappe.format(start, "Date"),
				frappe.format(end, "Date"),
			)
		)
	return found, end


def matching_list(start, end, weekdays: set, prefer=()) -> str | None:
	"""The holiday list whose period covers ``start .. end`` and whose week
	offs in it fall on exactly ``weekdays`` (``date.weekday()`` numbers),
	every one of them — "Friday Week Off 2026" for ``{4}``. Its own public
	holidays come with it: a shared list records one landing on its week off
	as a plain week off, so they cannot be compared against the old list's.

	Of several, one of ``prefer`` (the lists already in force) wins, then the
	one named the usual way ("Monday & Tuesday Week Off 2026"), then the one
	most assigned, then the longest period. Lists
	made for one employee by the old week-off change (named after them) are
	passed over."""
	covering = frappe.get_all(
		"Holiday List",
		filters={"from_date": ["<=", start], "to_date": [">=", end]},
		fields=["name", "from_date", "to_date"],
	)
	if not covering:
		return None

	found = {row.name: {} for row in covering}
	for h in frappe.get_all(
		"Holiday",
		filters={"parent": ["in", list(found)], "holiday_date": ["between", [start, end]]},
		fields=["parent", "holiday_date", "weekly_off"],
	):
		found[h.parent][getdate(h.holiday_date)] = bool(h.weekly_off)

	needed, date = [], getdate(start)
	while date <= getdate(end):
		if date.weekday() in weekdays:
			needed.append(date)
		date = add_days(date, 1)

	def reads_right(holidays):
		return all(date in holidays for date in needed) and all(
			date.weekday() in weekdays for date, weekly_off in holidays.items() if weekly_off
		)

	matches = [row for row in covering if reads_right(found[row.name])]
	if not matches:
		return None

	first_words = {row.name.split(" ", 1)[0] for row in matches}
	employees = set(frappe.get_all("Employee", filters={"name": ["in", list(first_words)]}, pluck="name"))
	matches = [row for row in matches if row.name.split(" ", 1)[0] not in employees]
	if not matches:
		return None

	names = [WEEKDAYS[number] for number in sorted(weekdays)]
	label = " & ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else "".join(names)
	conventional = f"{label} Week Off {getdate(start).year}" if names else None
	in_use = dict(
		frappe.db.sql(
			"""select holiday_list, count(*) from `tabHoliday List Assignment`
			where docstatus = 1 and holiday_list in %s group by holiday_list""",
			([row.name for row in matches],),
		)
	)
	matches.sort(
		key=lambda row: (
			row.name not in prefer,
			row.name != conventional,
			-in_use.get(row.name, 0),
			-date_diff(row.to_date, row.from_date),
			row.name,
		)
	)
	return matches[0].name


def _clear_absent_on_week_off(employee: str, holiday_list: str, start, end) -> dict:
	"""Cancel and delete the Absent attendance on the new week off in the
	window — the list may be a shared one running past it: it was
	written before that day was a rest day. One that cannot go — no permission,
	or something linked to it — is left and named."""
	absent = frappe.db.sql(
		"""
		select a.name, a.attendance_date
		from `tabAttendance` a
		join `tabHoliday` h on h.holiday_date = a.attendance_date
		where h.parent = %s and h.weekly_off = 1
			and a.employee = %s and a.docstatus < 2 and a.status = 'Absent'
			and a.attendance_date between %s and %s
		order by a.attendance_date
		""",
		(holiday_list, employee, start, end),
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
