# Copyright (c) 2026, Upande LTD and contributors

import re

import frappe
from frappe import _
from frappe.utils import getdate

from upande_ta.upande_ta.overrides.leave_type import LEAVE_TYPE_ABBR_FIELD

_DAY_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")

_LEAVE_SEP = "|"


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
			(ShiftAssignment.docstatus == 1)
			& (ShiftAssignment.status == "Active")
			& (ShiftAssignment.employee.isin(employees))
			& (ShiftAssignment.start_date <= end_date)
			& (ShiftAssignment.end_date.isnull() | (ShiftAssignment.end_date >= start_date))
		)
		.orderby(ShiftAssignment.start_date)
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


def get_attendance_map(filters):
	attendance_list = get_attendance_records(filters)

	resolve_shift = build_shift_resolver({d.employee for d in attendance_list}, filters)

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
	total_days = _hrms.get_dates_in_period(filters)
	attendance_values = []

	shift_attendance = employee_attendance or {"": {}}

	for shift, status_dict in shift_attendance.items():
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
	leave_chips = sorted(
		((get_leave_abbr(lt), lt) for lt in leave_types), key=lambda x: x[0]
	)
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
			frappe.log_error(
				frappe.get_traceback(), "Monthly Attendance Sheet summary rows"
			)

	return columns, data, message, chart


execute._upande_ta_patched = True


_hrms = None
_hrms_execute = None
_patched = False


def apply_patch(*args, **kwargs):
	global _hrms, _hrms_execute, _patched

	from hrms.hr.report.monthly_attendance_sheet import monthly_attendance_sheet as mod

	_hrms = mod

	if not getattr(getattr(mod, "execute", None), "_upande_ta_patched", False):
		mod._upande_ta_original_execute = mod.execute
	_hrms_execute = getattr(mod, "_upande_ta_original_execute", None)

	if _patched and getattr(mod.execute, "_upande_ta_patched", False):
		return

	mod.get_attendance_records = get_attendance_records
	mod.get_attendance_map = get_attendance_map
	mod.get_attendance_status_for_detailed_view = get_attendance_status_for_detailed_view
	mod.get_chart_data = get_chart_data
	mod.get_message = get_message
	mod.execute = execute
	_patched = True


def disable_prepared_report():
	if frappe.db.exists("Report", "Monthly Attendance Sheet"):
		if frappe.db.get_value("Report", "Monthly Attendance Sheet", "prepared_report"):
			frappe.db.set_value("Report", "Monthly Attendance Sheet", "prepared_report", 0)
