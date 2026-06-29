# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt
"""
Show per-leave-type abbreviations (ML, PL, UL, AL, CL, ...) in the detailed
Monthly Attendance Sheet grid instead of a single generic "L".

HRMS' standard report (apps/hrms/.../monthly_attendance_sheet.py) maps every
"On Leave" cell to "L" via `status_map`. That report file is shared by every
site on the bench, so instead of editing it we monkey-patch the report module
at runtime from upande_ta (installed only on the sites that want this, e.g.
kaitet and mona).

We replace three functions so the leave_type travels from the Attendance row
all the way to the rendered cell:
  - get_attendance_records      -> also SELECT Attendance.leave_type
  - get_attendance_map          -> store leave cells as "On Leave|<Leave Type>"
  - get_attendance_status_for_detailed_view -> resolve the leave-type abbr
  - get_chart_data              -> treat any "On Leave..." value as a leave

The summarized view already breaks leaves down per type (one column per Leave
Type), so it is left untouched.
"""

import re

import frappe
from frappe import _
from frappe.utils import getdate

# Day columns use a "dd-mm-yyyy" fieldname (see get_columns_for_days).
_DAY_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")

# Explicit abbreviations requested by the business. Keys are matched
# case-insensitively against the Leave Type name.
LEAVE_TYPE_ABBR = {
	"maternity leave": "ML",
	"paternity leave": "PL",
	"unpaid leave": "UL",
	"annual leave": "AL",
	"compassionate leave": "CL",
	"compationate leave": "CL",  # tolerate the common misspelling
}

# Marker separating the base status from the leave type in the attendance map,
# e.g. "On Leave|Annual Leave". Chosen to never collide with a Leave Type name.
_LEAVE_SEP = "|"

# Cache generated abbreviations so two leave types never collapse to the same
# code within a single request (e.g. "Sick Leave" vs "Suspension Period").
_abbr_cache = {}


def get_leave_abbr(leave_type: str) -> str:
	"""Return a short, unique-ish abbreviation for a Leave Type name.

	Listed business types use their fixed code; everything else is derived
	from the initials of the words (Sick Leave (Full Pay) -> SLFP), trimmed
	to keep the grid narrow.
	"""
	if not leave_type:
		return "L"

	key = leave_type.strip().lower()
	if key in LEAVE_TYPE_ABBR:
		return LEAVE_TYPE_ABBR[key]

	if key in _abbr_cache:
		return _abbr_cache[key]

	# Strip bracketed/punctuation noise, take the initial of each word.
	cleaned = "".join(c if c.isalnum() else " " for c in leave_type)
	words = [w for w in cleaned.split() if w]
	abbr = "".join(w[0].upper() for w in words)[:4] or "L"

	# Avoid collisions with the fixed business codes or another generated code
	# (e.g. "Prorata Leave" would otherwise clash with Paternity Leave -> "PL").
	taken = set(LEAVE_TYPE_ABBR.values()) | set(_abbr_cache.values())
	if abbr in taken:
		base = abbr
		# append the next distinguishing letter from the first long word
		suffix_chars = (words[0][1:] if words else "") + "2"
		for ch in suffix_chars:
			candidate = (base + ch.upper())[:4]
			if candidate not in taken:
				abbr = candidate
				break

	_abbr_cache[key] = abbr
	return abbr


def get_attendance_records(filters):
	"""Same as HRMS' version but also selects leave_type."""
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


def get_attendance_map(filters):
	"""Same as HRMS' version but tags leave cells with their leave type."""
	attendance_list = get_attendance_records(filters)
	attendance_map = {}
	leave_map = {}

	for d in attendance_list:
		if d.status == "On Leave":
			# carry the leave type alongside the date
			leave_map.setdefault(d.employee, {}).setdefault(d.shift, []).append(
				(d.attendance_date, d.leave_type)
			)
			continue

		if d.shift is None:
			d.shift = ""

		attendance_map.setdefault(d.employee, {}).setdefault(d.shift, {})
		attendance_map[d.employee][d.shift][d.attendance_date] = d.status

	# leave is applicable for the entire day so all shifts should show the leave entry
	for employee, leave_days in leave_map.items():
		for assigned_shift, entries in leave_days.items():
			if employee not in attendance_map:
				attendance_map.setdefault(employee, {}).setdefault(assigned_shift, {})

			for attendance_date, leave_type in entries:
				value = "On Leave"
				if leave_type:
					value = f"On Leave{_LEAVE_SEP}{leave_type}"
				for shift in attendance_map[employee].keys():
					attendance_map[employee][shift][attendance_date] = value

	return attendance_map


def get_attendance_status_for_detailed_view(employee, filters, employee_attendance, holidays):
	"""Same as HRMS' version but resolves "On Leave|<type>" to its abbr."""
	total_days = _hrms.get_dates_in_period(filters)
	attendance_values = []

	for shift, status_dict in employee_attendance.items():
		row = {"shift": shift}
		for d in total_days:
			d = getdate(d)
			status = status_dict.get(d)

			if status is None and holidays:
				status = _hrms.get_holiday_status(d, holidays)

			if status and status.startswith("On Leave" + _LEAVE_SEP):
				leave_type = status.split(_LEAVE_SEP, 1)[1]
				abbr = get_leave_abbr(leave_type)
			else:
				abbr = _hrms.status_map.get(status, "")

			row[d.strftime("%d-%m-%Y")] = abbr

		attendance_values.append(row)

	return attendance_values


