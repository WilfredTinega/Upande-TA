# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, date_diff, getdate


class BulkOvertime(Document):
	def validate(self):
		self.number_of_employees = len(self.bulk_overtime_entries or [])

	@frappe.whitelist()
	def fill_employee_details(self):
		"""
		Fetch active employees matching the filter criteria (company, branch,
		department, designation, grade) who were active during the selected date
		range, then populate the Bulk Overtime Entries child table.

		Mirrors the behaviour of Payroll Entry → Get Employees but without any
		salary-structure or payroll-frequency dependency.
		"""
		self._validate_mandatory_filters()

		employees = self._get_matching_employees()

		if not employees:
			error_msg = _("No active employees found for the selected criteria.")
			if self.company:
				error_msg += "<br>" + _("Company: {0}").format(frappe.bold(self.company))
			if self.branch:
				error_msg += "<br>" + _("Branch: {0}").format(frappe.bold(self.branch))
			if self.department:
				error_msg += "<br>" + _("Department: {0}").format(frappe.bold(self.department))
			if self.designation:
				error_msg += "<br>" + _("Designation: {0}").format(frappe.bold(self.designation))
			if self.grade:
				error_msg += "<br>" + _("Grade: {0}").format(frappe.bold(self.grade))
			if self.from_date:
				error_msg += "<br>" + _("From Date: {0}").format(frappe.bold(self.from_date))
			if self.to_date:
				error_msg += "<br>" + _("To Date: {0}").format(frappe.bold(self.to_date))
			frappe.throw(error_msg, title=_("No Employees Found"))

		# Rebuild the child table
		self.set("bulk_overtime_entries", [])
		from_date = getdate(self.from_date)
		total_days = date_diff(self.to_date, self.from_date) + 1

		for emp in employees:
			for day_offset in range(total_days):
				overtime_date = add_days(from_date, day_offset)
				self.append(
					"bulk_overtime_entries",
					{
						"employee": emp.employee,
						"employee_name": emp.employee_name,
						"department": emp.department,
						"overtime_date": overtime_date,
						"overtime_type": None,
						"hours_requested": self.default_requested_hours or 0,
						"row_status": "Pending",
					},
				)

		self.number_of_employees = len(self.bulk_overtime_entries)

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	def _validate_mandatory_filters(self):
		"""Raise a clear error if required header fields are missing."""
		missing = []
		for fieldname in ("company", "from_date", "to_date"):
			if not self.get(fieldname):
				missing.append(frappe.unscrub(fieldname))

		if missing:
			frappe.throw(
				_("Please fill in the following fields before fetching employees: {0}").format(
					", ".join(frappe.bold(f) for f in missing)
				),
				title=_("Missing Fields"),
			)

		if self.from_date and self.to_date and self.from_date > self.to_date:
			frappe.throw(_("From Date cannot be after To Date."))

	def _get_matching_employees(self):
		"""
		Return a list of dicts with keys: employee, employee_name, department.

		Conditions:
		  - Employee is active (status != Inactive)
		  - Employee belongs to the selected company
		  - Employee joined on or before to_date
		  - Employee relieving_date is null or >= from_date
		  - Optional filters: branch, department, designation, grade
		"""
		Employee = frappe.qb.DocType("Employee")

		query = (
			frappe.qb.from_(Employee)
			.select(
				Employee.name.as_("employee"),
				Employee.employee_name,
				Employee.department,
				Employee.designation,
				Employee.branch,
				Employee.grade,
			)
			.where(
				(Employee.status != "Inactive")
				& (Employee.company == self.company)
				& (
					(Employee.date_of_joining <= self.to_date)
					| Employee.date_of_joining.isnull()
				)
				& (
					(Employee.relieving_date >= self.from_date)
					| Employee.relieving_date.isnull()
				)
			)
			.orderby(Employee.employee_name)
		)

		# Optional dimension filters
		for field in ("branch", "department", "designation", "grade"):
			value = self.get(field)
			if value:
				query = query.where(Employee[field] == value)

		return query.run(as_dict=True)
