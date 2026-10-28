# Copyright (c) 2026, Upande LTD and contributors

import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, getdate

from upande_ta.upande_ta.holiday_list import (
	fill_holiday_list_date_gaps,
	get_assigned_holiday_lists_to_employee_and_company,
)
from upande_ta.upande_ta.overrides.leave_type import LEAVE_TYPE_ABBR_FIELD

_DAY_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")

_LEAVE_SEP = "|"

#: The Group By option added for sites that keep employees' greenhouses on
#: the Employee (upande_agriculture's Greenhouses table).
GREENHOUSE = "Greenhouse"


def greenhouse_table():
	"""``(child doctype, link target)`` of Employee's Greenhouses table, or
	None where the site has no such table."""
	for df in frappe.get_meta("Employee").get_table_fields():
		child = frappe.get_meta(df.options)
		field = child.get_field("greenhouse")
		if field and field.fieldtype == "Link":
			return df.options, field.options
	return None


def group_by_greenhouse(employees: dict) -> tuple[dict, list]:
	"""``{greenhouse: {employee: details}}`` and the greenhouses in order, from
	a flat ``{employee: details}``. An employee in two greenhouses is listed
	under both; one in none is left off, as HRMS leaves off an employee with
	no Branch when grouping by Branch."""
	table = greenhouse_table()
	grouped = {}
	if table and employees:
		for row in frappe.get_all(
			table[0],
			filters={"parenttype": "Employee", "parent": ["in", list(employees)]},
			fields=["parent", "greenhouse"],
			order_by="greenhouse, parent",
		):
			if row.greenhouse:
				grouped.setdefault(row.greenhouse, frappe._dict())[row.parent] = employees[row.parent]
	return grouped, sorted(grouped)


def get_leave_abbr(leave_type: str) -> str:
	if not leave_type:
		return "L"

	try:
		stored = frappe.get_cached_value(
			"Leave Type", leave_type, LEAVE_TYPE_ABBR_FIELD
		)
	except Exception:
		stored = None

	return str(stored).strip() if stored and str(stored).strip() else "L"


def get_attendance_records(filters):
	Attendance = frappe.qb.DocType("Attendance")
	attendance_date_condition = _hrms.get_date_condition(Attendance.attendance_date, filters)
	status = (
		frappe.qb.terms.Case()
		.when(
			((Attendance.status == "Half Day") & (Attendance.half_day_status == "Present")),
			"Half Day/Other Half Present",
		)
		.when(
			((Attendance.status == "Half Day") & (Attendance.half_day_status == "Absent")),
			"Half Day/Other Half Absent",
		)
		.else_(Attendance.status)
	)
	query = (
		frappe.qb.from_(Attendance)
		.select(
			Attendance.employee,
			Attendance.attendance_date,
			(status).as_("status"),
			Attendance.shift,
			Attendance.leave_type,
		)
		.where(
			(Attendance.docstatus == 1)
			& (Attendance.company.isin(filters.companies))
			& (attendance_date_condition)
		)
	)

	if filters.employee:
		query = query.where(Attendance.employee == filters.employee)
	query = query.orderby(Attendance.employee, Attendance.attendance_date)

	return query.run(as_dict=1)


