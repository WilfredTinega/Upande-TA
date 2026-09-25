# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""What a planned Holiday List Assignment change will do to the days around
it, worked out before anything is written.

Holiday Assignment Tool and Bulk Week Off both write Holiday List Assignments,
and HRMS decides an employee's rest days from whichever assignment started
last on or before each date. So a change can:

* **replace** days an existing assignment was covering — back-dated ones
  included, which rewrites rest days already worked or marked;
* leave a **later** assignment in place that takes over again from its own
  date (Bulk Week Off is open-ended and cancels nothing);
* leave days **blank**: no holiday list at all, or a week with no week off,
  which the attendance sheet shows empty and absent marking fills.

The simulation replays the employee's own assignments through HRMS' range
builder, before and after the planned change, and compares the two day by
day. Nothing here writes.
"""

import frappe
from frappe.utils import add_days, getdate, today

#: How far past the change the comparison looks, so a later assignment that
#: takes over, or a gap after the window, is seen.
LOOK_PAST_DAYS = 31

#: Never compare more than this many days per employee.
MAX_SPAN_DAYS = 400

#: Employees reported individually before the list collapses to a count.
MAX_EMPLOYEES = 20


def preview(changes: list) -> dict:
	"""``changes`` is one dict per employee::

	    {"employee", "employee_name", "holiday_list", "from_date",
	     "to_date" (None = open-ended), "restore_to", "cancel_in_window"}

	``cancel_in_window`` is the Holiday Assignment Tool's rule — assignments
	starting inside the window are cancelled and the employee is handed back
	to ``restore_to`` the day after. Without it (Bulk Week Off) the one
	assignment is added and nothing is cancelled.

	Returns ``{"employees": [...], "total": n}`` listing only the employees
	whose change reaches past its own dates or leaves days blank.
	"""
	changes = [c for c in changes if c.get("employee") and c.get("holiday_list") and c.get("from_date")]
	if not changes:
		return {"employees": [], "total": 0}

	existing = _assignments({c["employee"] for c in changes})
	companies = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ["in", [c["employee"] for c in changes]]},
			fields=["name", "company"],
			as_list=True,
		)
	)
	company_rows = _assignments(set(companies.values()) - {None})
	today_date = getdate(today())

	found = []
	for change in changes:
		employee = change["employee"]
		rows = existing.get(employee, [])
		start = getdate(change["from_date"])
		window_end = getdate(change["to_date"]) if change.get("to_date") else None
		after_rows, cancelled = _apply(rows, change, start, window_end)

		last_start = max([getdate(r.from_date) for r in rows] + [window_end or start])
		end = min(add_days(max(last_start, window_end or start), LOOK_PAST_DAYS), add_days(start, MAX_SPAN_DAYS))

		company = company_rows.get(companies.get(employee), [])
		before = _per_day(rows, company, start, end)
		after = _per_day(after_rows, company, start, end)
		offs = _weekly_offs({day for day in list(before.values()) + list(after.values()) if day}, start, end)

		replaced = _replaced(before, after, today_date)
		blank_days = [str(d) for d in sorted(after) if not after[d] and before[d]]
		weeks_without = _weeks_without_week_off(before, after, offs, start, window_end or end)
		later = []
		if not change.get("cancel_in_window"):
			later = [
				{"from_date": str(getdate(r.from_date)), "holiday_list": r.holiday_list}
				for r in rows
				if getdate(r.from_date) > start
			]

		if replaced or blank_days or weeks_without or later:
			found.append(
				{
					"employee": employee,
					"employee_name": change.get("employee_name") or employee,
					"replaced": replaced,
					"backdated": any(r["backdated"] for r in replaced),
					"later": later,
					"blank_days": blank_days,
					"weeks_without_week_off": weeks_without,
					"cancelled": cancelled,
				}
			)

	return {"employees": found[:MAX_EMPLOYEES], "total": len(found)}


def _assignments(assigned_to) -> dict:
	"""Submitted assignments per employee or company, with their list's end
	date — what HRMS' range builder reads."""
	if not assigned_to:
		return {}
	rows = frappe.db.sql(
		"""
		select hla.name, hla.assigned_to, hla.holiday_list, hla.from_date,
			hl.to_date as holiday_list_to_date
		from `tabHoliday List Assignment` hla
		join `tabHoliday List` hl on hl.name = hla.holiday_list
		where hla.docstatus = 1 and hla.assigned_to in %s
		order by hla.from_date
		""",
		(tuple(assigned_to),),
		as_dict=True,
	)
	grouped = {}
	for row in rows:
		grouped.setdefault(row.assigned_to, []).append(row)
	return grouped


