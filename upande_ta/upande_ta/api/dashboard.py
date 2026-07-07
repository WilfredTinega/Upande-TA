# Copyright (c) 2026, Upande LTD and contributors

from datetime import time as _time

import frappe
from frappe.utils import add_days, get_datetime, get_time, getdate, nowdate

_MAX_SHIFT_SECONDS = 14 * 3600

_NIGHT_DIVIDER = _time(12, 0)


def _overnight_shift_map():
	"""{shift_name: split_time} for shifts treated as crossing midnight, so the
	dashboard pairs yesterday-evening (check-in) with this-morning (check-out).

	A shift qualifies if its Shift Type start_time > end_time (e.g. 18:00 -> 06:00)
	OR its name contains both "security" and "night" — the latter catches security
	night shifts even when their times aren't configured as overnight. The split
	time (the shift's start_time, or noon as a fallback) separates the evening
	clock-in from the early-morning clock-out."""
	out = {}
	for s in frappe.get_all("Shift Type", fields=["name", "start_time", "end_time"]):
		nm = (s.name or "").lower()
		named = "security" in nm and "night" in nm
		by_time = (
			s.start_time is not None
			and s.end_time is not None
			and get_time(s.start_time) > get_time(s.end_time)
		)
		if named or by_time:
			out[s.name] = get_time(s.start_time) if s.start_time is not None else _time(12, 0)
	return out


def _employee_has_custom_farm():
	return "custom_farm" in frappe.db.get_table_columns("Employee")


def _scoped_employees(company, farm, department, designation, employee):
	has_farm = _employee_has_custom_farm()
	if not any([company, farm and has_farm, department, designation, employee]):
		return None
	filters = {"status": "Active"}
	if employee:
		filters["name"] = employee
	if company:
		filters["company"] = company
	if department:
		filters["department"] = department
	if designation:
		filters["designation"] = designation
	if farm and has_farm:
		filters["custom_farm"] = farm
	names = frappe.get_all("Employee", filters=filters, pluck="name")
	return names


@frappe.whitelist()
def get_ta_dashboard_stats(from_date=None, to_date=None,
                           company=None, farm=None, department=None,
                           designation=None, employee=None):
	today = getdate(nowdate())
	from_date = getdate(from_date) if from_date else today
	to_date = getdate(to_date) if to_date else today
	if from_date > to_date:
		from_date, to_date = to_date, from_date

	scoped = _scoped_employees(company, farm, department, designation, employee)
	total_employees = _total_employees(scoped)
	if scoped is not None and not scoped:
		payload = _empty_payload(from_date, to_date)
		payload["total_employees"] = total_employees
		return payload

	emp_clause = ""
	params_extra = {}
	if scoped is not None:
		emp_clause = " AND employee IN %(emp_list)s"
		params_extra["emp_list"] = tuple(scoped)

	totals = frappe.db.sql(
		f"""
		SELECT
			SUM(CASE WHEN log_type = 'IN'  THEN 1 ELSE 0 END) AS in_count,
			SUM(CASE WHEN log_type = 'OUT' THEN 1 ELSE 0 END) AS out_count,
			COUNT(*) AS total,
			COUNT(DISTINCT employee) AS unique_employees,
			COUNT(DISTINCT CASE WHEN log_type = 'IN'  THEN employee END) AS unique_in,
			COUNT(DISTINCT CASE WHEN log_type = 'OUT' THEN employee END) AS unique_out
		FROM `tabEmployee Checkin`
		WHERE DATE(time) BETWEEN %(from_date)s AND %(to_date)s
		{emp_clause}
		""",
		{"from_date": from_date, "to_date": to_date, **params_extra},
		as_dict=True,
	)[0]

	today_totals = frappe.db.sql(
		f"""
		SELECT
			SUM(CASE WHEN log_type = 'IN'  THEN 1 ELSE 0 END) AS in_count,
			SUM(CASE WHEN log_type = 'OUT' THEN 1 ELSE 0 END) AS out_count,
			COUNT(DISTINCT employee) AS unique_employees
		FROM `tabEmployee Checkin`
		WHERE DATE(time) = %(today)s
		{emp_clause}
		""",
		{"today": today, **params_extra},
		as_dict=True,
	)[0]

	per_day = frappe.db.sql(
		f"""
		SELECT
			DATE(time) AS date,
			SUM(CASE WHEN log_type = 'IN'  THEN 1 ELSE 0 END) AS in_count,
			SUM(CASE WHEN log_type = 'OUT' THEN 1 ELSE 0 END) AS out_count
		FROM `tabEmployee Checkin`
		WHERE DATE(time) BETWEEN %(from_date)s AND %(to_date)s
		{emp_clause}
		GROUP BY DATE(time)
		ORDER BY DATE(time) ASC
		""",
		{"from_date": from_date, "to_date": to_date, **params_extra},
		as_dict=True,
	)

	devices_online = 0
	devices_total = 0
	if frappe.db.exists("DocType", "Biometric Device"):
		row = frappe.db.sql(
			"""
			SELECT
				COUNT(*) AS total,
				SUM(CASE WHEN status = 'Online' THEN 1 ELSE 0 END) AS online
			FROM `tabBiometric Device`
			WHERE parenttype = 'Biometric Setting'
			""",
			as_dict=True,
		)[0]
		devices_total = int(row.get("total") or 0)
		devices_online = int(row.get("online") or 0)

	return {
		"from_date": str(from_date),
		"to_date": str(to_date),
		"window": {
			"in_count": int(totals.get("in_count") or 0),
			"out_count": int(totals.get("out_count") or 0),
			"total": int(totals.get("total") or 0),
			"unique_employees": int(totals.get("unique_employees") or 0),
			"unique_in": int(totals.get("unique_in") or 0),
			"unique_out": int(totals.get("unique_out") or 0),
		},
		"today": {
			"in_count": int(today_totals.get("in_count") or 0),
			"out_count": int(today_totals.get("out_count") or 0),
			"unique_employees": int(today_totals.get("unique_employees") or 0),
		},
		"per_day": [
			{
				"date": str(row["date"]),
				"in_count": int(row["in_count"] or 0),
				"out_count": int(row["out_count"] or 0),
			}
			for row in per_day
		],
		"devices": {"online": devices_online, "total": devices_total},
		"total_employees": total_employees,
	}


