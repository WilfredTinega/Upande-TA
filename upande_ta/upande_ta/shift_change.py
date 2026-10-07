# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Change one employee's shift from a date, clicked on the Monthly Attendance
Sheet.

The shift chosen owns every day from that date to the end date given. An
assignment that started earlier is end-dated the day before; one that starts
inside the window is cancelled. Whatever ran past the window carries on the
day after it, as a new assignment for the rest of its period, so the employee
goes back to the shift they would otherwise have been on.

The old assignment is end-dated and, once that end date has passed, set
Inactive, so only one shift marks the employee's attendance. One whose end is
still ahead stays Active until then: HRMS skips an Inactive assignment for
every date it covers, which would leave those days with no shift at all.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, get_datetime, get_link_to_form, getdate, today

#: A window longer than this is a slip of the date picker, not a shift change.
MAX_WINDOW_DAYS = 366

#: Days of schedule the sheet's popup shows from the date clicked.
SCHEDULE_DAYS = 30

CHECKIN_SHIFT_FIELDS = (
	"shift",
	"offshift",
	"shift_actual_start",
	"shift_actual_end",
	"shift_start",
	"shift_end",
	"overtime_type",
)


@frappe.whitelist()
def get_shift_schedule(employee: str, from_date: str | None = None, days: int = SCHEDULE_DAYS) -> dict:
	"""The employee's shift for each day from ``from_date`` (today by
	default), at least ``days`` long and through the last scheduled entry,
	merged into runs of the same shift. ``next_from`` is where the next entry
	starts: the day after that chain of entries ends, or today when the shift
	in force is open-ended."""
	frappe.has_permission("Employee", doc=employee, throw=True)
	current = current_assignment(employee)
	next_from = next_start(employee)
	start = getdate(from_date) if from_date else getdate(today())
	end = max(add_days(start, max(cint(days), 1) - 1), add_days(next_from, -1))
	in_force = shifts_in_force(employee, start, end)

	ranges = []
	for date in sorted(in_force):
		day = in_force[date]
		last = ranges[-1] if ranges else None
		if last and last["shift_type"] == day["shift_type"] and last["assignment"] == day["assignment"]:
			last["to_date"] = date
		else:
			ranges.append(dict(day, from_date=date, to_date=date))
	periods = {
		row.name: row
		for row in frappe.get_all(
			"Shift Assignment",
			filters={"name": ["in", [r["assignment"] for r in ranges if r["assignment"]] or [""]]},
			fields=["name", "start_date", "end_date"],
		)
	}
	for r in ranges:
		period = periods.get(r["assignment"])
		r["start_date"] = period.start_date if period else None
		r["end_date"] = period.end_date if period else None

	return {
		"current": current,
		"next_from": next_from,
		"from_date": start,
		"to_date": end,
		"shift_type": in_force[start]["shift_type"],
		"ranges": ranges,
	}


@frappe.whitelist(methods=["POST"])
def change_shift(
	employee: str,
	from_date: str,
	to_date: str,
	shift_type: str | None = None,
	rotation=None,
	weeks_per_shift: int = 1,
) -> dict:
	"""``shift_type`` is the employee's shift from ``from_date`` to
	``to_date``. With ``rotation`` — more shifts, in order — they take turns
	with it by calendar week (Monday to Sunday), ``weeks_per_shift`` weeks
	each, the first turn running to the end of the week ``from_date`` is in."""
	start, end = _window(employee, from_date, to_date)
	shifts = [name for name in (shift_type, *_shift_list(rotation)) if name]
	if not shifts:
		frappe.throw(_("Pick a Shift Type."))
	for name in shifts:
		if not frappe.db.exists("Shift Type", name):
			frappe.throw(_("Shift Type {0} not found.").format(frappe.bold(name)))

	blocks = _blocks(start, end, shifts, max(cint(weeks_per_shift), 1))
	in_force = shifts_in_force(employee, start, end)
	wanted = {}
	for block_start, block_end, shift in blocks:
		date = block_start
		while date <= block_end:
			wanted[date] = shift
			date = add_days(date, 1)
	if all(day["assignment"] and day["shift_type"] == wanted[date] for date, day in in_force.items()):
		frappe.throw(
			_("{0} is already their shift from {1} to {2}.").format(
				frappe.bold(" / ".join(dict.fromkeys(shifts))),
				frappe.format(start, "Date"),
				frappe.format(end, "Date"),
			)
		)

	after = add_days(end, 1)
	trimmed, cancelled, carried = [], [], []
	for doc in _overlapping(employee, start, end):
		runs_past = not doc.end_date or getdate(doc.end_date) > end
		carry = _carry_over(doc, after) if runs_past else None

		if getdate(doc.start_date) < start:
			doc.end_date = add_days(start, -1)
			if getdate(doc.end_date) < getdate(today()):
				doc.status = "Inactive"
			doc.flags.ignore_permissions = True
			doc.save()
			trimmed.append(doc.name)
		else:
			doc.flags.ignore_permissions = True
			doc.flags.ignore_links = True
			_cancel(doc)
			cancelled.append(doc.name)

		if carry:
			carried.append(carry)

	created = [_submit(_new_assignment(employee, shift, a, b)).name for a, b, shift in blocks]
	restored_to = None
	for carry in carried:
		created.append(_submit(carry).name)
		restored_to = restored_to or carry.shift_type

	result = {
		"shift_type": shifts[0],
		"blocks": [{"from_date": a, "to_date": b, "shift_type": shift} for a, b, shift in blocks],
		"restored_to": restored_to,
		"created": [get_link_to_form("Shift Assignment", name) for name in created],
		"trimmed": trimmed,
		"cancelled": cancelled,
		"checkins_moved": _restamp_checkins(employee, start, end),
		"absent_removed": [],
		"attendance_on_other_shift": [],
	}
	for a, b, shift in blocks:
		for key, dates in _clear_absent(employee, a, b, shift).items():
			result[key].extend(dates)
	return result


