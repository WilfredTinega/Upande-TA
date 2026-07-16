# Copyright (c) 2026, Upande LTD and contributors

import calendar
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


def _scope_meta(company, farm, department, designation, employee, on_date):
	"""Employees active (joined, not relieved) on ``on_date`` within the given scope,
	with the metadata needed to render Absent / Leave / Weekly-Off table rows."""
	has_farm = _employee_has_custom_farm()
	conds = [
		"status = 'Active'",
		"(date_of_joining IS NULL OR date_of_joining <= %(d)s)",
		"(relieving_date IS NULL OR relieving_date >= %(d)s)",
	]
	params = {"d": on_date}
	if employee:
		conds.append("name = %(employee)s"); params["employee"] = employee
	if company:
		conds.append("company = %(company)s"); params["company"] = company
	if department:
		conds.append("department = %(department)s"); params["department"] = department
	if designation:
		conds.append("designation = %(designation)s"); params["designation"] = designation
	if farm and has_farm:
		conds.append("custom_farm = %(farm)s"); params["farm"] = farm

	return frappe.db.sql(
		"SELECT name, employee_name, attendance_device_id, designation, default_shift, "
		"holiday_list FROM `tabEmployee` WHERE " + " AND ".join(conds),
		params, as_dict=True,
	)


def _leave_initials(leave_type):
	"""Initials of a leave type, e.g. 'Annual Leave' -> 'AL'. Falls back to 'L'."""
	if not leave_type:
		return "L"
	letters = [w[0] for w in str(leave_type).split() if w[:1].isalpha()]
	return "".join(letters).upper() or "L"


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

	# Filter on the indexed `time` column with a half-open range rather than
	# DATE(time) (which forces a full scan of the check-in table).
	win_start = get_datetime(f"{from_date} 00:00:00")
	win_end = get_datetime(f"{add_days(to_date, 1)} 00:00:00")

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
		WHERE time >= %(win_start)s AND time < %(win_end)s
		{emp_clause}
		""",
		{"win_start": win_start, "win_end": win_end, **params_extra},
		as_dict=True,
	)[0]

	today_totals = frappe.db.sql(
		f"""
		SELECT
			SUM(CASE WHEN log_type = 'IN'  THEN 1 ELSE 0 END) AS in_count,
			SUM(CASE WHEN log_type = 'OUT' THEN 1 ELSE 0 END) AS out_count,
			COUNT(DISTINCT employee) AS unique_employees
		FROM `tabEmployee Checkin`
		WHERE time >= %(today_start)s AND time < %(today_end)s
		{emp_clause}
		""",
		{
			"today_start": get_datetime(f"{today} 00:00:00"),
			"today_end": get_datetime(f"{add_days(today, 1)} 00:00:00"),
			**params_extra,
		},
		as_dict=True,
	)[0]

	per_day = frappe.db.sql(
		f"""
		SELECT
			DATE(time) AS date,
			SUM(CASE WHEN log_type = 'IN'  THEN 1 ELSE 0 END) AS in_count,
			SUM(CASE WHEN log_type = 'OUT' THEN 1 ELSE 0 END) AS out_count
		FROM `tabEmployee Checkin`
		WHERE time >= %(win_start)s AND time < %(win_end)s
		{emp_clause}
		GROUP BY DATE(time)
		ORDER BY DATE(time) ASC
		""",
		{"win_start": win_start, "win_end": win_end, **params_extra},
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
	present_ids = set()
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
		present_ids.add(emp)

		show_out = bool(check_out) and (
			not check_in or (check_out - check_in).total_seconds() > 10 * 60
		)
		worked_seconds = None
		if check_in and check_out and check_out > check_in:
			worked_seconds = (check_out - check_in).total_seconds()

		shift_vals = [s.shift for s in emp_scans if s.get("shift")]
		result_rows.append({
			"employee": emp,
			"employee_number": meta.get("attendance_device_id") or "",
			"employee_name": meta.get("employee_name") or emp,
			"shift": max(shift_vals) if shift_vals else "",
			"designation": meta.get("designation") or "",
			"check_in": _fmt(check_in),
			"check_out": _fmt(check_out) if show_out else "",
			"worked_hours": f"{worked_seconds / 3600:.1f}h" if worked_seconds else "",
			"status": "Present",
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

	present_rows = result_rows[:limit]

	# ---- attendance breakdown + per-category rows (for tile → table filtering) ----
	# "present" is derived from the punches above (overnight-aware), so night-shift
	# staff who clocked in the previous evening are not miscounted as absent.
	scope_meta = _scope_meta(company, farm, department, designation, employee, day)
	scope_ids = {m["name"] for m in scope_meta}
	present_ids &= scope_ids

	leave_map = {}
	if scope_ids:
		for r in frappe.db.sql(
			"SELECT employee, leave_type FROM `tabLeave Application` "
			"WHERE status='Approved' AND docstatus=1 "
			"AND from_date <= %(d)s AND to_date >= %(d)s AND employee IN %(emps)s",
			{"d": day, "emps": tuple(scope_ids)}, as_dict=True,
		):
			leave_map.setdefault(r["employee"], r["leave_type"])

	wo_lists = set()
	hlists = tuple({m["holiday_list"] for m in scope_meta if m["holiday_list"]})
	if hlists:
		wo_lists = {
			r["parent"] for r in frappe.db.sql(
				"SELECT DISTINCT parent FROM `tabHoliday` "
				"WHERE parent IN %(lists)s AND holiday_date = %(d)s AND weekly_off = 1",
				{"lists": hlists, "d": day}, as_dict=True,
			)
		}

	def _meta_row(m, status, leave_type=""):
		return {
			"employee": m["name"],
			"employee_number": m.get("attendance_device_id") or "",
			"employee_name": m.get("employee_name") or m["name"],
			"shift": leave_type or m.get("default_shift") or "",
			"designation": m.get("designation") or "",
			"check_in": "", "check_out": "", "worked_hours": "",
			"status": status,
		}

	absent_rows, leave_rows, weekoff_rows = [], [], []
	for m in scope_meta:
		emp = m["name"]
		if emp in present_ids:
			continue
		if emp in leave_map:
			leave_rows.append(_meta_row(m, _leave_initials(leave_map[emp]), leave_type=leave_map[emp]))
		elif m["holiday_list"] in wo_lists:
			weekoff_rows.append(_meta_row(m, "WO"))
		else:
			absent_rows.append(_meta_row(m, "Absent"))

	# Check-in / check-out head-counts from the SAME overnight-aware punch set that
	# feeds the table, so the Check-Ins / Check-Outs tiles stay in step with the
	# selected date and the rows shown (not a separate same-day-only count).
	checked_in = len(result_rows)
	checked_out = sum(1 for r in result_rows if r.get("check_out"))

	return {
		"date": str(day),
		"rows": present_rows,
		"attendance": {
			"checked_in": checked_in,
			"checked_out": checked_out,
			"present": len(present_ids),
			"absent": len(absent_rows),
			"leave": len(leave_rows),
			"weekly_off": len(weekoff_rows),
			"active_on_date": len(scope_meta),
		},
		"categories": {
			"present": present_rows,
			"absent": absent_rows[:limit],
			"leave": leave_rows[:limit],
			"weekly_off": weekoff_rows[:limit],
		},
	}


def _month_day(year, month, day):
	"""Date for (year, month, day), clamping day to the month's last day."""
	last = calendar.monthrange(year, month)[1]
	return getdate(f"{year}-{month:02d}-{min(int(day), last):02d}")


