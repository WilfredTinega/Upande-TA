# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""One punch by an exchange employee, from a device here or from another
instance. Kept apart from Employee Checkin so attendance and payroll never
count it.

A person has one punch per date and time: the same time sent again — by the
device, by another instance, or typed in — is refused, whatever its log type,
and a unique key on (exchange_employee, time) holds that even when two copies
arrive at once."""

import frappe
from frappe import _
from frappe.model.document import Document

from upande_ta.upande_ta import exchange

#: The unique key that keeps one punch per person per date and time.
UNIQUE_PUNCH = "unique_exchange_employee_time"


def on_doctype_update():
	frappe.db.add_unique("Exchange Checkin", ["exchange_employee", "`time`"], constraint_name=UNIQUE_PUNCH)


class ExchangeCheckin(Document):
	def insert(self, *args, **kwargs):
		existing = self.take_device_payload()
		if existing:
			return frappe.get_doc("Exchange Checkin", existing)
		try:
			return super().insert(*args, **kwargs)
		except frappe.UniqueValidationError:
			# the same punch was saved a moment ago by another request
			frappe.clear_last_message()
			self.throw_duplicate(
				exchange.same_punch(
					"Exchange Checkin", "exchange_employee", self.exchange_employee, self.time
				)
			)

	def take_device_payload(self):
		"""Node-RED posts the device's own punch: the PIN in ``employee`` and
		the device in ``device_id``. Finds the Exchange or HQ Employee by that
		PIN or payroll number; returns the same punch when it is already kept."""
		if self.flags.from_sync or self.exchange_employee:
			return None
		pin = str(self.employee or self.payroll_number or "").strip()
		if not pin:
			return None
		person = exchange.person_by_pin(pin, fields=("name",))
		if not person:
			frappe.throw(
				_("No active Exchange or HQ Employee has the device PIN or payroll number {0}.").format(pin)
			)
		self.person_doctype = person.doctype
		self.exchange_employee = person.name
		if not frappe.db.exists("Employee", self.employee):
			self.employee = None
		self.device_sn = self.device_sn or self.get("device_id")
		self.log_type = (self.log_type or "").strip().upper() or None
		if self.log_type not in (None, "IN", "OUT"):
			self.log_type = None
		self.arrived_via = "Device"
		return exchange.same_punch("Exchange Checkin", "exchange_employee", person.name, self.time)

	def validate(self):
		if self.flags.from_sync:
			self.validate_duplicate()
			return
		self.person_doctype = self.person_doctype or "Exchange Employee"
		if self.person_doctype not in exchange.PERSON_DOCTYPES:
			frappe.throw(_("Employee Type must be one of {0}.").format(", ".join(exchange.PERSON_DOCTYPES)))
		if not self.exchange_employee:
			frappe.throw(_("Pick the Employee."))
		fields = ["full_name", "payroll_number", "home_instance", "host_company", "origin_id"]
		employee = frappe.db.get_value(self.person_doctype, self.exchange_employee, fields, as_dict=True)
		if not employee:
			# the Employee Type was left at its default for the other kind
			for doctype in exchange.PERSON_DOCTYPES:
				employee = frappe.db.get_value(doctype, self.exchange_employee, fields, as_dict=True)
				if employee:
					self.person_doctype = doctype
					break
		if not employee:
			frappe.throw(_("{0} {1} not found.").format(self.person_doctype, self.exchange_employee))
		self.employee_name = employee.full_name
		self.payroll_number = employee.payroll_number
		self.home_instance = employee.home_instance
		self.employee_origin_id = employee.origin_id
		self.company = self.company or employee.host_company
		if self.device_sn and not self.device_location:
			self.device_location = exchange.device_location(self.device_sn)
		if not self.source_instance:
			self.source_instance = exchange.my_code() or None
		if not self.origin_id and self.source_instance:
			self.origin_id = f"{self.source_instance}:{self.name}"
		self.validate_duplicate()

	def validate_duplicate(self):
		if not (self.exchange_employee and self.time):
			return
		other = frappe.db.get_value(
			"Exchange Checkin",
			{"exchange_employee": self.exchange_employee, "time": self.time, "name": ["!=", self.name]},
		)
		if other:
			self.throw_duplicate(other)

	def throw_duplicate(self, other):
		frappe.throw(
			_("{0} already has a punch at {1}: {2}.").format(
				self.employee_name or self.exchange_employee,
				frappe.format(self.time, "Datetime"),
				frappe.get_desk_link("Exchange Checkin", other) if other else "",
			),
			exc=frappe.DuplicateEntryError,
			title=_("Duplicate Punch"),
		)

	def after_insert(self):
		if self.flags.from_sync:
			return
		if self.home_instance and self.home_instance == exchange.my_code():
			# one of our own employees: their attendance here counts it too
			exchange.link_employee_checkin(self.name, exchange.make_employee_checkin_quietly(self))
		exchange.queue_sync(self)
