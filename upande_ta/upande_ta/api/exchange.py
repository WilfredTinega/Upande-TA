# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""What the other linked instances (and Node-RED) call on this one.

Every endpoint needs a signed-in caller: the integration user made by
Exchange Setting → Generate API Key, whose token the other instance sends.
See ``upande_ta.upande_ta.exchange`` for how the pieces fit.
"""

import frappe
from frappe import _

from upande_ta.upande_ta import exchange


@frappe.whitelist(methods=["GET"])
def ping() -> dict:
	exchange.require_sync_caller()
	return {"instance_code": exchange.my_code()}


@frappe.whitelist(methods=["GET"])
def get_employee(payroll_number: str, company: str | None = None) -> dict | None:
	"""Who has this payroll number here (in ``company``, when given), for an
	instance hosting them."""
	exchange.require_sync_caller()
	if not frappe.utils.cint(exchange.settings().allow_employee_lookup):
		frappe.throw(_("This instance does not share its employees."), frappe.PermissionError)
	return exchange.employee_for_lookup(payroll_number, company)


@frappe.whitelist(methods=["GET"])
def get_companies() -> list:
	exchange.require_sync_caller()
	return frappe.get_all("Company", pluck="name", order_by="name asc")


@frappe.whitelist(methods=["POST"])
def receive(doctype: str, data: dict | str) -> dict:
	"""An exchange employee or punch made on another instance."""
	exchange.require_sync_caller()
	return exchange.receive(doctype, frappe.parse_json(data))


@frappe.whitelist(methods=["POST"])
def device_punch(pin: str, time: str, device_sn: str | None = None, log_type: str | None = None) -> dict:
	"""For Node-RED: a scan whose PIN is no Employee's. Saved as an Exchange
	Checkin when the PIN belongs to an active exchange employee."""
	exchange.require_sync_caller("HR Manager", "HR User")
	name = exchange.record_device_punch(pin, time, device_sn=device_sn, log_type=log_type)
	return {"status": "saved" if name else "not an exchange employee", "name": name}
