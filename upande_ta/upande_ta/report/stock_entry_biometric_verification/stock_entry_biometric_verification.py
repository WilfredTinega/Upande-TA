# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	report_summary = get_report_summary(data)
	return columns, data, None, None, report_summary


def get_columns():
	return [
		{"label": _("Stock Entry"), "fieldname": "name", "fieldtype": "Link", "options": "Stock Entry", "width": 140},
		{"label": _("Status"), "fieldname": "docstatus_label", "fieldtype": "Data", "width": 90},
		{
			"label": _("Stock Entry Type"),
			"fieldname": "stock_entry_type",
			"fieldtype": "Link",
			"options": "Stock Entry Type",
			"width": 150,
		},
		{"label": _("Purpose"), "fieldname": "purpose", "fieldtype": "Data", "width": 110},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
		{
			"label": _("Employee (Receiving)"),
			"fieldname": "bio_employee",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 110,
		},
		{"label": _("Employee Name"), "fieldname": "bio_employee_name", "fieldtype": "Data", "width": 140},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Department", "width": 130},
		{
			"label": _("Issued via Biometric"),
			"fieldname": "issued_via_biometric",
			"fieldtype": "Data",
			"width": 130,
		},
		{"label": _("Verification Status"), "fieldname": "biometric_status", "fieldtype": "Data", "width": 110},
		{"label": _("Verified At"), "fieldname": "biometric_verified_at", "fieldtype": "Datetime", "width": 160},
		{
			"label": _("Matched Biometric Log"),
			"fieldname": "matched_biometric_log",
			"fieldtype": "Link",
			"options": "Biometric Logs",
			"width": 160,
		},
	]


DOCSTATUS_LABELS = {0: "Draft", 1: "Submitted", 2: "Cancelled"}


def get_data(filters):
	conditions, values = get_conditions(filters)

	rows = frappe.db.sql(
		f"""
		select
			name, docstatus, stock_entry_type, purpose, posting_date, company,
			bio_employee, bio_employee_name, department,
			biometric_status, biometric_verified_at, matched_biometric_log
		from `tabStock Entry`
		where requires_biometric = 1
			{conditions}
		order by posting_date desc, creation desc
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row.docstatus_label = DOCSTATUS_LABELS.get(row.docstatus, row.docstatus)
		row.issued_via_biometric = "Yes" if row.biometric_status == "Verified" else "No"

	return rows


def get_conditions(filters):
	conditions = []
	values = {}

	if filters.get("company"):
		conditions.append("company = %(company)s")
		values["company"] = filters.company

	if filters.get("stock_entry_type"):
		conditions.append("stock_entry_type = %(stock_entry_type)s")
		values["stock_entry_type"] = filters.stock_entry_type

	if filters.get("from_date"):
		conditions.append("posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date

	if filters.get("biometric_status"):
		conditions.append("biometric_status = %(biometric_status)s")
		values["biometric_status"] = filters.biometric_status

	if not frappe.utils.cint(filters.get("include_cancelled")):
		conditions.append("docstatus != 2")

	return ("and " + " and ".join(conditions) if conditions else ""), values


def get_report_summary(data):
	total = len(data)
	verified = len([d for d in data if d.biometric_status == "Verified"])
	not_verified = total - verified

	return [
		{"label": _("Total Stock Entries"), "value": total, "indicator": "Blue"},
		{"label": _("Issued via Biometric"), "value": verified, "indicator": "Green"},
		{"label": _("Not Verified"), "value": not_verified, "indicator": "Red" if not_verified else "Green"},
	]
