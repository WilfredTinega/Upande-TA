# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

CODE = re.compile(r"[A-Z0-9_-]{2,20}")


class ExchangeSetting(Document):
	def onload(self):
		from upande_ta.upande_ta.exchange import sync_status

		self.set_onload("sync_status", sync_status())

	def validate(self):
		self.instance_code = (self.instance_code or "").strip().upper()
		if self.instance_code and not CODE.fullmatch(self.instance_code):
			frappe.throw(_("This Instance Code takes 2 to 20 letters, digits, - or _, e.g. HQ or KAITET."))
		field = (self.payroll_number_field or "").strip() or "attendance_device_id"
		if field != "name" and not frappe.get_meta("Employee").has_field(field):
			frappe.throw(_("Employee has no field {0}.").format(frappe.bold(field)))
		self.payroll_number_field = field
		self.validate_instances()
		self.validate_company_map()

	def validate_instances(self):
		seen = set()
		for row in self.instances:
			row.instance_code = (row.instance_code or "").strip().upper()
			label = _("Instances row #{0}").format(row.idx)
			if not CODE.fullmatch(row.instance_code):
				frappe.throw(
					_("{0}: Instance Code takes 2 to 20 letters, digits, - or _, e.g. KAITET.").format(label)
				)
			if row.instance_code == self.instance_code:
				frappe.throw(
					_("{0}: {1} is this instance. List the other instances here.").format(
						label, row.instance_code
					)
				)
			if row.instance_code in seen:
				frappe.throw(_("{0}: {1} is listed twice.").format(label, row.instance_code))
			seen.add(row.instance_code)
			token = (row.api_token or "").strip()
			if token and set(token) != {"*"}:
				from upande_ta.upande_ta.exchange import split_token

				key, secret = split_token(token)
				if not (key and secret):
					frappe.throw(
						_(
							"{0}: API Key:Secret is the key and the secret joined by a colon, e.g. 4945abedd220db5:f4c3e9bfefadadd"
						).format(label)
					)
				row.api_token = f"{key}:{secret}"
			row.base_url = (row.base_url or "").strip().rstrip("/")
			if not re.match(r"^https?://", row.base_url):
				frappe.throw(
					_(
						"{0}: Base URL starts with https:// (or http://), e.g. https://kaitet.upande.com"
					).format(label)
				)

	def validate_company_map(self):
		codes = {row.instance_code for row in self.instances}
		seen = set()
		for row in self.company_map:
			row.instance = (row.instance or "").strip().upper()
			label = _("Company Map row #{0}").format(row.idx)
			if row.instance not in codes:
				frappe.throw(
					_("{0}: {1} is not in the Instances tab.").format(label, frappe.bold(row.instance))
				)
			key = (row.instance, row.home_company)
			if key in seen:
				frappe.throw(
					_("{0}: {1} on {2} is mapped twice.").format(
						label, frappe.bold(row.home_company), row.instance
					)
				)
			seen.add(key)
