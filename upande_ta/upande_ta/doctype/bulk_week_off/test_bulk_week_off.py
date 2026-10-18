# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Department",
	"Designation",
	"Employee",
	"Employee Transfer",
	"Holiday List",
	"Holiday List Assignment",
]

PREFIX = "_Test BWO"


class IntegrationTestBulkWeekOff(IntegrationTestCase):
	"""The newest week-off entry for an employee wins, whatever made the
	older one: another Bulk Week Off or the Monthly Attendance Sheet."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		gender = frappe.db.get_value("Gender", {}, "name")
		if not (cls.company and gender):
			raise unittest.SkipTest("needs a Company and a Gender")
		cls.today = getdate(today())
		start, end = add_days(cls.today, -60), add_days(cls.today, 300)
		cls.lists = {}
		for weekday in ("Sunday", "Monday", "Tuesday"):
			name = f"{PREFIX} {weekday}"
			if not frappe.db.exists("Holiday List", name):
				doc = frappe.get_doc(
					{
						"doctype": "Holiday List",
						"holiday_list_name": name,
						"from_date": start,
						"to_date": end,
						"weekly_off": weekday,
					}
				)
				doc.get_weekly_off_dates()
				doc.insert(ignore_permissions=True)
			cls.lists[weekday] = name
		cls.employee = (
			frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": f"{PREFIX} Employee",
					"company": cls.company,
					"gender": gender,
					"date_of_birth": "1990-01-01",
					"date_of_joining": add_days(cls.today, -100),
					"status": "Active",
					"holiday_list": cls.lists["Sunday"],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": cls.employee,
				"holiday_list": cls.lists["Sunday"],
				"from_date": start,
			}
		).insert(ignore_permissions=True).submit()

	def bulk(self, weekday, from_date):
		doc = frappe.get_doc(
			{
				"doctype": "Bulk Week Off",
				"company": self.company,
				"from_date": from_date,
				"holiday_list": self.lists[weekday],
				"employees": [{"employee": self.employee, "assigned_off_day": self.lists[weekday]}],
			}
		).insert(ignore_permissions=True)
		doc.submit()
		return doc

	def in_force(self, date):
		from upande_ta.upande_ta.holiday_list import get_assigned_holiday_list

		return get_assigned_holiday_list(self.employee, as_on=date)

	def test_a_second_bulk_week_off_on_the_same_date_wins(self):
		date = add_days(self.today, 10)
		self.bulk("Monday", date)
		self.bulk("Tuesday", date)
		self.assertEqual(self.in_force(date), self.lists["Tuesday"])

	def test_the_sheet_overrides_a_bulk_week_off_starting_inside_its_window(self):
		from upande_ta.upande_ta.week_off_change import change_week_off

		date = add_days(self.today, 10)
		self.bulk("Monday", date)
		result = change_week_off(
			self.employee, str(add_days(self.today, 5)), str(add_days(self.today, 20)), weekdays=["Tuesday"]
		)
		self.assertNotEqual(self.in_force(date), self.lists["Monday"])
		# the Bulk Week Off's list carries on after the window
		self.assertEqual(result["restored_to"], self.lists["Monday"])
		self.assertEqual(self.in_force(add_days(self.today, 21)), self.lists["Monday"])

	def test_cancelling_an_older_bulk_week_off_leaves_the_newer_one(self):
		date = add_days(self.today, 10)
		older = self.bulk("Monday", date)
		self.bulk("Tuesday", date)
		older.reload()
		older.cancel()
		self.assertEqual(self.in_force(date), self.lists["Tuesday"])

	def test_cancelling_an_older_applied_bulk_week_off_keeps_the_employee_field(self):
		date = add_days(self.today, -5)  # already in force: the transfers are submitted
		older = self.bulk("Monday", date)
		self.bulk("Tuesday", date)
		self.assertEqual(
			frappe.db.get_value("Employee", self.employee, "holiday_list"), self.lists["Tuesday"]
		)
		older.reload()
		older.cancel()
		self.assertEqual(
			frappe.db.get_value("Employee", self.employee, "holiday_list"), self.lists["Tuesday"]
		)
		self.assertEqual(self.in_force(date), self.lists["Tuesday"])

	def test_it_applies_when_the_employee_field_already_matches(self):
		date = add_days(self.today, 10)
		frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": self.employee,
				"holiday_list": self.lists["Monday"],
				"from_date": add_days(date, -3),
			}
		).insert(ignore_permissions=True).submit()
		frappe.db.set_value("Employee", self.employee, "holiday_list", self.lists["Tuesday"])
		self.bulk("Tuesday", date)
		self.assertEqual(self.in_force(date), self.lists["Tuesday"])