def build_shift_resolver(employees, filters):
	"""Return resolve(employee, attendance_date) -> assigned shift name.

	Rows in the detailed view are grouped by this value, so an employee is
	grouped under the shift they were *assigned* (Shift Assignment, then the
	Employee's default shift) rather than whatever shift each individual
	Attendance record happened to be stamped with. This collapses the split
	rows produced by different attendance sources (manual Mark Attendance,
	Attendance Request, auto-marked Weekly Off, auto leave from Leave
	Application) into a single row per assigned shift.
	"""
	employees = list(employees)
	if not employees:
		return lambda employee, attendance_date: ""

	start_date, end_date = _hrms.get_date_range_from_filters(filters)

	ShiftAssignment = frappe.qb.DocType("Shift Assignment")
	rows = (
		frappe.qb.from_(ShiftAssignment)
		.select(
			ShiftAssignment.employee,
			ShiftAssignment.shift_type,
			ShiftAssignment.start_date,
			ShiftAssignment.end_date,
		)
		.where(
			# NOT filtered on status == "Active": an assignment goes "Inactive" the
			# moment it is superseded, but its date range is still the authoritative
			# shift for those *historical* dates. Filtering to Active-only dropped the
			# old shift, so days before a mid-period shift change resolved to "" and
			# rendered as a mystery blank-Shift row. docstatus == 1 still excludes
			# cancelled assignments.
			(ShiftAssignment.docstatus == 1)
			& (ShiftAssignment.employee.isin(employees))
			& (ShiftAssignment.start_date <= end_date)
			& (ShiftAssignment.end_date.isnull() | (ShiftAssignment.end_date >= start_date))
		)
		.orderby(ShiftAssignment.start_date, order=frappe.qb.desc)
	).run(as_dict=1)

	assignments = {}
	for r in rows:
		assignments.setdefault(r.employee, []).append(r)

	default_shifts = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ("in", employees)},
			fields=["name", "default_shift"],
			as_list=1,
		)
	)

	def resolve(employee, attendance_date):
		# assignments are ordered by start_date DESC, so the first one whose range
		# contains the day is the most recently effective — this wins when an older
		# assignment was left open-ended (end_date NULL) and overlaps a newer one.
		day = getdate(attendance_date)
		for a in assignments.get(employee, []):
			if a.start_date and getdate(a.start_date) > day:
				continue
			if a.end_date and getdate(a.end_date) < day:
				continue
			if a.shift_type:
				return a.shift_type
		return default_shifts.get(employee) or ""

	return resolve


def current_shift_map(employees, filters) -> dict:
	"""{employee: shift} the row is labelled with: the shift they are on now --
	the assignment covering today, or the period's last day when that is
	already past (its first day when the period is still ahead). With nothing
	covering that day, the latest assignment that has started wins, then any in
	the period. Employees with no assignment in the period are left out.

	An Inactive assignment counts only once its end date has passed (HRMS'
	expiry job); one switched off by hand while still running does not.
	"""
	employees = list(employees)
	if not employees:
		return {}
	period_start, period_end = (getdate(d) for d in _hrms.get_date_range_from_filters(filters))
	today = getdate()
	ref_date = min(max(today, period_start), period_end)

	ShiftAssignment = frappe.qb.DocType("Shift Assignment")
	rows = (
		frappe.qb.from_(ShiftAssignment)
		.select(
			ShiftAssignment.employee,
			ShiftAssignment.shift_type,
			ShiftAssignment.start_date,
			ShiftAssignment.end_date,
			ShiftAssignment.status,
			ShiftAssignment.creation,
		)
		.where(
			(ShiftAssignment.docstatus == 1)
			& (ShiftAssignment.employee.isin(employees))
			& (ShiftAssignment.start_date <= period_end)
			& ((ShiftAssignment.end_date.isnull()) | (ShiftAssignment.end_date >= period_start))
		)
	).run(as_dict=True)

	# employee -> (rank, start, creation, shift); rank 2 covers ref_date,
	# 1 started by it, 0 only starts after it
	best = {}
	for r in rows:
		if not r.shift_type:
			continue
		end = getdate(r.end_date) if r.end_date else None
		if r.status != "Active" and not (end and end < today):
			continue
		start = getdate(r.start_date)
		if start <= ref_date and (end is None or end >= ref_date):
			rank = 2
		elif start <= ref_date:
			rank = 1
		else:
			rank = 0
		key = (rank, start, r.creation, r.shift_type)
		if r.employee not in best or key[:3] > best[r.employee][:3]:
			best[r.employee] = key
	return {employee: key[3] for employee, key in best.items()}


