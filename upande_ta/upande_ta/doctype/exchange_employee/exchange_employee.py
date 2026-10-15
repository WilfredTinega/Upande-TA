# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Someone on an exchange program: employed at their home instance, working
here (or anywhere) for a while. Never an Employee here. See
``upande_ta.upande_ta.exchange``."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, now_datetime

from upande_ta.upande_ta import exchange


class ExchangeEmployee(Document):
	def onload(self):
		self.set_onload("my_code", exchange.my_code())

	def validate(self):
		self.payroll_number = (self.payroll_number or "").strip()
		self.device_pin = (self.device_pin or "").strip() or None
		if self.flags.from_sync:
			return
		if not exchange.is_own(self):
			# received from where it was made: only the local fields (company
			# here, PIN, devices) are edited on this instance
			self.validate_device_pin()
			return

		if not self.source_instance:
			self.source_instance = exchange.my_code() or None
		if not self.origin_id and self.source_instance:
			self.origin_id = f"{self.source_instance}:{self.name}"

		self.home_instance = (self.home_instance or "").strip().upper()
		exchange.get_instance(self.home_instance)
		self.validate_one_per_payroll_number()
		if self.needs_fetch():
			self.fetch_quietly()
		if not self.device_pin and self.payroll_number:
			self.device_pin = self.payroll_number
		self.validate_device_pin()
		self.validate_program_dates()

	def on_update(self):
		if not self.flags.from_sync:
			exchange.queue_sync(self)

	# ──────────────────────────────────────────────────────────────────

	def validate_one_per_payroll_number(self):
		other = frappe.db.get_value(
			"Exchange Employee",
			{
				"home_instance": self.home_instance,
				"payroll_number": self.payroll_number,
				"name": ["!=", self.name],
			},
		)
		if other:
			frappe.throw(
				_("{0} payroll number {1} is already {2}.").format(
					self.home_instance,
					frappe.bold(self.payroll_number),
					frappe.get_desk_link("Exchange Employee", other),
				)
			)

	def needs_fetch(self) -> bool:
		if self.flags.from_refresh or not (self.home_instance and self.payroll_number):
			return False
		if self.link_status != "Linked":
			return True
		before = self.get_doc_before_save()
		return bool(
			before
			and (before.home_instance != self.home_instance or before.payroll_number != self.payroll_number)
		)

	def fetch_quietly(self):
		"""Fetch on save without blocking it when home cannot be reached:
		the record is kept, unlinked, and the form says why."""
		try:
			if not exchange.fetch_home_record(self):
				frappe.msgprint(
					_("{0} has no employee with payroll number {1}.").format(
						self.home_instance, frappe.bold(self.payroll_number)
					),
					indicator="orange",
					title=_("Not Found"),
				)
		except exchange.RemoteError as e:
			self.link_status = "Not Linked"
			frappe.msgprint(
				_("Saved without their details: {0}. Press Fetch from Home once {1} answers.").format(
					e, self.home_instance
				),
				indicator="orange",
				title=_("Home Not Reached"),
			)

	def validate_device_pin(self):
		if not self.device_pin:
			return
		employee = frappe.db.get_value("Employee", {"attendance_device_id": self.device_pin}, "name")
		if employee:
			frappe.throw(
				_("Device PIN {0} already belongs to {1} here. Give {2} another PIN.").format(
					frappe.bold(self.device_pin),
					frappe.get_desk_link("Employee", employee),
					self.full_name or _("this person"),
				)
			)

	def validate_program_dates(self):
		if (
			self.program_start
			and self.program_end
			and getdate(self.program_end) < getdate(self.program_start)
		):
			frappe.throw(_("Program End cannot be before Program Start."))

	# ──────────────────────────────────────────────────────────────────
	# Buttons
	# ──────────────────────────────────────────────────────────────────

	@frappe.whitelist()
	def fetch_home_details(self, reset_host: int = 0) -> dict:
		self.check_permission("write")
		try:
			found = exchange.fetch_home_record(self, reset_host=bool(cint(reset_host)))
		except exchange.RemoteError as e:
			exchange._throw_remote(e)
		self.flags.from_refresh = True
		self.save()
		return {"found": found}

	@frappe.whitelist()
	def device_choices(self) -> list:
		on = {d.device_sn: d for d in self.devices}
		return [
			{
				"device_sn": d.device_sn,
				"device_location": d.device_location,
				"online": d.status == "Online",
				"on_device": bool(on.get(d.device_sn) and cint(on[d.device_sn].on_device)),
			}
			for d in exchange.registered_devices()
		]

	@frappe.whitelist()
	def enroll_on_devices(self, devices) -> dict:
		self.check_permission("write")
		exchange._require_device_commands()
		devices = self._devices_arg(devices)
		if not self.device_pin:
			frappe.throw(_("Set a Device PIN first."))
		templates = 0
		for device_sn in devices:
			templates += exchange.push_to_device(self, device_sn)
			self._mark_device(device_sn, _("Enrolled"), on_device=1)
		self.save()
		return {"devices": len(devices), "templates": templates}

	@frappe.whitelist()
	def update_on_devices(self) -> dict:
		self.check_permission("write")
		exchange._require_device_commands()
		devices = [d.device_sn for d in self.devices if cint(d.on_device)]
		if not devices:
			frappe.throw(_("{0} is not on any device yet. Use Enroll on Devices.").format(self.full_name))
		templates = 0
		for device_sn in devices:
			templates += exchange.push_to_device(self, device_sn)
			self._mark_device(device_sn, _("Updated"), on_device=1)
		self.save()
		return {"devices": len(devices), "templates": templates}

	@frappe.whitelist()
	def delete_from_devices(self, devices=None) -> dict:
		self.check_permission("write")
		exchange._require_device_commands()
		devices = (
			self._devices_arg(devices)
			if devices
			else [d.device_sn for d in self.devices if cint(d.on_device)]
		)
		for device_sn in devices:
			exchange.delete_from_device(self, device_sn)
			self._mark_device(device_sn, _("Deleted"), on_device=0)
		self.save()
		return {"devices": len(devices)}

	@frappe.whitelist()
	def fetch_templates(self, device_sn: str) -> dict:
		"""Ask a device for this person's templates; they arrive through
		store_biotemplate and are kept in Stored Templates."""
		self.check_permission("write")
		exchange._require_device_commands()
		from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import request_biodata_internal

		if not self.device_pin:
			frappe.throw(_("Set a Device PIN first."))
		self._devices_arg([device_sn])
		result = request_biodata_internal(device_sn, self.device_pin)
		self._mark_device(device_sn, _("Templates requested"))
		self.save()
		return result

	def _devices_arg(self, devices) -> list:
		devices = frappe.parse_json(devices) if isinstance(devices, str) else (devices or [])
		known = {d.device_sn for d in exchange.registered_devices()}
		unknown = [d for d in devices if d not in known]
		if unknown:
			frappe.throw(_("Not a device in Biometric Setting: {0}").format(", ".join(unknown)))
		if not devices:
			frappe.throw(_("Pick at least one device."))
		return list(dict.fromkeys(devices))

	def _mark_device(self, device_sn, action, on_device=None):
		row = next((d for d in self.devices if d.device_sn == device_sn), None)
		if not row:
			row = self.append("devices", {"device_sn": device_sn})
		row.device_location = exchange.device_location(device_sn)
		row.last_action = action
		row.last_action_on = now_datetime()
		row.result = "Queued"
		if on_device is not None:
			row.on_device = on_device
