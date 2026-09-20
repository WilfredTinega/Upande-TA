# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Overtime Request — the approval that comes before overtime is worked.

A supervisor lists who is to work overtime on one date and for how many hours.
Submitting the request is the approval: only roles with submit permission
(HR Manager by default) can do it. Bulk Overtime later pays each approved row
against what the attendance shows was actually worked.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form


class OvertimeRequest(Document):
	def validate(self):
		self.validate_employees()
		self.set_totals()

	def before_submit(self):
		self.validate_not_already_requested()

	def validate_employees(self):
		if not self.employees:
			frappe.throw(_("Add at least one employee."))

		seen = set()
		for row in self.employees:
			if row.employee in seen:
				frappe.throw(
					_("Row #{0}: {1} is added more than once.").format(row.idx, frappe.bold(row.employee_name or row.employee))
				)
			seen.add(row.employee)

			if flt(row.requested_hours) <= 0:
				frappe.throw(
					_("Row #{0}: Requested Hours for {1} must be more than 0.").format(
						row.idx, frappe.bold(row.employee_name or row.employee)
					)
				)

		other_company = frappe.get_all(
			"Employee",
			filters={"name": ["in", list(seen)], "company": ["!=", self.company]},
			pluck="employee_name",
		)
		if other_company:
			frappe.throw(
				_("These employees are not in {0}: {1}").format(
					frappe.bold(self.company), ", ".join(frappe.bold(n) for n in other_company[:20])
				)
			)

	def validate_not_already_requested(self):
		"""One approved request per employee per date: Bulk Overtime pays a row
		once, so a second approval for the same day would be ambiguous."""
		Request = frappe.qb.DocType("Overtime Request")
		Row = frappe.qb.DocType("Overtime Request Employee")
		clashes = (
			frappe.qb.from_(Row)
			.join(Request)
			.on(Request.name == Row.parent)
			.select(Row.employee, Row.employee_name, Request.name)
			.where(
				(Request.docstatus == 1)
				& (Request.overtime_date == self.overtime_date)
				& (Request.name != self.name)
				& (Row.parenttype == "Overtime Request")
				& (Row.employee.isin([row.employee for row in self.employees]))
			)
			.run(as_dict=True)
		)
		if clashes:
			lines = "<br>".join(
				"{0}: {1}".format(frappe.bold(c.employee_name or c.employee), get_link_to_form("Overtime Request", c.name))
				for c in clashes[:20]
			)
			frappe.throw(
				_("Already approved for overtime on {0}. Remove them or cancel the other request:<br>{1}").format(
					frappe.bold(frappe.format(self.overtime_date, "Date")), lines
				),
				title=_("Already Requested"),
			)

	def set_totals(self):
		self.number_of_employees = len(self.employees)
		self.total_requested_hours = sum(flt(row.requested_hours) for row in self.employees)
