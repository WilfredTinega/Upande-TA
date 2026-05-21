import frappe
from frappe.utils import getdate, nowdate


def _employee_has_custom_farm():
	return "custom_farm" in frappe.db.get_table_columns("Employee")


def _scoped_employees(company, farm, department, designation, employee):
	has_farm = _employee_has_custom_farm()
	if not any([company, farm and has_farm, department, designation, employee]):
		return None
	filters = {}
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


@frappe.whitelist()
def get_ta_dashboard_checkins(date=None, company=None, farm=None,
                              department=None, designation=None, employee=None,
                              limit: int = 5000):
	day = getdate(date) if date else getdate(nowdate())
	limit = max(1, min(int(limit or 5000), 10000))

	scoped = _scoped_employees(company, farm, department, designation, employee)
	if scoped is not None and not scoped:
		return {"date": str(day), "rows": []}

	emp_clause = ""
	params = {"day": day, "limit": limit}
	if scoped is not None:
		emp_clause = " AND ec.employee IN %(emp_list)s"
		params["emp_list"] = tuple(scoped)

	rows = frappe.db.sql(
		f"""
		SELECT
			ec.employee,
			COALESCE(ec.employee_name, e.employee_name) AS employee_name,
			e.attendance_device_id,
			e.designation,
			MAX(ec.shift) AS shift,
			MIN(CASE WHEN ec.log_type = 'IN'  THEN ec.time END) AS check_in,
			MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.time END) AS check_out,
			TIMESTAMPDIFF(
				SECOND,
				MIN(CASE WHEN ec.log_type = 'IN'  THEN ec.time END),
				MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.time END)
			) AS worked_seconds
		FROM `tabEmployee Checkin` ec
		LEFT JOIN `tabEmployee` e ON e.name = ec.employee
		WHERE DATE(ec.time) = %(day)s
		{emp_clause}
		GROUP BY ec.employee, employee_name, e.attendance_device_id, e.designation
		ORDER BY (TIMESTAMPDIFF(
		             SECOND,
		             MIN(CASE WHEN ec.log_type = 'IN'  THEN ec.time END),
		             MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.time END)
		         ) IS NULL OR TIMESTAMPDIFF(
		             SECOND,
		             MIN(CASE WHEN ec.log_type = 'IN'  THEN ec.time END),
		             MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.time END)
		         ) <= 0),
		         TIMESTAMPDIFF(
		             SECOND,
		             MIN(CASE WHEN ec.log_type = 'IN'  THEN ec.time END),
		             MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.time END)
		         ) DESC,
		         MIN(CASE WHEN ec.log_type = 'IN' THEN ec.time END) ASC
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)

	def _fmt(dt):
		return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

	def _worked(ci, co):
		if not ci or not co or co <= ci:
			return ""
		total = int((co - ci).total_seconds())
		return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"

	return {
		"date": str(day),
		"rows": [
			{
				"employee_number": r.get("attendance_device_id") or "",
				"employee_name": r.get("employee_name") or r.get("employee") or "",
				"shift": r.get("shift") or "",
				"designation": r.get("designation") or "",
				"check_in": _fmt(r.get("check_in")),
				"check_out": _fmt(r.get("check_out")),
				"worked_hours": _worked(r.get("check_in"), r.get("check_out")),
			}
			for r in rows
		],
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
