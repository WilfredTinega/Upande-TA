# Copyright (c) 2026, Upande LTD and Contributors

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_ta.upande_ta.overrides.shift_assignment import sync_default_shift

EMPLOYEE_NAME = "_Test TA Default Shift"
DAY = "_Test TA Default Day"
NIGHT = "_Test TA Default Night"


class IntegrationTestDefaultShiftFromAssignment(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		company = frappe.get_all("Company", limit=1, pluck="name")
		genders = frappe.get_all("Gender", limit=1, pluck="name")
		if not company or not genders:
			raise unittest.SkipTest("site has no Company / Gender fixtures")
		cls.company = company[0]
		cls.employee = frappe.db.get_value("Employee", {"employee_name": EMPLOYEE_NAME}) or (
			frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": EMPLOYEE_NAME,
					"company": cls.company,
					"gender": genders[0],
					"date_of_birth": "1990-01-01",
					"date_of_joining": add_days(getdate(nowdate()), -365),
					"status": "Active",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		for name, start, end in ((DAY, "07:00:00", "16:00:00"), (NIGHT, "19:00:00", "04:00:00")):
			if not frappe.db.exists("Shift Type", name):
				frappe.get_doc(
					{"doctype": "Shift Type", "name": name, "start_time": start, "end_time": end}
				).insert(ignore_permissions=True)

	def setUp(self):
		frappe.db.delete("Shift Assignment", {"employee": self.employee})
		frappe.db.set_value("Employee", self.employee, "default_shift", None)
		self.today = getdate(nowdate())

	def assign(self, shift, start, end=None):
		return frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": self.employee,
				"company": self.company,
				"shift_type": shift,
				"start_date": start,
				"end_date": end,
				"status": "Active",
			}
		).submit()

	def default_shift(self):
		return frappe.db.get_value("Employee", self.employee, "default_shift")

	def test_an_assignment_in_effect_updates_the_employee_on_submit(self):
		self.assign(DAY, add_days(self.today, -5))
		self.assertEqual(self.default_shift(), DAY)

	def test_a_later_assignment_waits_for_its_start_date(self):
		self.assign(DAY, add_days(self.today, -5), add_days(self.today, 2))
		self.assign(NIGHT, add_days(self.today, 3))
		self.assertEqual(self.default_shift(), DAY)

		sync_default_shift([self.employee], on_date=add_days(self.today, 3))
		self.assertEqual(self.default_shift(), NIGHT)

	def test_cancelling_hands_back_the_shift_before_it(self):
		self.assign(DAY, add_days(self.today, -10), add_days(self.today, -3))
		night = self.assign(NIGHT, add_days(self.today, -2))
		self.assertEqual(self.default_shift(), NIGHT)

		night.cancel()
		self.assertEqual(self.default_shift(), DAY)

	def test_switching_inactive_hands_back_the_shift_before_it(self):
		self.assign(DAY, add_days(self.today, -10), add_days(self.today, -3))
		night = self.assign(NIGHT, add_days(self.today, -2))

		night.status = "Inactive"
		night.save()
		self.assertEqual(self.default_shift(), DAY)