def _total_employees(scoped):
	if scoped is not None:
		return len(scoped)
	return frappe.db.count("Employee", {"status": "Active"})


def _empty_payload(from_date, to_date):
	return {
		"from_date": str(from_date),
		"to_date": str(to_date),
		"window": {"in_count": 0, "out_count": 0, "total": 0, "unique_employees": 0, "unique_in": 0, "unique_out": 0},
		"today": {"in_count": 0, "out_count": 0, "unique_employees": 0},
		"per_day": [],
		"devices": {"online": 0, "total": 0},
		"total_employees": 0,
	}


def _overnight_shift_assignments(employees, on_date, overnight_types):
	"""{employee: (start_time, end_time)} for each employee on an Active, submitted
	overnight Shift Assignment covering `on_date`. Bulk-fetched to avoid N+1."""
	if not overnight_types or not employees:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT employee, shift_type
		FROM `tabShift Assignment`
		WHERE docstatus = 1
		  AND status = 'Active'
		  AND shift_type IN %(shifts)s
		  AND start_date <= %(d)s
		  AND (end_date IS NULL OR end_date >= %(d)s)
		  AND employee IN %(emps)s
		ORDER BY start_date DESC
		""",
		{"shifts": tuple(overnight_types.keys()), "d": on_date, "emps": tuple(employees)},
		as_dict=True,
	)
	out = {}
	for r in rows:
		if r.employee not in out:
			out[r.employee] = overnight_types[r.shift_type]
	return out


@frappe.whitelist()
def get_ta_dashboard_checkins(date=None, company=None, farm=None,
                              department=None, designation=None, employee=None,
                              limit: int = 5000):
	day = getdate(date) if date else getdate(nowdate())
	prev_day = add_days(day, -1)
	limit = max(1, min(int(limit or 5000), 10000))

	scoped = _scoped_employees(company, farm, department, designation, employee)
	if scoped is not None and not scoped:
		return {"date": str(day), "rows": []}

	emp_clause = ""
	params = {
		"win_start": get_datetime(f"{prev_day} 00:00:00"),
		"win_end": get_datetime(f"{add_days(day, 1)} 00:00:00"),
	}
	if scoped is not None:
		emp_clause = " AND ec.employee IN %(emp_list)s"
		params["emp_list"] = tuple(scoped)

	scans = frappe.db.sql(
		f"""
		SELECT
			ec.employee,
			COALESCE(ec.employee_name, e.employee_name) AS employee_name,
			e.attendance_device_id,
			e.designation,
			ec.time,
			ec.log_type,
			ec.shift
		FROM `tabEmployee Checkin` ec
		LEFT JOIN `tabEmployee` e ON e.name = ec.employee
		WHERE ec.time >= %(win_start)s AND ec.time < %(win_end)s
		{emp_clause}
		ORDER BY ec.employee ASC, ec.time ASC
		""",
		params,
		as_dict=True,
	)

	by_emp = {}
	for s in scans:
		by_emp.setdefault(s.employee, []).append(s)

	from upande_ta.upande_ta.overrides.employee_checkin import _overnight_shift_types

	overnight_map = _overnight_shift_map()
	overnight_types = _overnight_shift_types()
	overnight_emp = _overnight_shift_assignments(list(by_emp.keys()), prev_day, overnight_types)

	def _fmt(dt):
		return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

	result_rows = []
	for emp, emp_scans in by_emp.items():
		meta = emp_scans[0]

		ov_start = None
		for s in emp_scans:
			if s.shift and s.shift in overnight_map:
				ov_start = overnight_map[s.shift]
				break
		if ov_start is None and emp in overnight_emp:
			ov_start = get_time(overnight_emp[emp][0])

		if ov_start is not None:
			evening, morning = [], []
			for s in emp_scans:
				t = get_datetime(s.time)
				d = getdate(t)
				tod = get_time(t)
				if d == prev_day and tod >= _NIGHT_DIVIDER:
					evening.append(t)
				elif d == day and tod < _NIGHT_DIVIDER:
					morning.append(t)
			check_in = min(evening) if evening else None
			if check_in is not None:
				morning = [
					t for t in morning
					if (t - check_in).total_seconds() <= _MAX_SHIFT_SECONDS
				]
			check_out = max(morning) if morning else None
		else:
			ins = [get_datetime(s.time) for s in emp_scans
			       if getdate(s.time) == day and (s.log_type or "") == "IN"]
			outs = [get_datetime(s.time) for s in emp_scans
			        if getdate(s.time) == day and (s.log_type or "") == "OUT"]
			check_in = min(ins) if ins else None
			if check_in is not None:
				outs = [
					t for t in outs
					if (t - check_in).total_seconds() <= _MAX_SHIFT_SECONDS
				]
			check_out = max(outs) if outs else None

		if check_in is None and check_out is None:
			continue

		show_out = bool(check_out) and (
			not check_in or (check_out - check_in).total_seconds() > 10 * 60
		)
		worked_seconds = None
		if check_in and check_out and check_out > check_in:
			worked_seconds = (check_out - check_in).total_seconds()

		shift_vals = [s.shift for s in emp_scans if s.get("shift")]
		result_rows.append({
			"employee_number": meta.get("attendance_device_id") or "",
			"employee_name": meta.get("employee_name") or emp,
			"shift": max(shift_vals) if shift_vals else "",
			"designation": meta.get("designation") or "",
			"check_in": _fmt(check_in),
			"check_out": _fmt(check_out) if show_out else "",
			"worked_hours": f"{worked_seconds / 3600:.1f}h" if worked_seconds else "",
			"_worked_seconds": worked_seconds,
			"_check_in": check_in,
		})

	def _sort_key(row):
		ws = row["_worked_seconds"]
		has_pair = ws is not None and ws > 0
		return (
			0 if has_pair else 1,
			-(ws or 0),
			row["_check_in"] or get_datetime(f"{add_days(day, 1)} 00:00:00"),
		)

	result_rows.sort(key=_sort_key)
	for row in result_rows:
		row.pop("_worked_seconds", None)
		row.pop("_check_in", None)

	return {"date": str(day), "rows": result_rows[:limit]}


@frappe.whitelist()
def get_ta_dashboard_filter_options(company=None, farm=None, department=None, designation=None):
	has_farm = _employee_has_custom_farm()
	fields = ["name", "first_name", "last_name", "designation", "department", "company"]
	if has_farm:
		fields.append("custom_farm")
	rows = frappe.get_all("Employee", filters={"status": "Active"}, fields=fields)

	companies = sorted({r.company for r in rows if r.company})
	farms = sorted({r.custom_farm for r in rows if r.get("custom_farm")}) if has_farm else []

	scope = rows
	if company:
		scope = [r for r in scope if r.company == company]
	if farm and has_farm:
		scope = [r for r in scope if r.get("custom_farm") == farm]

	departments = sorted({r.department for r in scope if r.department})

	designation_pool = scope
	if department:
		designation_pool = [r for r in designation_pool if r.department == department]
	designations = sorted({r.designation for r in designation_pool if r.designation})

	emp_pool = scope
	if department:
		emp_pool = [r for r in emp_pool if r.department == department]
	if designation:
		emp_pool = [r for r in emp_pool if r.designation == designation]
	employees = [
		{
			"value": r.name,
			"label": f"{(r.first_name or '').strip()} {(r.last_name or '').strip()}".strip() or r.name,
		}
		for r in emp_pool
	]
	employees.sort(key=lambda e: e["label"].lower())

	enabled = _enabled_filters()
	if not has_farm:
		enabled["farm"] = False

	return {
		"companies": companies,
		"farms": farms,
		"departments": departments,
		"designations": designations,
		"employees": employees,
		"has_farm": bool(has_farm),
		"enabled_filters": enabled,
	}


def _enabled_filters():
	flags = frappe.db.get_value(
		"Biometric Setting", "Biometric Setting",
		["r_company", "r_farm", "r_department", "r_designation", "r_employee"],
		as_dict=True,
	) or {}
	return {
		"company":     bool(flags.get("r_company")),
		"farm":        bool(flags.get("r_farm")),
		"department":  bool(flags.get("r_department")),
		"designation": bool(flags.get("r_designation")),
		"employee":    bool(flags.get("r_employee")),
	}