def _apply(rows, change, start, window_end):
	"""The employee's assignments as they would be after the change, and the
	names it would cancel."""
	from upande_ta.upande_ta.holiday_segments import plan_segments

	cancelled = []
	kept = list(rows)
	if change.get("cancel_in_window"):
		last = window_end or start
		cancelled = [r.name for r in rows if start <= getdate(r.from_date) <= last]
		kept = [r for r in rows if r.name not in cancelled]
		boundaries = plan_segments(change["holiday_list"], start, window_end, [], change.get("restore_to"))
	else:
		boundaries = [(start, change["holiday_list"])]

	starts = {getdate(r.from_date) for r in kept}
	list_ends = dict(
		frappe.get_all(
			"Holiday List",
			filters={"name": ["in", [b[1] for b in boundaries]]},
			fields=["name", "to_date"],
			as_list=True,
		)
	)
	added = []
	for date, holiday_list in boundaries:
		date = getdate(date)
		if date in starts:
			# an assignment already starts that day and HRMS refuses a second:
			# the tool skips its restore there, Bulk Week Off keeps the old one
			continue
		added.append(
			frappe._dict(
				name=None, holiday_list=holiday_list, from_date=date, holiday_list_to_date=list_ends.get(holiday_list)
			)
		)
	after = sorted(kept + added, key=lambda r: getdate(r.from_date))
	return after, cancelled


def _per_day(rows, company_rows, start, end) -> dict:
	"""``{date: holiday list or None}`` as HRMS resolves it."""
	from hrms.utils.holiday_list import (
		build_effective_date_ranges_for_holiday_assignments,
		fill_employee_holiday_list_date_gaps_with_company_holiday_list,
	)

	own = build_effective_date_ranges_for_holiday_assignments({"e": rows}, start, end).get("e", [])
	fallback = build_effective_date_ranges_for_holiday_assignments({"c": company_rows}, start, end).get("c", [])
	ranges = fill_employee_holiday_list_date_gaps_with_company_holiday_list(own, fallback, start, end)

	days, date = {}, start
	while date <= end:
		days[date] = next(
			(r["holiday_list"] for r in ranges if getdate(r["from_date"]) <= date <= getdate(r["to_date"])), None
		)
		date = add_days(date, 1)
	return days


def _weekly_offs(lists, start, end) -> set:
	if not lists:
		return set()
	return {
		(h.parent, getdate(h.holiday_date))
		for h in frappe.get_all(
			"Holiday",
			filters={"parent": ["in", list(lists)], "weekly_off": 1, "holiday_date": ["between", [start, end]]},
			fields=["parent", "holiday_date"],
		)
	}


def _replaced(before, after, today_date) -> list:
	"""Runs of days whose list changes from one that was there, collapsed."""
	runs = []
	for date in sorted(before):
		was, will = before[date], after[date]
		if not was or was == will:
			continue
		if runs and runs[-1]["holiday_list"] == was and runs[-1]["to"] == add_days(date, -1) and runs[-1]["new"] == will:
			runs[-1]["to"] = date
		else:
			runs.append({"holiday_list": was, "new": will, "from": date, "to": date})
	return [
		{
			"holiday_list": r["holiday_list"],
			"new": r["new"],
			"from_date": str(r["from"]),
			"to_date": str(r["to"]),
			"backdated": r["from"] < today_date,
		}
		for r in runs
	]


def _weeks_without_week_off(before, after, offs, start, end) -> list:
	"""Mondays of whole weeks inside ``start .. end`` that had a week off and
	will have none."""
	weeks = []
	monday = add_days(start, (7 - start.weekday()) % 7)
	while add_days(monday, 6) <= end:
		week = [add_days(monday, i) for i in range(7)]
		had = any((before.get(d), d) in offs for d in week)
		has = any((after.get(d), d) in offs for d in week)
		if had and not has:
			weeks.append(str(monday))
		monday = add_days(monday, 7)
	return weeks