def get_attendance_map(filters):
	attendance_list = get_attendance_records(filters)

	employees = {d.employee for d in attendance_list}
	resolve_shift = build_shift_resolver(employees, filters)
	# read by get_attendance_status_for_detailed_view, called per employee
	frappe.local.upande_ta_current_shift = current_shift_map(employees, filters)

	attendance_map = {}

	for d in attendance_list:
		shift = resolve_shift(d.employee, d.attendance_date)
		day_map = attendance_map.setdefault(d.employee, {}).setdefault(shift, {})

		if d.status == "On Leave":
			value = "On Leave"
			if d.leave_type:
				value = f"On Leave{_LEAVE_SEP}{d.leave_type}"
			day_map[d.attendance_date] = value
		else:
			day_map[d.attendance_date] = d.status

	return attendance_map


def get_attendance_status_for_detailed_view(employee, filters, employee_attendance, holidays):
	"""One row per employee for the whole period, even when they alternated shifts.

	The attendance map buckets an employee's days by resolved shift; here we collapse
	those buckets into a single per-date status map (each date resolves to exactly one
	shift, so the merge never collides) and render it as a SINGLE row. The Shift column
	is labelled with the shift the employee is on now (current_shift_map), so a shift
	changed from the sheet shows on the row straight away; without an assignment in
	the period, the shift they worked the most days in, ties broken by shift name.
	"""
	total_days = _hrms.get_dates_in_period(filters)

	shift_attendance = employee_attendance or {"": {}}

	merged = {}
	for status_dict in shift_attendance.values():
		merged.update(status_dict)

	label_shift = (getattr(frappe.local, "upande_ta_current_shift", None) or {}).get(employee) or max(
		shift_attendance.items(),
		key=lambda item: (len(item[1]), item[0] or ""),
	)[0]

	row = {"shift": label_shift}
	for d in total_days:
		d = getdate(d)
		status = merged.get(d)

		if status is None and holidays:
			status = _hrms.get_holiday_status(d, holidays)

		if status and status.startswith("On Leave" + _LEAVE_SEP):
			leave_type = status.split(_LEAVE_SEP, 1)[1]
			abbr = get_leave_abbr(leave_type)
		else:
			abbr = _hrms.status_map.get(status, "")

		row[d.strftime("%d-%m-%Y")] = abbr

	return [row]


def get_chart_data(attendance_map, filters):
	days = _hrms.get_columns_for_days(filters)
	labels = []
	absent = []
	present = []
	leave = []

	for day in days:
		labels.append(day["label"])
		total_absent_on_day = total_leaves_on_day = total_present_on_day = 0

		for __, attendance_dict in attendance_map.items():
			for __, attendance in attendance_dict.items():
				attendance_on_day = attendance.get(getdate(day["fieldname"], parse_day_first=True))

				if attendance_on_day and str(attendance_on_day).startswith("On Leave"):
					total_leaves_on_day += 1
					break
				elif attendance_on_day == "Absent":
					total_absent_on_day += 1
				elif attendance_on_day in ["Present", "Work From Home"]:
					total_present_on_day += 1
				elif attendance_on_day == "Half Day":
					total_present_on_day += 0.5
					total_leaves_on_day += 0.5

		absent.append(total_absent_on_day)
		present.append(total_present_on_day)
		leave.append(total_leaves_on_day)

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Absent"), "values": absent},
				{"name": _("Present"), "values": present},
				{"name": _("Leave"), "values": leave},
			],
		},
		"type": "line",
		"colors": ["red", "green", "blue"],
	}


def get_message():
	def chip(color, label):
		return (
			f"<span style='border-left: 2px solid {color}; padding-right: 12px; "
			f"padding-left: 5px; margin-right: 3px;'>{label}</span>"
		)

	message = ""

	base = [
		("green", _("Present"), "P"),
		("red", _("Absent"), "A"),
		("orange", _("Half Day/Other Half Absent"), "HD/A"),
		("#914EE3", _("Half Day/Other Half Present"), "HD/P"),
		("green", _("Work From Home"), "WFH"),
		("#878787", _("Holiday"), "H"),
		("#878787", _("Weekly Off"), "WO"),
	]
	for color, label, abbr in base:
		message += chip(color, f"{label} - {abbr}")

	leave_types = frappe.db.get_all("Leave Type", pluck="name")
	leave_chips = sorted(((get_leave_abbr(lt), lt) for lt in leave_types), key=lambda x: x[0])
	for abbr, label in leave_chips:
		message += chip("#318AD8", f"{label} - {abbr}")

	return message


