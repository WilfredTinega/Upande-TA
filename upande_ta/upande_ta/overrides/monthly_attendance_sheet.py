# Copyright (c) 2026, Upande LTD and contributors

import re

import frappe
from frappe import _
from frappe.utils import cint, getdate

from upande_ta.upande_ta.overrides.leave_type import LEAVE_TYPE_ABBR_FIELD

_DAY_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")

_LEAVE_SEP = "|"

# Extra report filters this override adds on top of HRMS'. Each maps the report
# filter's fieldname to the Employee field it restricts on. The Unit/Division
# field is a custom field shipped by the site apps (upande_hr / upande_kaitet),
# so it is only offered where it actually exists -- upande_ta itself runs on
# sites without it.
EXTRA_FILTER_FIELDS = {
	"employment_type": "employment_type",
	"unit_division": "custom_farm",
}


def get_extra_filter_config() -> list[dict]:
	"""Describe the extra filters the client should render, in display order.

	Returns one entry per Employee field that exists on this site; the client
	turns each into a Link filter. Driven off the Employee meta so the label and
	link target follow whatever the site's custom field says.
	"""
	meta = frappe.get_meta("Employee")
	config = []

	for filter_field, employee_field in EXTRA_FILTER_FIELDS.items():
		df = meta.get_field(employee_field)
		if not df or df.fieldtype != "Link" or not df.options:
			continue
		entry = {
			"fieldname": filter_field,
			"label": _(df.label or employee_field),
			"options": df.options,
		}
		# A unit belongs to exactly one company, so once a company is picked the
		# picker must not offer another company's units. Only advertise the
		# scope where the link target actually carries a company field — the
		# custom field is site-shipped and its target varies.
		if filter_field == "unit_division" and frappe.get_meta(df.options).has_field("company"):
			entry["company_scoped"] = 1
		config.append(entry)

	return config