def _shift_list(value) -> list:
	"""Shift Type names, in order, from what the desk sends: a JSON array of
	names or of ``{shift_type}`` rows."""
	if not value:
		return []
	if isinstance(value, str):
		value = frappe.parse_json(value)
	names = [row.get("shift_type") if isinstance(row, dict) else row for row in value]
	return [name for name in names if name]


def _blocks(start, end, shifts, weeks) -> list:
	"""``(from, to, shift)`` turns covering ``start .. end``: the shifts in
	order, ``weeks`` calendar weeks each, the first ending on the Sunday that
	closes ``start``'s week. Turns of the same shift back to back are one."""
	blocks, block_start, turn = [], start, 0
	while block_start <= end:
		block_end = min(add_days(block_start, 6 - block_start.weekday() + 7 * (weeks - 1)), end)
		shift = shifts[turn % len(shifts)]
		if blocks and blocks[-1][2] == shift:
			blocks[-1] = (blocks[-1][0], block_end, shift)
		else:
			blocks.append((block_start, block_end, shift))
		block_start, turn = add_days(block_end, 1), turn + 1
	return blocks


def current_assignment(employee) -> dict | None:
	"""The assignment deciding the employee's shift today."""
	date = getdate(today())
	day = shifts_in_force(employee, date, date)[date]
	if not day["assignment"]:
		return None
	return frappe.db.get_value(
		"Shift Assignment", day["assignment"], ["name", "shift_type", "start_date", "end_date"], as_dict=True
	)


def next_start(employee):
	"""Where the next entry starts by default: follow the assignments with an
	end date from today, one after another, and start the day after the last;
	today when the shift in force is open-ended or there is none."""
	date = getdate(today())
	rows = _in_force_rows(employee, date, add_days(date, 10 * MAX_WINDOW_DAYS))
	for _turn in range(len(rows) + 1):
		covering = [
			row
			for row in rows
			if getdate(row.start_date) <= date and (not row.end_date or date <= getdate(row.end_date))
		]
		if not covering:
			break
		row = max(covering, key=lambda r: (r.status == "Active", getdate(r.start_date), r.creation))
		if not row.end_date:
			break
		date = add_days(row.end_date, 1)
	return date


def shift_change_disabled() -> bool:
	from upande_ta.upande_ta.overrides.monthly_attendance_sheet import shift_change_disabled

	return shift_change_disabled()


def _window(employee, from_date, to_date):
	if shift_change_disabled():
		frappe.throw(_("Shift Change is disabled in Biometric Setting."))
	frappe.has_permission("Shift Assignment", "create", throw=True)
	frappe.has_permission("Shift Assignment", "submit", throw=True)
	frappe.has_permission("Employee", doc=employee, throw=True)

	start, end = getdate(from_date), getdate(to_date)
	if end < start:
		frappe.throw(_("End Date cannot be before {0}.").format(frappe.format(start, "Date")))
	if date_diff(end, start) >= MAX_WINDOW_DAYS:
		frappe.throw(_("A shift change cannot run longer than {0} days.").format(MAX_WINDOW_DAYS))
	return start, end


def _in_force_rows(employee, start, end) -> list:
	"""Submitted assignments touching ``start .. end`` that still decide a
	shift: Active ones, and Inactive ones only because HRMS' daily job retired
	them after their end date passed."""
	rows = frappe.get_all(
		"Shift Assignment",
		filters={"employee": employee, "docstatus": 1, "start_date": ["<=", end]},
		or_filters=[["end_date", ">=", start], ["end_date", "is", "not set"]],
		fields=["name", "shift_type", "start_date", "end_date", "status", "creation"],
		order_by="start_date asc, creation asc",
	)
	expired_before = getdate(today())
	return [
		row
		for row in rows
		if row.status == "Active" or (row.end_date and getdate(row.end_date) < expired_before)
	]


