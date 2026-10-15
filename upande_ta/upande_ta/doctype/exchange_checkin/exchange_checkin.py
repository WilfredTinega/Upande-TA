# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""One punch by an exchange employee, from a device here or from another
instance. Kept apart from Employee Checkin so attendance and payroll never
count it."""

import frappe
from frappe import _
from frappe.model.document import Document

from upande_ta.upande_ta import exchange


class ExchangeCheckin(Document):
	def validate(self):
		if self.flags.from_sync:
			return
		if not self.exchange_employee:
			frappe.throw(_("Pick the Exchange Employee."))
		employee = frappe.db.get_value(
			"Exchange Employee",
			self.exchange_employee,
			["full_name", "payroll_number", "home_instance", "host_company", "origin_id"],
			as_dict=True,
		)
		self.employee_name = employee.full_name
		self.payroll_number = employee.payroll_number
		self.home_instance = employee.home_instance
		self.employee_origin_id = employee.origin_id
		self.company = self.company or employee.host_company
		if not self.source_instance:
			self.source_instance = exchange.my_code() or None
		if not self.origin_id and self.source_instance:
			self.origin_id = f"{self.source_instance}:{self.name}"
		self.validate_duplicate()

	def validate_duplicate(self):
		other = frappe.db.get_value(
			"Exchange Checkin",
			{
				"exchange_employee": self.exchange_employee,
				"time": self.time,
				"log_type": self.log_type or ["is", "not set"],
				"name": ["!=", self.name],
			},
		)
		if other:
			frappe.throw(
				_("{0} already has this punch: {1}.").format(
					self.employee_name, frappe.get_desk_link("Exchange Checkin", other)
				),
				title=_("Duplicate Punch"),
			)

	def after_insert(self):
		if not self.flags.from_sync:
			exchange.queue_sync(self)
