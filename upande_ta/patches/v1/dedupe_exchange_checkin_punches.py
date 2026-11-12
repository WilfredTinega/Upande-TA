# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Exchange Checkin keeps one punch per person per date and time, held by a
unique key on (exchange_employee, time) that the doctype sync adds. Copies
already saved twice would stop that key being made, so they go first: of each
set, the one linked to an Employee Checkin stays, else the first saved.

Runs before the model sync, which is when the key is added."""

import frappe


def execute():
	if not frappe.db.table_exists("Exchange Checkin"):
		return

	# the Employee Checkin link came in a later version than the table
	linked_first = (
		"IFNULL(k.employee_checkin, '') = '', "
		if frappe.db.has_column("Exchange Checkin", "employee_checkin")
		else ""
	)
	extra = frappe.db.sql(
		f"""
		SELECT c.name
		FROM `tabExchange Checkin` c
		JOIN (
			SELECT exchange_employee, `time`
			FROM `tabExchange Checkin`
			WHERE IFNULL(exchange_employee, '') != ''
			GROUP BY exchange_employee, `time`
			HAVING COUNT(*) > 1
		) d ON d.exchange_employee = c.exchange_employee AND d.`time` = c.`time`
		WHERE c.name != (
			SELECT k.name
			FROM `tabExchange Checkin` k
			WHERE k.exchange_employee = c.exchange_employee AND k.`time` = c.`time`
			ORDER BY {linked_first}k.creation, k.name
			LIMIT 1
		)
		""",
		pluck=True,
	)
	for name in extra:
		frappe.delete_doc(
			"Exchange Checkin", name, ignore_permissions=True, force=True, delete_permanently=True
		)
