# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from upande_ta.upande_ta.doctype.bulk_week_off.bulk_week_off import employee_has_farm, get_farms


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class IntegrationTestBulkWeekOff(IntegrationTestCase):
	"""
	Integration tests for BulkWeekOff.
	Use this class for testing interactions between multiple components.
	"""

	def employee_with_farm(self):
		if not employee_has_farm():
			self.skipTest("this site's employees have no farm")
		row = frappe.db.get_value("Employee", {"status": "Active", "custom_farm": ["is", "set"]},
		                          ["company", "custom_farm"], as_dict=True)
		if not row:
			self.skipTest("no active employee has a farm")
		return row

	def test_farm_filter_keeps_only_that_farms_employees(self):
		row = self.employee_with_farm()
		doc = frappe.get_doc({"doctype": "Bulk Week Off", "company": row.company, "farm": row.custom_farm})
		got = doc.get_employees()
		self.assertTrue(got)
		farms = set(frappe.get_all("Employee", filters={"name": ["in", [e.employee for e in got]]}, pluck="custom_farm"))
		self.assertEqual(farms, {row.custom_farm})

	def test_no_farm_means_every_farm(self):
		row = self.employee_with_farm()
		every = frappe.get_doc({"doctype": "Bulk Week Off", "company": row.company}).get_employees()
		one = frappe.get_doc({"doctype": "Bulk Week Off", "company": row.company, "farm": row.custom_farm}).get_employees()
		self.assertGreaterEqual(len(every), len(one))
		farms = set(frappe.get_all("Employee", filters={"name": ["in", [e.employee for e in every]]}, pluck="custom_farm"))
		self.assertTrue(len(farms) >= 1)

	def test_get_farms_lists_the_company_farms(self):
		row = self.employee_with_farm()
		self.assertIn(row.custom_farm, get_farms(row.company))
