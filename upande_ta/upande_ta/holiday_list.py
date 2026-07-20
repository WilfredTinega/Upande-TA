# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Date-effective holiday-list resolution, backported from HRMS v16.

The source of truth is the ``Holiday List Assignment`` doctype (owned by this
app): a dated record of "employee/company X uses Holiday List Y from date Z".
These helpers resolve, for any date, which Holiday List was in force — so a
change to an employee's week off no longer rewrites history.

Kept entirely inside upande_ta so nothing in hrms/erpnext core is modified.
Payroll is intentionally out of scope; only the assignment chain and the
Monthly Attendance Sheet / Shift auto-attendance consume these.
"""

from datetime import date

import frappe
from frappe.utils import add_days, getdate


def get_holiday_dates_between(
	holiday_list: str,
	start_date: str,
	end_date: str,
	skip_weekly_offs: bool = False,
	as_dict: bool = False,
	select_weekly_off: bool = False,
) -> list:
	Holiday = frappe.qb.DocType("Holiday")
	query = frappe.qb.from_(Holiday).select(Holiday.holiday_date)

	if select_weekly_off:
		query = query.select(Holiday.weekly_off)

	query = query.where(
		(Holiday.parent == holiday_list) & (Holiday.holiday_date.between(start_date, end_date))
	)

	if skip_weekly_offs:
		query = query.where(Holiday.weekly_off == 0)

	if as_dict:
		return query.run(as_dict=True)

	return query.run(pluck=True)


def get_assigned_holiday_list(assigned_to: str, as_on=None, as_dict: bool = False) -> str:
	"""Most recent submitted Holiday List Assignment for ``assigned_to`` (an
	Employee or Company name) effective on ``as_on`` (defaults to today)."""
	as_on = getdate(as_on)
	HLA = frappe.qb.DocType("Holiday List Assignment")
	query = (
		frappe.qb.from_(HLA)
		.select(HLA.holiday_list)
		.where(HLA.assigned_to == assigned_to)
		.where(HLA.from_date <= as_on)
		.where(HLA.docstatus == 1)
		.orderby(HLA.from_date, order=frappe.qb.desc)
		.limit(1)
	)
	if as_dict:
		query = query.select(HLA.from_date)
		holiday_list = query.run(as_dict=True)
		return holiday_list[0] if holiday_list else None

	result = query.run()
	return result[0][0] if result else None


def get_holiday_list_for_employee(
	employee: str, raise_exception: bool = True, as_on: date | str | None = None, as_dict: bool = False
):
	"""Employee's effective Holiday List on ``as_on`` — the employee's own
	assignment first, then their company's assignment. Returns None (no throw)
	when nothing is assigned; callers fall back to ``Employee.holiday_list``."""
	as_on = getdate(as_on)
	holiday_list = get_assigned_holiday_list(employee, as_on, as_dict)
	if not holiday_list:
		company = frappe.db.get_value("Employee", employee, "company")
		if company:
			holiday_list = get_assigned_holiday_list(company, as_on, as_dict)
	return holiday_list


def get_holiday_dates_between_range(
	assigned_to: str,
	start_date: str,
	end_date: str,
	skip_weekly_offs: bool = False,
	select_weekly_offs: bool = False,
	raise_exception_for_holiday_list: bool = True,
) -> list:
	"""Holiday dates in [start_date, end_date], honouring a change of holiday
	list mid-range (splits the range at the new assignment's start)."""
	start_date = getdate(start_date)
	end_date = getdate(end_date)

	from_holiday_list = get_holiday_list_for_employee(assigned_to, as_on=start_date, as_dict=True) or {}
	to_holiday_list = get_holiday_list_for_employee(assigned_to, as_on=end_date, as_dict=True) or {}

	if (
		from_holiday_list
		and to_holiday_list
		and from_holiday_list.holiday_list != to_holiday_list.holiday_list
	):
		return list(
			set(
				get_holiday_dates_between(
					holiday_list=from_holiday_list.holiday_list,
					start_date=start_date,
					end_date=add_days(to_holiday_list.from_date, -1),
					select_weekly_off=select_weekly_offs,
					skip_weekly_offs=skip_weekly_offs,
				)
				+ get_holiday_dates_between(
					holiday_list=to_holiday_list.holiday_list,
					start_date=to_holiday_list.from_date,
					end_date=end_date,
					select_weekly_off=select_weekly_offs,
					skip_weekly_offs=skip_weekly_offs,
				)
			)
		)
	elif holiday_list := from_holiday_list.get("holiday_list", None) or to_holiday_list.get(
		"holiday_list", None
	):
		return get_holiday_dates_between(
			holiday_list=holiday_list,
			start_date=start_date,
			end_date=end_date,
			select_weekly_off=select_weekly_offs,
			skip_weekly_offs=skip_weekly_offs,
		)
	else:
		return []


def get_assigned_holiday_lists_to_employee_and_company(
	assigned_to_list: list,
	start_date,
	end_date,
) -> dict:
	"""Effective holiday-list ranges for many employees/companies in one query.
	{"EMP-001": [{"holiday_list": "HL-1", "from_date": date, "to_date": date}, ...]}"""
	if not assigned_to_list:
		return {}
	return build_holiday_list_map(assigned_to_list, getdate(start_date), getdate(end_date))


def build_holiday_list_map(assigned_to_list: list, start_date: date, end_date: date) -> dict:
	HLA = frappe.qb.DocType("Holiday List Assignment")
	HolidayList = frappe.qb.DocType("Holiday List")

	holiday_list_assignments = (
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
		.where(HolidayList.to_date >= start_date)
		.orderby(HLA.assigned_to)
		.orderby(HLA.from_date)
	).run(as_dict=True)

	holiday_assignment_map = {}
	for assignment in holiday_list_assignments:
		holiday_assignment_map.setdefault(assignment.assigned_to, []).append(assignment)

	return build_effective_date_ranges_for_holiday_assignments(holiday_assignment_map, start_date, end_date)


def build_effective_date_ranges_for_holiday_assignments(
	holiday_assignment_map: dict, start_date: date, end_date: date
) -> dict:
	"""effective_to_date = MIN(Holiday List to_date, next assignment's from_date - 1 day),
	clipped to [start_date, end_date]."""
	result = {}
	for assigned_to, assignments in holiday_assignment_map.items():
		ranges = []
		for idx, assignment in enumerate(assignments):
			hl_to_date = getdate(assignment.holiday_list_to_date)
			next_assignment = assignments[idx + 1] if idx + 1 < len(assignments) else None

			if next_assignment:
				effective_to_date = min(hl_to_date, add_days(next_assignment.from_date, -1))
			else:
				effective_to_date = hl_to_date

			from_date = max(getdate(assignment.from_date), start_date)
			effective_to_date = min(getdate(effective_to_date), end_date)

			if from_date <= effective_to_date:
				ranges.append(
					{
						"holiday_list": assignment.holiday_list,
						"from_date": from_date,
						"to_date": effective_to_date,
					}
				)
		if ranges:
			result[assigned_to] = ranges

	return result


def fill_holiday_list_date_gaps(
	primary_ranges: list, fallback_ranges: list, start_date: date, end_date: date
) -> list:
	"""Fill dates in [start_date, end_date] not covered by ``primary_ranges``
	using ``fallback_ranges`` (company-level, then the static employee list)."""
	if not primary_ranges:
		return fallback_ranges
	if not fallback_ranges:
		return primary_ranges

	result = []
	current = start_date

	for primary in sorted(primary_ranges, key=lambda r: r["from_date"]):
		gap_end = add_days(primary["from_date"], -1)
		if current <= gap_end:
			for fallback in fallback_ranges:
				overlap_start = max(getdate(fallback["from_date"]), current)
				overlap_end = min(getdate(fallback["to_date"]), gap_end)
				if overlap_start <= overlap_end:
					result.append(
						{
							"holiday_list": fallback["holiday_list"],
							"from_date": overlap_start,
							"to_date": overlap_end,
						}
					)
		result.append(primary)
		current = add_days(primary["to_date"], 1)

	if current <= end_date:
		for fallback in fallback_ranges:
			overlap_start = max(getdate(fallback["from_date"]), current)
			overlap_end = min(getdate(fallback["to_date"]), end_date)
			if overlap_start <= overlap_end:
				result.append(
					{
						"holiday_list": fallback["holiday_list"],
						"from_date": overlap_start,
						"to_date": overlap_end,
					}
				)

	return sorted(result, key=lambda r: r["from_date"])