def shifts_in_force(employee, start, end) -> dict:
	"""``{date: {shift_type, assignment}}`` for every day of ``start .. end``.
	Where two assignments cover a day the Active, later-starting, newer one
	wins; a day with none falls back to the employee's default shift."""
	rows = _in_force_rows(employee, start, end)
	default = frappe.db.get_value("Employee", employee, "default_shift")

	days, date = {}, getdate(start)
	while date <= end:
		covering = [
			row
			for row in rows
			if getdate(row.start_date) <= date and (not row.end_date or date <= getdate(row.end_date))
		]
		if covering:
			row = max(covering, key=lambda r: (r.status == "Active", getdate(r.start_date), r.creation))
			days[date] = {"shift_type": row.shift_type, "assignment": row.name}
		else:
			days[date] = {"shift_type": default, "assignment": None}
		date = add_days(date, 1)
	return days


def _overlapping(employee, start, end) -> list:
	return [frappe.get_doc("Shift Assignment", row.name) for row in _in_force_rows(employee, start, end)]


def _carry_over(doc, after):
	"""The part of ``doc`` past the window, as a new assignment."""
	carry = frappe.copy_doc(doc)
	carry.start_date = max(after, getdate(doc.start_date))
	carry.end_date = doc.end_date
	carry.status = "Active"
	carry.amended_from = None
	return carry


def _new_assignment(employee, shift_type, start, end):
	return frappe.get_doc(
		{
			"doctype": "Shift Assignment",
			"employee": employee,
			"company": frappe.db.get_value("Employee", employee, "company"),
			"shift_type": shift_type,
			"start_date": start,
			"end_date": end,
			"status": "Active",
		}
	)


def _submit(doc):
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()
	return doc


def _cancel(doc):
	"""Cancel ``doc``. HRMS refuses while check-ins or attendance carry its
	shift on its dates; those stay as the record of what happened, so the
	assignment is cancelled without that check."""
	try:
		frappe.db.savepoint("shift_change_cancel")
		doc.cancel()
	except frappe.ValidationError:
		frappe.db.rollback(save_point="shift_change_cancel")
		frappe.clear_last_message()
		doc.reload()
		doc.validate_employee_checkin = doc.validate_attendance = lambda: None
		doc.flags.ignore_permissions = True
		doc.cancel()


def _restamp_checkins(employee, start, end) -> int:
	"""Re-resolve the shift of check-ins in the window that no attendance has
	taken yet, so the new shift's attendance run picks them up."""
	meta = frappe.get_meta("Employee Checkin")
	fields = [f for f in CHECKIN_SHIFT_FIELDS if meta.has_field(f)]
	names = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [get_datetime(start), get_datetime(f"{add_days(end, 1)} 23:59:59")]],
			"attendance": ["is", "not set"],
		},
		pluck="name",
	)
	moved = 0
	for name in names:
		doc = frappe.get_doc("Employee Checkin", name)
		before = doc.shift
		try:
			doc.fetch_shift()
		except frappe.ValidationError:
			frappe.clear_last_message()
			continue
		frappe.db.set_value("Employee Checkin", name, {f: doc.get(f) for f in fields}, update_modified=False)
		moved += doc.shift != before
	return moved


def _clear_absent(employee, start, end, shift_type) -> dict:
	"""Cancel and delete Absent attendance in the window that another shift
	marked: it was written against the shift the employee no longer works.
	One that cannot go is left and named, as is any other attendance still
	under a different shift."""
	rows = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"docstatus": ["<", 2],
			"attendance_date": ["between", [start, end]],
		},
		fields=["name", "attendance_date", "status", "shift"],
		order_by="attendance_date asc",
	)
	removed, kept = [], []
	for row in rows:
		if row.shift == shift_type:
			continue
		if row.status != "Absent":
			kept.append(str(row.attendance_date))
			continue
		try:
			frappe.db.savepoint("shift_change_absent")
			attendance = frappe.get_doc("Attendance", row.name)
			if attendance.docstatus == 1:
				attendance.cancel()
			frappe.delete_doc("Attendance", row.name, delete_permanently=True)
			removed.append(str(row.attendance_date))
		except Exception:
			frappe.db.rollback(save_point="shift_change_absent")
			frappe.clear_last_message()
			kept.append(str(row.attendance_date))
	return {"absent_removed": removed, "attendance_on_other_shift": kept}