def get_chart_data(attendance_map, filters):
	"""Same as HRMS' version but counts any "On Leave..." value as a leave."""
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
	"""Legend strip shown above the grid.

	Same as HRMS' version for the base statuses, but the single "On Leave - L"
	chip is expanded into one blue chip per Leave Type (ML, PL, UL, AL, CL, ...)
	so the legend matches the per-type abbreviations now shown in the cells.
	"""

	def chip(color, label):
		return (
			f"<span style='border-left: 2px solid {color}; padding-right: 12px; "
			f"padding-left: 5px; margin-right: 3px;'>{label}</span>"
		)

	message = ""

	# Base (non-leave) statuses, in the report's usual order/colors.
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

	# One blue chip per Leave Type on this site, using the same abbreviations
	# the cells use. Sorted by abbreviation for a stable, readable legend.
	leave_types = frappe.db.get_all("Leave Type", pluck="name")
	leave_chips = sorted(
		((get_leave_abbr(lt), lt) for lt in leave_types), key=lambda x: x[0]
	)
	for abbr, label in leave_chips:
		message += chip("#318AD8", f"{label} - {abbr}")

	return message


def _classify(abbr):
	"""Map a rendered cell abbreviation to a summary category key.

	Leave-type codes (AL, ML, PL, ...) and the generic "L" all roll up to
	"on_leave". Returns None for blank/unmarked cells so they are not counted.
	"""
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
	# anything else non-blank is a leave-type abbreviation
	return "on_leave"


# Order/precedence when an employee has several shift rows on the same day:
# a worked status wins over leave/holiday/blank so the headcount isn't
# double-counted and reflects what the person actually did that day.
_PRECEDENCE = {
	"present": 6,
	"half_day": 5,
	"absent": 4,
	"on_leave": 3,
	"holiday": 2,
	"weekly_off": 1,
}

# Summary rows shown at the bottom, in display order.
_SUMMARY_ROWS = [
	("present", _("Present")),
	("absent", _("Absent")),
	("on_leave", _("On Leave")),
	("half_day", _("Half Day")),
	("holiday", _("Holiday")),
	("weekly_off", _("Weekly Off")),
]


def build_summary_rows(data, columns):
	"""Per-day count rows appended to the bottom of the detailed grid.

	Counts each employee once per day (deduped across shift rows, highest
	precedence status winning) and totals each category, plus a Total Headcount
	row. Labels go in the first wide text column so they read like a footer.
	"""
	day_fields = [
		c["fieldname"]
		for c in columns
		if c.get("fieldtype") == "Data" and _DAY_RE.match(str(c.get("fieldname", "")))
	]
	if not day_fields:
		return []

	# label column: prefer employee_name (always present in detailed view)
	label_field = "employee_name"

	# per_day_emp[day][employee] = best category for that employee that day
	per_day_emp = {d: {} for d in day_fields}

	for row in data:
		emp = row.get("employee")
		if not emp:
			# group-by header row (e.g. {'branch': 'X'}) — skip
			continue
		for d in day_fields:
			cat = _classify(row.get(d))
			if cat is None:
				continue
			cur = per_day_emp[d].get(emp)
			if cur is None or _PRECEDENCE.get(cat, 0) > _PRECEDENCE.get(cur, 0):
				per_day_emp[d][emp] = cat

	# tally categories per day
	counts = {key: {d: 0 for d in day_fields} for key, _label in _SUMMARY_ROWS}
	headcount = {d: 0 for d in day_fields}
	for d in day_fields:
		for _emp, cat in per_day_emp[d].items():
			if cat in counts:
				counts[cat][d] += 1
			headcount[d] += 1

	summary = []
	# spacer row so the totals visually separate from the employee rows.
	# `_is_summary` marks every summary row so the client can style them (bold +
	# border) and skip the day-cell coloring meant for employee rows.
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
	"""HRMS' execute, with per-day summary rows appended to the detailed view."""
	columns, data, message, chart = _hrms_execute(filters)

	filters = frappe._dict(filters or {})
	if data and not filters.summarized_view:
		try:
			data = list(data) + build_summary_rows(data, columns)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), "Monthly Attendance Sheet summary rows"
			)

	return columns, data, message, chart


# Reference to the HRMS report module, set by apply_patch().
_hrms = None
_hrms_execute = None
_patched = False


def apply_patch(*args, **kwargs):
	"""Idempotently swap our functions into the HRMS report module.

	Accepts/ignores args so it can be used both as a `before_request` hook and
	as a `before_job` hook (the latter passes method/kwargs/transaction_type).
	Prepared/queued reports run in a background RQ worker where `before_request`
	never fires, so `before_job` is what covers them.
	"""
	global _hrms, _hrms_execute, _patched
	if _patched:
		return

	from hrms.hr.report.monthly_attendance_sheet import monthly_attendance_sheet as mod

	_hrms = mod
	# keep a reference to the original execute BEFORE we swap it
	_hrms_execute = mod.execute
	mod.get_attendance_records = get_attendance_records
	mod.get_attendance_map = get_attendance_map
	mod.get_attendance_status_for_detailed_view = get_attendance_status_for_detailed_view
	mod.get_chart_data = get_chart_data
	mod.get_message = get_message
	mod.execute = execute
	_patched = True


def disable_prepared_report():
	"""Keep Monthly Attendance Sheet rendering live (not queued).

	The framework only shows the legend (the report `message`) for live reports,
	never for prepared/queued ones (query_report.js: `if (data.message &&
	!data.prepared_report)`). It also lets the colored abbreviations apply
	immediately without a Rebuild. `bench migrate` re-imports this standard hrms
	report and flips prepared_report back to 1, so we re-assert it after migrate.
	"""
	if frappe.db.exists("Report", "Monthly Attendance Sheet"):
		if frappe.db.get_value("Report", "Monthly Attendance Sheet", "prepared_report"):
			frappe.db.set_value("Report", "Monthly Attendance Sheet", "prepared_report", 0)