def _classify(abbr):
	if not abbr:
		return None
	a = str(abbr).strip()
	if a in ("P", "WFH"):
		return "present"
	if a == "A":
		return "absent"
	if a in ("HD/P", "HD/A"):
		return "half_day"
	if a == "H":
		return "holiday"
	if a == "WO":
		return "weekly_off"
	return "on_leave"


_PRECEDENCE = {
	"present": 6,
	"half_day": 5,
	"absent": 4,
	"on_leave": 3,
	"holiday": 2,
	"weekly_off": 1,
}

_SUMMARY_ROWS = [
	("present", _("Present")),
	("absent", _("Absent")),
	("on_leave", _("On Leave")),
	("half_day", _("Half Day")),
	("holiday", _("Holiday")),
	("weekly_off", _("Weekly Off")),
]


def build_summary_rows(data, columns):
	day_fields = [
		c["fieldname"]
		for c in columns
		if c.get("fieldtype") == "Data" and _DAY_RE.match(str(c.get("fieldname", "")))
	]
	if not day_fields:
		return []

	# Put the label in the first column so the client can render it spanning the
	# three empty leading columns (Employee / Employee Name / Shift), left-aligned,
	# instead of being truncated inside the narrow Employee Name column.
	label_field = "employee"

	per_day_emp = {d: {} for d in day_fields}

	for row in data:
		emp = row.get("employee")
		if not emp:
			continue
		for d in day_fields:
			cat = _classify(row.get(d))
			if cat is None:
				continue
			cur = per_day_emp[d].get(emp)
			if cur is None or _PRECEDENCE.get(cat, 0) > _PRECEDENCE.get(cur, 0):
				per_day_emp[d][emp] = cat

	counts = {key: {d: 0 for d in day_fields} for key, _label in _SUMMARY_ROWS}
	headcount = {d: 0 for d in day_fields}
	for d in day_fields:
		for _emp, cat in per_day_emp[d].items():
			if cat in counts:
				counts[cat][d] += 1
			headcount[d] += 1

	summary = []
	summary.append({label_field: "", "_is_summary": 1})
	for key, label in _SUMMARY_ROWS:
		row = {label_field: label, "_is_summary": 1}
		for d in day_fields:
			row[d] = counts[key][d]
		summary.append(row)

	headcount_row = {label_field: _("Total Headcount"), "_is_summary": 1}
	for d in day_fields:
		headcount_row[d] = headcount[d]
	summary.append(headcount_row)

	return summary


def execute(filters=None):
	original = _hrms_execute
	if original is None or original is execute:
		apply_patch()
		original = _hrms_execute
	if original is None or original is execute:
		frappe.throw(_("Monthly Attendance Sheet override is not correctly patched."))

	columns, data, message, chart = original(filters)

	if (filters or {}).get("group_by") == GREENHOUSE:
		table = greenhouse_table()
		for c in columns or []:
			if c.get("fieldname") == "greenhouse":
				c["options"] = table[1] if table else None
				c["width"] = 160
				break

	# Shift holds long labels (e.g. "LOE/ECE SECURITY DAY SHIFT"); left-align it
	# so the names read naturally instead of hugging the right edge.
	for c in columns or []:
		if c.get("fieldname") == "shift":
			c["align"] = "left"
			break

	filters = frappe._dict(filters or {})
	if data and not filters.summarized_view:
		try:
			# Group the default view by shift on the server so users don't need to
			# click-sort the Shift column (whose persisted sort would otherwise
			# scatter the summary block on load). Skipped when group_by is set, as
			# that inserts its own group-header rows we must not reorder.
			if not filters.group_by:
				data = sorted(
					data,
					key=lambda r: (
						str(r.get("shift") or "").lower(),
						str(r.get("employee_name") or ""),
					),
				)
			data = list(data) + build_summary_rows(data, columns)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Monthly Attendance Sheet summary rows")

	return columns, data, message, chart


execute._upande_ta_patched = True


_hrms = None
_hrms_execute = None
_hrms_gerd = None
_patched = False


