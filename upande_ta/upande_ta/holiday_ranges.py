# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Which holiday list is in force on each date, for many employees at once.

HRMS answers this one date at a time (``get_holiday_list_for_employee``); the
bulk helpers that answer it for a range exist only on some HRMS branches and
behave differently between them (``develop`` ends an assignment where its
holiday list's own period ends, ``version-16`` runs it to the next
assignment), and one of them is missing from ``version-16`` altogether. This
module does the same job itself, so the app gives the same answer on every
HRMS version it runs on. The functions keep HRMS ``develop``'s names and
signatures.

The rule, as HRMS applies it: an employee's submitted Holiday List Assignment
with the latest ``from_date`` on or before a date is in force that day, until
the next one starts or its holiday list's own period ends, whichever is first;
days no employee assignment covers take the company's assignment.
"""

from datetime import date

import frappe
from frappe.utils import add_days, getdate


def get_assigned_holiday_lists_to_employee_and_company(
	assigned_to_list: list[str], start_date: date | str, end_date: date | str
) -> dict[str, list[dict]]:
	"""``{assigned_to: [{holiday_list, from_date, to_date}, ...]}`` for employees
	and/or companies, clipped to ``start_date .. end_date``, in one query."""
	assigned_to_list = [a for a in (assigned_to_list or []) if a]
	if not assigned_to_list:
		return {}

	start_date, end_date = getdate(start_date), getdate(end_date)
	HLA = frappe.qb.DocType("Holiday List Assignment")
	HolidayList = frappe.qb.DocType("Holiday List")
	rows = (
		frappe.qb.from_(HLA)
		.join(HolidayList)
		.on(HLA.holiday_list == HolidayList.name)
		.select(
			HLA.assigned_to,
			HLA.holiday_list,
			HLA.from_date,
			HolidayList.to_date.as_("holiday_list_to_date"),
		)
		.where(HLA.assigned_to.isin(assigned_to_list))
		.where(HLA.docstatus == 1)
		.where(HLA.from_date <= end_date)
		.orderby(HLA.assigned_to)
		.orderby(HLA.from_date)
		.orderby(HLA.creation)
	).run(as_dict=True)

	grouped = {}
	for row in rows:
		grouped.setdefault(row.assigned_to, []).append(row)
	return build_effective_date_ranges_for_holiday_assignments(grouped, start_date, end_date)


def build_effective_date_ranges_for_holiday_assignments(
	holiday_assignment_map: dict[str, list[dict]], start_date: date, end_date: date
) -> dict[str, list[dict]]:
	"""Each assignment runs from its ``from_date`` to the day before the next
	one starts, or to its holiday list's ``to_date`` when that is earlier (read
	from ``holiday_list_to_date`` when the rows carry it), clipped to the range.
	Rows must be in ``from_date`` order per key."""
	start_date, end_date = getdate(start_date), getdate(end_date)
	result = {}
	for assigned_to, assignments in holiday_assignment_map.items():
		ranges = []
		for idx, assignment in enumerate(assignments):
			get = (
				assignment.get
				if isinstance(assignment, dict)
				else lambda k, a=assignment: getattr(a, k, None)
			)
			following = assignments[idx + 1] if idx + 1 < len(assignments) else None

			effective_to = end_date
			if following is not None:
				next_from = following.get("from_date") if isinstance(following, dict) else following.from_date
				effective_to = add_days(getdate(next_from), -1)
			if get("holiday_list_to_date"):
				effective_to = min(getdate(effective_to), getdate(get("holiday_list_to_date")))

			from_date = max(getdate(get("from_date")), start_date)
			effective_to = min(getdate(effective_to), end_date)
			if from_date <= effective_to:
				ranges.append(
					{"holiday_list": get("holiday_list"), "from_date": from_date, "to_date": effective_to}
				)
		if ranges:
			result[assigned_to] = ranges
	return result


def fill_employee_holiday_list_date_gaps_with_company_holiday_list(
	primary_ranges: list[dict], fallback_ranges: list[dict], start_date: date, end_date: date
) -> list[dict]:
	"""The employee's ranges, with every day they leave uncovered in
	``start_date .. end_date`` filled from the company's ranges."""
	if not primary_ranges:
		return list(fallback_ranges or [])
	if not fallback_ranges:
		return list(primary_ranges)

	start_date, end_date = getdate(start_date), getdate(end_date)
	result, current = [], start_date

	def fill(until):
		for fallback in fallback_ranges:
			lo = max(getdate(fallback["from_date"]), current)
			hi = min(getdate(fallback["to_date"]), until)
			if lo <= hi:
				result.append({"holiday_list": fallback["holiday_list"], "from_date": lo, "to_date": hi})

	for primary in sorted(primary_ranges, key=lambda r: getdate(r["from_date"])):
		gap_end = add_days(getdate(primary["from_date"]), -1)
		if current <= gap_end:
			fill(gap_end)
		result.append(primary)
		current = add_days(getdate(primary["to_date"]), 1)
	if current <= end_date:
		fill(end_date)

	return sorted(result, key=lambda r: getdate(r["from_date"]))
