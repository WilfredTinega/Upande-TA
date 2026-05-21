import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Employee #"), "fieldname": "employee_number", "fieldtype": "Data", "width": 110},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 140},
		{"label": _("Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 220},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Department", "width": 160},
		{"label": _("Designation"), "fieldname": "designation", "fieldtype": "Link", "options": "Designation", "width": 160},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 160},
		{"label": _("Check-In"), "fieldname": "check_in", "fieldtype": "Datetime", "width": 160},
		{"label": _("Check-Out"), "fieldname": "check_out", "fieldtype": "Datetime", "width": 160},
		{"label": _("Worked Hours"), "fieldname": "worked_hours", "fieldtype": "Duration", "width": 120},
	]


def get_data(filters):
	day = getdate(filters.date) if filters.date else getdate(nowdate())
	from_date = to_date = day

	has_farm = "custom_farm" in frappe.db.get_table_columns("Employee")

	emp_filters = {"status": "Active"}
	if filters.company:
		emp_filters["company"] = filters.company
	if filters.department:
		emp_filters["department"] = filters.department
	if filters.designation:
		emp_filters["designation"] = filters.designation
	if filters.employee:
		emp_filters["name"] = filters.employee
	if filters.farm and has_farm:
		emp_filters["custom_farm"] = filters.farm

	scoped = None
	if any(k in emp_filters for k in ("name", "company", "department", "designation", "custom_farm")):
		scoped = frappe.get_all("Employee", filters=emp_filters, pluck="name")
		if not scoped:
			return []

	params = {"from_date": from_date, "to_date": to_date}
	emp_clause = ""
	if scoped is not None:
		emp_clause = " AND ec.employee IN %(emp_list)s"
		params["emp_list"] = tuple(scoped)

	rows = frappe.db.sql(
		f"""
		SELECT
			ec.employee,
			COALESCE(ec.employee_name, e.employee_name) AS employee_name,
			e.attendance_device_id AS employee_number,
			e.department,
			e.designation,
			e.company,
			DATE(ec.time) AS log_date,
			MIN(CASE WHEN ec.log_type = 'IN'  THEN ec.time END) AS check_in,
			MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.time END) AS check_out
		FROM `tabEmployee Checkin` ec
		LEFT JOIN `tabEmployee` e ON e.name = ec.employee
		WHERE DATE(ec.time) BETWEEN %(from_date)s AND %(to_date)s
		{emp_clause}
		GROUP BY ec.employee, employee_name, employee_number, e.department, e.designation, e.company, DATE(ec.time)
		ORDER BY DATE(ec.time) DESC,
		         MIN(CASE WHEN ec.log_type = 'IN' THEN ec.time END) IS NULL,
		         MIN(CASE WHEN ec.log_type = 'IN' THEN ec.time END) ASC
		""",
		params,
		as_dict=True,
	)

	for r in rows:
		ci, co = r.get("check_in"), r.get("check_out")
		r["worked_hours"] = int((co - ci).total_seconds()) if ci and co and co > ci else 0
	return rows