def get_employee_related_details(filters):
	"""hrms' ``get_employee_related_details`` plus a "Category" (Employee Grade)
	filter, mirroring the custom Monthly Attendance Report's Category filter
	(``WHERE e.grade = %(category)s``).

	Implemented as a post-filter over the original result so we never fork hrms'
	query logic. Handles both result shapes: the flat ``{name: emp}`` map and the
	grouped ``{group_value: {name: emp}}`` map produced when a Group By is set.
	"""
	original = _hrms_gerd or getattr(_hrms, "_upande_ta_original_gerd", None)
	f = frappe._dict(filters or {})

	if f.get("group_by") == GREENHOUSE:
		# HRMS orders by the group-by column, which Employee does not have
		emp_map, _values = original(frappe._dict(filters, group_by=None))
		if f.get("category"):
			emp_map = {name: emp for name, emp in emp_map.items() if emp.get("grade") == f.category}
		return group_by_greenhouse(emp_map)

	emp_map, group_by_param_values = original(filters)

	category = f.get("category")
	if not category:
		return emp_map, group_by_param_values

	if f.get("group_by"):
		filtered_map = {}
		filtered_values = []
		for parameter, employees in emp_map.items():
			kept = frappe._dict(
				{name: emp for name, emp in employees.items() if emp.get("grade") == category}
			)
			if kept:
				filtered_map[parameter] = kept
				filtered_values.append(parameter)
		return filtered_map, filtered_values

	kept = {name: emp for name, emp in emp_map.items() if emp.get("grade") == category}
	return kept, group_by_param_values


get_employee_related_details._upande_ta_patched = True


def get_date_range_from_filters(filters):
	"""(start_date, end_date) as date objects for the report period.

	Backfilled onto the hrms report module because v15 lacks it — the existing
	shift resolver (build_shift_resolver) already calls
	``_hrms.get_date_range_from_filters`` and would otherwise raise.
	"""
	filters = frappe._dict(filters or {})
	if filters.get("filter_based_on") == "Month":
		total_days = _hrms.get_total_days_in_month(filters)
		start_date = getdate(f"{cstr(filters.year)}-{cstr(filters.month)}-01")
		end_date = getdate(f"{cstr(filters.year)}-{cstr(filters.month)}-{total_days}")
		return start_date, end_date
	return getdate(filters.start_date), getdate(filters.end_date)


def get_employee_holiday_map(employee_details, filters):
	"""{employee: [holiday rows]} resolved per-date from Holiday List Assignments.

	For any date not covered by an assignment we fall back to the company's
	assignment and finally to the employee's static ``Employee.holiday_list``
	(current v15 behaviour). So employees who never had a Bulk Week Off — hence
	no assignments — render exactly as before, and those who did get the correct
	*historical* week off per date instead of the latest one applied backwards.
	"""
	if not employee_details:
		return {}

	start_date, end_date = get_date_range_from_filters(filters)

	employees = list(employee_details.keys())
	companies = list({d.company for d in employee_details.values() if d.get("company")})

	assigned = get_assigned_holiday_lists_to_employee_and_company(employees + companies, start_date, end_date)

	company_default = {}

	def default_for(company):
		if company not in company_default:
			company_default[company] = (
				frappe.get_cached_value("Company", company, "default_holiday_list") if company else None
			)
		return company_default[company]

	employee_hl_ranges = {}
	for employee, details in employee_details.items():
		employee_ranges = assigned.get(employee, [])
		company_ranges = assigned.get(details.get("company"), [])
		ranges = fill_holiday_list_date_gaps(employee_ranges, company_ranges, start_date, end_date)

		static_hl = details.get("holiday_list") or default_for(details.get("company"))
		if static_hl:
			ranges = fill_holiday_list_date_gaps(
				ranges,
				[{"holiday_list": static_hl, "from_date": start_date, "to_date": end_date}],
				start_date,
				end_date,
			)
		if ranges:
			employee_hl_ranges[employee] = ranges

	if not employee_hl_ranges:
		return {}

	used_hl_names = {r["holiday_list"] for ranges in employee_hl_ranges.values() for r in ranges}

	Holiday = frappe.qb.DocType("Holiday")
	holiday_rows = (
		frappe.qb.from_(Holiday)
		.select(Holiday.parent, Holiday.holiday_date, Holiday.weekly_off)
		.where(Holiday.parent.isin(list(used_hl_names)))
		.where(Holiday.holiday_date.between(start_date, end_date))
	).run(as_dict=True)

	hl_holidays = {}
	for h in holiday_rows:
		hl_holidays.setdefault(h.parent, []).append(h)

	employee_holiday_map = {}
	for employee, ranges in employee_hl_ranges.items():
		holidays = [
			h
			for r in ranges
			for h in hl_holidays.get(r["holiday_list"], [])
			if r["from_date"] <= getdate(h.holiday_date) <= r["to_date"]
		]
		if holidays:
			employee_holiday_map[employee] = holidays

	return employee_holiday_map