def _unit_division_doctype():
	"""The doctype Employee's Unit/Division custom field links to, or None.

	Resolved from Employee's meta rather than taken from the client, so the
	table name interpolated into the query below can never come from a request.
	"""
	df = frappe.get_meta("Employee").get_field(EXTRA_FILTER_FIELDS["unit_division"])
	if not df or df.fieldtype != "Link" or not df.options:
		return None
	if not frappe.get_meta(df.options).has_field("company"):
		return None
	return df.options


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def unit_division_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for the Unit/Division report filter, scoped to the company.

	Endebess belongs to Kaitet Ltd., not Karen Roses, so selecting a company
	must narrow the list rather than leave every unit on offer. Honours the
	report's "Include Company Descendants" checkbox, because the report itself
	resolves employees across descendant companies.
	"""
	target = _unit_division_doctype()
	if not target:
		return []

	filters = filters or {}
	company = filters.get("company")

	companies = []
	if company:
		companies = [company]
		if cint(filters.get("include_company_descendants")):
			companies += frappe.db.get_descendants("Company", company) or []

	where = [f"`{searchfield}` LIKE %(txt)s"]
	params = {
		"txt":      f"%{txt or ''}%",
		"start":    cint(start),
		"page_len": cint(page_len) or 20,
	}
	if companies:
		where.append("IFNULL(`company`, '') IN %(companies)s")
		params["companies"] = tuple(companies)

	return frappe.db.sql(
		f"""
		SELECT `name`, IFNULL(`company`, '') AS company
		  FROM `tab{target}`
		 WHERE {" AND ".join(where)}
		 ORDER BY `name` ASC
		 LIMIT %(start)s, %(page_len)s
		""",
		params,
	)


def extend_bootinfo(bootinfo=None):
	"""Publish the extra filters to the desk so the client patch can render them
	without an extra round trip when the report opens."""
	try:
		bootinfo.upande_ta_attendance_filters = get_extra_filter_config()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Monthly Attendance Sheet filters bootinfo")


def get_extra_employee_conditions(filters) -> dict:
	"""Applied filter value per Employee field; empty when none are set."""
	meta = frappe.get_meta("Employee")
	conditions = {}

	for filter_field, employee_field in EXTRA_FILTER_FIELDS.items():
		value = filters.get(filter_field)
		if not value or not meta.has_field(employee_field):
			continue
		conditions[employee_field] = value

	return conditions


def get_allowed_employees(filters):
	"""Employees passing the extra filters, or None when none are set."""
	conditions = get_extra_employee_conditions(filters)
	if not conditions:
		return None

	if filters.get("companies"):
		conditions["company"] = ("in", filters.companies)

	return set(frappe.get_all("Employee", filters=conditions, pluck="name"))


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

	# The rows come from the Employee query, but the chart is built straight off
	# these records -- so the same employee restrictions have to be applied here
	# or the chart counts people the filters excluded.
	employee_conditions = get_extra_employee_conditions(filters)
	if filters.department or filters.branch or employee_conditions:
		Employee = frappe.qb.DocType("Employee")
		query = query.join(Employee).on(Attendance.employee == Employee.name)
		if filters.department and filters.department != "All Departments":
			query = query.where(Employee.department == filters.department)
		if filters.branch:
			query = query.where(Employee.branch == filters.branch)
		for employee_field, value in employee_conditions.items():
			query = query.where(Employee[employee_field] == value)

	query = query.orderby(Attendance.employee, Attendance.attendance_date)

	return query.run(as_dict=1)


def get_employee_related_details(filters):
	"""HRMS' employee query plus the Employment Type / Unit/Division filters.

	Wraps rather than reimplements the original so it keeps following HRMS'
	selected fields and group-by handling; the extra filters are applied by
	pruning the employees it returned.
	"""
	original = _hrms_get_employee_related_details
	if original is None or original is get_employee_related_details:
		apply_patch()
		original = _hrms_get_employee_related_details
	if original is None or original is get_employee_related_details:
		frappe.throw(_("Monthly Attendance Sheet override is not correctly patched."))

	emp_map, group_by_param_values = original(filters)

	allowed = get_allowed_employees(filters)
	if allowed is None:
		return emp_map, group_by_param_values

	if not filters.group_by:
		return {name: emp for name, emp in emp_map.items() if name in allowed}, group_by_param_values

	# Grouped: emp_map is {group value: {employee: details}}. Drop the employees
	# that were filtered out, then the groups left empty -- get_data iterates
	# group_by_param_values and would KeyError on a group we removed.
	pruned = {}
	for parameter, employees in emp_map.items():
		kept = frappe._dict(
			{name: emp for name, emp in employees.items() if name in allowed}
		)
		if kept:
			pruned[parameter] = kept

	return pruned, [value for value in group_by_param_values if value in pruned]


get_employee_related_details._upande_ta_patched = True


def build_shift_resolver(employees, filters):
	"""Return resolve(employee, attendance_date) -> the employee's shift for the period.

	The detailed view emits one row per distinct value this returns, so it
	deliberately resolves to a *single* shift per employee for the whole report
	period: the shift their assignments cover the most days of, ties going to
	the later assignment. Anything else -- a mid-period shift change, a day the
	assignments do not cover, or attendance stamped with a different shift by
	its source (manual Mark Attendance, Attendance Request, auto-marked Weekly
	Off, auto leave from Leave Application) -- would otherwise split the
	employee across two rows.

	Assignment `status` is deliberately NOT filtered on. HRMS' daily
	`mark_expired_shift_assignments_as_inactive` job flips every assignment to
	Inactive once its end_date has passed, so filtering on Active erased the
	shift for every day before a re-assignment: those days fell through to
	Employee.default_shift, which is unset for most staff, and the employee
	gained a second row with a blank Shift cell.
	"""
	employees = list(employees)
	if not employees:
		return lambda employee, attendance_date=None: ""

	period_start, period_end = _hrms.get_date_range_from_filters(filters)
	period_start, period_end = getdate(period_start), getdate(period_end)

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
			& (ShiftAssignment.employee.isin(employees))
			& (ShiftAssignment.start_date <= period_end)
			& (ShiftAssignment.end_date.isnull() | (ShiftAssignment.end_date >= period_start))
		)
		.orderby(ShiftAssignment.start_date)
	).run(as_dict=1)

	# employee -> shift -> [days covered inside the period, latest start date]
	coverage = {}
	for r in rows:
		if not r.shift_type:
			continue
		assignment_start = getdate(r.start_date) if r.start_date else period_start
		covered_from = max(assignment_start, period_start)
		covered_to = min(getdate(r.end_date), period_end) if r.end_date else period_end
		days = (covered_to - covered_from).days + 1
		if days <= 0:
			continue
		shifts = coverage.setdefault(r.employee, {})
		tally = shifts.setdefault(r.shift_type, [0, assignment_start])
		tally[0] += days
		tally[1] = max(tally[1], assignment_start)

	default_shifts = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ("in", employees)},
			fields=["name", "default_shift"],
			as_list=1,
		)
	)

	resolved = {}
	for employee in employees:
		shifts = coverage.get(employee)
		if shifts:
			resolved[employee] = max(shifts.items(), key=lambda item: (item[1][0], item[1][1]))[0]
		else:
			resolved[employee] = default_shifts.get(employee) or ""

	def resolve(employee, attendance_date=None):
		return resolved.get(employee) or ""

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
_hrms_get_employee_related_details = None
_patched = False


def apply_patch(*args, **kwargs):
	global _hrms, _hrms_execute, _hrms_get_employee_related_details, _patched

	from hrms.hr.report.monthly_attendance_sheet import monthly_attendance_sheet as mod

	_hrms = mod

	if not getattr(getattr(mod, "execute", None), "_upande_ta_patched", False):
		mod._upande_ta_original_execute = mod.execute
	_hrms_execute = getattr(mod, "_upande_ta_original_execute", None)

	if not getattr(
		getattr(mod, "get_employee_related_details", None), "_upande_ta_patched", False
	):
		mod._upande_ta_original_get_employee_related_details = mod.get_employee_related_details
	_hrms_get_employee_related_details = getattr(
		mod, "_upande_ta_original_get_employee_related_details", None
	)

	if _patched and getattr(mod.execute, "_upande_ta_patched", False):
		return

	mod.get_attendance_records = get_attendance_records
	mod.get_employee_related_details = get_employee_related_details
	mod.get_attendance_map = get_attendance_map
	mod.get_attendance_status_for_detailed_view = get_attendance_status_for_detailed_view
	mod.get_chart_data = get_chart_data
	mod.get_message = get_message
	mod.execute = execute
	_patched = True


def disable_prepared_report():
	"""Keep the Monthly Attendance Sheet running live, never as a Prepared Report.

	A Prepared Report serves a snapshot built by a background job, so the grid
	shows attendance as it stood whenever that job last ran -- which is exactly
	what people open the report to check.

	Frappe turns this on by itself: `Report.execute_script_report` arms a
	15-second timer and calls `enable_prepared_report` if the run outlasts it,
	so one slow month (a company with ~1,400 employees) latches the report into
	snapshot mode for everyone, permanently. `disable_prepared_report_automation`
	is the flag that stops that, so it has to be set as well -- clearing
	`prepared_report` alone only lasts until the next slow run.

	Written with db.set_value: the Report is standard, and a doc.save() on a
	standard report is refused outside developer mode.
	"""
	if not frappe.db.exists("Report", "Monthly Attendance Sheet"):
		return

	current = frappe.db.get_value(
		"Report",
		"Monthly Attendance Sheet",
		["prepared_report", "disable_prepared_report_automation"],
		as_dict=True,
	)
	if not current:
		return

	if current.prepared_report or not current.disable_prepared_report_automation:
		frappe.db.set_value(
			"Report",
			"Monthly Attendance Sheet",
			{"prepared_report": 0, "disable_prepared_report_automation": 1},
		)
		frappe.clear_cache(doctype="Report")