def _default_payroll_range():
	"""Payroll window rolls automatically each month from the configured day-of-month
	values on Biometric Setting: `from` = start day taken in the PREVIOUS month,
	`to` = end day taken in the CURRENT month (relative to today). Falls back to the
	23rd of last month .. 22nd of this month when the days are not set."""
	def _day(v):
		try:
			n = int(str(v).strip())
			return n if 1 <= n <= 31 else None
		except (ValueError, TypeError):
			return None

	from_day = _day(frappe.db.get_single_value("Biometric Setting", "from"))
	to_day = _day(frappe.db.get_single_value("Biometric Setting", "to"))
	today = getdate(nowdate())
	py = today.year if today.month > 1 else today.year - 1
	pm = today.month - 1 if today.month > 1 else 12

	if from_day and to_day:
		return _month_day(py, pm, from_day), _month_day(today.year, today.month, to_day)
	return _month_day(py, pm, 23), _month_day(today.year, today.month, 22)


@frappe.whitelist()
def get_ta_dashboard_employee_grid(employee, from_date=None, to_date=None):
	"""Per-day attendance grid for one employee over a date range (defaults to the
	payroll window). Each day is P / A / WO / WFH / leave-initials / '' (no record)."""
	emp = frappe.db.get_value(
		"Employee", employee,
		["name", "employee_name", "attendance_device_id", "designation",
		 "default_shift", "holiday_list"],
		as_dict=True,
	)
	if not emp:
		frappe.throw("Employee not found")

	def_start, def_end = _default_payroll_range()
	start = getdate(from_date) if from_date else def_start
	end = getdate(to_date) if to_date else def_end
	if start > end:
		start, end = end, start
	if (end - start).days > 92:
		start = add_days(end, -92)

	days = []
	d = start
	while d <= end:
		days.append(d)
		d = add_days(d, 1)

	att = {}
	for r in frappe.db.sql(
		"SELECT attendance_date, status, leave_type FROM `tabAttendance` "
		"WHERE docstatus < 2 AND employee = %(e)s "
		"AND attendance_date BETWEEN %(s)s AND %(t)s",
		{"e": emp.name, "s": start, "t": end}, as_dict=True,
	):
		st = r["status"]
		if st == "Present":
			code = "P"
		elif st == "Absent":
			code = "A"
		elif st == "Half Day":
			code = "HD"
		elif st == "Work From Home":
			code = "WFH"
		elif st == "On Leave":
			code = _leave_initials(r["leave_type"])
		else:
			code = ""
		att[str(r["attendance_date"])] = code

	wo = set()
	if emp.holiday_list:
		for r in frappe.db.sql(
			"SELECT holiday_date FROM `tabHoliday` "
			"WHERE parent = %(p)s AND weekly_off = 1 "
			"AND holiday_date BETWEEN %(s)s AND %(t)s",
			{"p": emp.holiday_list, "s": start, "t": end}, as_dict=True,
		):
			wo.add(str(r["holiday_date"]))

	codes = []
	counts = {"present": 0, "absent": 0, "weekly_off": 0, "leave": 0, "wfh": 0}
	for d in days:
		ds = str(d)
		code = att.get(ds, "")
		if not code and ds in wo:
			code = "WO"
		codes.append(code)
		if code == "P":
			counts["present"] += 1
		elif code == "A":
			counts["absent"] += 1
		elif code == "WO":
			counts["weekly_off"] += 1
		elif code == "WFH":
			counts["wfh"] += 1
		elif code:
			counts["leave"] += 1

	return {
		"employee": emp.name,
		"employee_name": emp.employee_name or emp.name,
		"employee_number": emp.attendance_device_id or "",
		"designation": emp.designation or "",
		"shift": emp.default_shift or "",
		"start": str(start),
		"end": str(end),
		"days": [frappe.utils.formatdate(x, "d EEE") for x in days],
		"daysfull": [str(x) for x in days],
		"codes": codes,
		"counts": counts,
	}


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