def get_rows(employee_details, filters, holiday_map, attendance_map):
	"""Drop-in for the hrms report's get_rows that resolves each employee's
	holidays per-date (via Holiday List Assignments) instead of from the single
	static ``Employee.holiday_list``. ``holiday_map`` (the old per-list map built
	by the core report) is ignored on purpose.
	"""
	employee_holiday_map = get_employee_holiday_map(employee_details, filters)
	records = []

	for employee, details in employee_details.items():
		holidays = employee_holiday_map.get(employee, [])

		if filters.summarized_view:
			attendance = _hrms.get_attendance_status_for_summarized_view(
				employee, filters, holidays, details.joined_in_current_period, details.joined_date
			)
			if not attendance:
				continue

			leave_summary = _hrms.get_leave_summary(employee, filters)
			entry_exits_summary = _hrms.get_entry_exits_summary(employee, filters)

			row = {"employee": employee, "employee_name": details.employee_name}
			_hrms.set_defaults_for_summarized_view(filters, row)
			row.update(attendance)
			row.update(leave_summary)
			row.update(entry_exits_summary)

			records.append(row)
		else:
			employee_attendance = attendance_map.get(employee)
			if not employee_attendance:
				continue

			attendance_for_employee = _hrms.get_attendance_status_for_detailed_view(
				employee, filters, employee_attendance, holidays
			)
			for record in attendance_for_employee:
				record.update({"employee": employee, "employee_name": details.employee_name})

			records.extend(attendance_for_employee)

	return records


def apply_patch(*args, **kwargs):
	global _hrms, _hrms_execute, _hrms_gerd, _patched

	# Runs on before_request AND before_job (before every background job, payroll
	# included). It must NEVER raise — a failure would abort the job. Degrade to a
	# logged no-op; the report simply keeps hrms' default behaviour if this fails.
	try:
		from hrms.hr.report.monthly_attendance_sheet import monthly_attendance_sheet as mod

		_hrms = mod

		if not getattr(getattr(mod, "execute", None), "_upande_ta_patched", False):
			mod._upande_ta_original_execute = mod.execute
		_hrms_execute = getattr(mod, "_upande_ta_original_execute", None)

		# Stash the original employee fetch so the "Category" (Employee Grade)
		# filter can post-filter its result without forking hrms' query logic.
		if not getattr(mod.get_employee_related_details, "_upande_ta_patched", False):
			mod._upande_ta_original_gerd = mod.get_employee_related_details
		_hrms_gerd = getattr(mod, "_upande_ta_original_gerd", None)

		if _patched and getattr(mod.execute, "_upande_ta_patched", False):
			return

		mod.get_employee_related_details = get_employee_related_details
		mod.get_attendance_records = get_attendance_records
		mod.get_attendance_map = get_attendance_map
		mod.get_attendance_status_for_detailed_view = get_attendance_status_for_detailed_view
		mod.get_chart_data = get_chart_data
		mod.get_message = get_message
		# Date-effective holiday resolution: get_rows now pulls each employee's
		# holidays per-date from Holiday List Assignments (with static fallback), and
		# get_date_range_from_filters is backfilled for the existing shift resolver.
		if not hasattr(mod, "get_date_range_from_filters"):
			mod.get_date_range_from_filters = get_date_range_from_filters
		mod.get_employee_holiday_map = get_employee_holiday_map
		mod.get_rows = get_rows
		mod.execute = execute
		_patched = True
	except Exception:
		frappe.log_error(
			title="upande_ta monthly_attendance_sheet.apply_patch failed (patch skipped)",
			message=frappe.get_traceback(),
		)


