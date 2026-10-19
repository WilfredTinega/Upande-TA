# Copyright (c) 2026, Upande LTD and contributors

"""Exchange Checkin's Employee became a link to either an Exchange Employee or
an HQ Employee; every punch kept so far is an Exchange Employee's."""

import frappe


def execute():
	if not frappe.db.has_column("Exchange Checkin", "person_doctype"):
		return
	frappe.db.sql(
		"""UPDATE `tabExchange Checkin` SET person_doctype = 'Exchange Employee'
		WHERE IFNULL(person_doctype, '') = ''"""
	)
