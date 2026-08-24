# Copyright (c) 2026, Upande LTD and contributors
"""Add the Gate Pass approver table to Department.

Reuses the native `Department Approver` child DocType, the same pattern HRMS
uses for `leave_approvers` / `expense_approvers`, so HOD routing for Gate
Passes is configured in the place people already look.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	# `expense_approvers` is itself an HRMS custom field (hrms/setup.py), not a
	# core Department field. hrms is a required_app so it is always present,
	# but fall back to the field above it rather than orphan the anchor.
	anchor = "expense_approvers"
	if not frappe.db.exists("Custom Field", {"dt": "Department", "fieldname": anchor}):
		anchor = "leave_approvers"

	create_custom_fields(
		{
			"Department": [
				{
					"fieldname": "custom_gate_pass_approvers",
					"label": "Gate Pass Approver",
					"fieldtype": "Table",
					"options": "Department Approver",
					"insert_after": anchor,
					"description": (
						"The first Approver in the list is the default Head of Department "
						"for Gate Passes."
					),
				}
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