def disable_prepared_report():
	"""Keep the Monthly Attendance Sheet running live, never as a Prepared Report.

	Wired into ``after_migrate`` (every ``bench migrate``, so every Frappe Cloud
	deploy): HRMS syncs this Report from its JSON with ``prepared_report = 1``,
	and a Prepared Report serves a snapshot built by a background job -- the
	grid shows attendance as it stood whenever that job last ran.

	Frappe also turns it on by itself: ``Report.execute_script_report`` arms a
	15-second timer and calls ``enable_prepared_report`` if a run outlasts it,
	so one slow month latches the report into snapshot mode for everyone.
	``disable_prepared_report_automation`` is the flag that stops that, so it is
	set as well; clearing ``prepared_report`` alone only lasts until the next
	slow run.

	Written with ``db.set_value``: a doc.save() on a standard report is refused
	outside developer mode. Must never raise -- a failure here should not abort
	the migrate.
	"""
	try:
		if not frappe.db.exists("Report", "Monthly Attendance Sheet"):
			return
		values = {"prepared_report": 0}
		if frappe.db.has_column("Report", "disable_prepared_report_automation"):
			values["disable_prepared_report_automation"] = 1
		current = frappe.db.get_value("Report", "Monthly Attendance Sheet", list(values), as_dict=True)
		if current and any(cint(current.get(field)) != value for field, value in values.items()):
			frappe.db.set_value("Report", "Monthly Attendance Sheet", values)
			frappe.clear_document_cache("Report", "Monthly Attendance Sheet")
	except Exception:
		frappe.log_error(
			title="upande_ta disable_prepared_report failed",
			message=frappe.get_traceback(),
		)


#: Where the switches this report reads live.
SETTING_DOCTYPE = "Biometric Setting"


def _setting_enabled(fieldname: str, default: bool) -> bool:
	"""Read one Check on Biometric Setting, with "never saved" as `default`.

	A Single materialises its field defaults only when it is first saved, so a
	site that has never opened Biometric Setting has no row for a newly shipped
	field at all. That must read as the field's own default, or a setting would
	behave one way until somebody saved a form they have no reason to open and
	another way afterwards — hence the raw read of `tabSingles` rather than
	``get_single_value``, which casts a missing Check to 0.
	"""
	try:
		row = frappe.db.sql(
			"""SELECT value FROM tabSingles WHERE doctype = %s AND field = %s""",
			(SETTING_DOCTYPE, fieldname),
		)
	except Exception:
		# the setting doctype is not on this site, or has not synced yet
		return default
	return bool(cint(row[0][0])) if row else default


def week_off_change_disabled() -> bool:
	"""Whether changing a week off from a day cell is switched off. Off by
	default, i.e. the change is available."""
	return _setting_enabled("disable_week_off_change", False)


def shift_change_disabled() -> bool:
	"""Whether changing a shift from the Shift column is switched off. Off by
	default, i.e. the change is available."""
	return _setting_enabled("disable_shift_change", False)


def greenhouse_grouping_enabled() -> bool:
	"""Whether Group By offers Greenhouse: ticked in Biometric Setting, on a
	site that keeps employees' greenhouses. Off by default."""
	return _setting_enabled("group_by_greenhouse", False) and bool(greenhouse_table())


def extend_bootinfo(bootinfo=None):
	"""Tell the desk whether the week-off and shift changes are available, so
	the sheet knows without a round trip when a cell is clicked."""
	try:
		bootinfo.upande_ta_week_off_change_disabled = week_off_change_disabled()
		bootinfo.upande_ta_shift_change_disabled = shift_change_disabled()
		bootinfo.upande_ta_group_by_greenhouse = greenhouse_grouping_enabled()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Monthly Attendance Sheet bootinfo")
