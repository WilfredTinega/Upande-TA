# Copyright (c) 2026, Upande LTD and contributors
"""Override of the HRMS Overtime Slip.

Bulk Overtime generates one Overtime Slip per employee and pre-computes the pay
amount (base / 199.33 x multiplier x hours) which it stashes on each detail row
(``custom_salary_component`` / ``custom_amount``). For those slips we bypass the
native Overtime-Type amount engine and just pay out the stashed amounts; standard,
manually-created Overtime Slips (no ``custom_bulk_overtime`` link) keep 100% of the
native behaviour.
"""

import frappe
from frappe.utils import flt, getdate

from hrms.hr.doctype.overtime_slip.overtime_slip import OvertimeSlip


class UpandeOvertimeSlip(OvertimeSlip):
	def _is_bulk(self):
		return bool(self.get("custom_bulk_overtime"))

	def validate(self):
		if not self._is_bulk():
			return super().validate()
		# Bulk-generated: amounts are pre-computed, and the two summary rows may share
		# the period's To date, so skip the native duplicate-date / overtime-type /
		# max-hours checks. Keep the sane guards.
		if self.start_date and self.end_date and getdate(self.start_date) > getdate(self.end_date):
			frappe.throw(frappe._("Start date cannot be greater than end date"))
		self.validate_overlap()
		self.total_overtime_duration = sum(flt(d.overtime_duration) for d in self.overtime_details)

	def on_submit(self):
		if not self._is_bulk():
			return super().on_submit()
		self._create_bulk_additional_salary()

	def on_cancel(self):
		# Cancel the Additional Salary this slip generated (native slip has no
		# on_cancel, so only act on our bulk-generated slips).
		if not self._is_bulk():
			return
		names = frappe.get_all(
			"Additional Salary",
			filters={"ref_doctype": "Overtime Slip", "ref_docname": self.name, "docstatus": 1},
			pluck="name",
		)
		for name in names:
			frappe.get_doc("Additional Salary", name).cancel()

	def _create_bulk_additional_salary(self):
		totals = {}
		for d in self.overtime_details:
			component = d.get("custom_salary_component")
			amount = flt(d.get("custom_amount"))
			if component and amount > 0:
				totals[component] = totals.get(component, 0) + amount

		for component, amount in totals.items():
			ad = frappe.get_doc({
				"doctype": "Additional Salary",
				"company": self.company,
				"employee": self.employee,
				"salary_component": component,
				"amount": round(amount, 2),
				"payroll_date": self.end_date,
				"overwrite_salary_structure_amount": 0,
				"ref_doctype": "Overtime Slip",
				"ref_docname": self.name,
			})
			ad.insert(ignore_permissions=True)
			ad.submit()

		if totals:
			frappe.msgprint(
				frappe._("{0} Additional Salary record(s) created from Overtime Slip {1}.").format(
					len(totals), self.name
				),
				indicator="green",
				alert=True,
			)
