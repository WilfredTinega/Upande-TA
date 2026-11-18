# Copyright (c) 2026, Upande LTD and Contributors

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

EMPLOYEE_NAME = "_Test TA Late Roster"
SHIFT = "_Test TA Late Roster Shift"


class IntegrationTestRestampCheckinsOnSubmit(IntegrationTestCase):
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
		frappe.db.set_value("Employee", cls.employee, "default_shift", None)
		if not frappe.db.exists("Shift Type", SHIFT):
			frappe.get_doc(
				{
					"doctype": "Shift Type",
					"name": SHIFT,
					"start_time": "08:00:00",
					"end_time": "17:00:00",
					"enable_auto_attendance": 1,
					"process_attendance_after": add_days(getdate(nowdate()), -60),
					"last_sync_of_checkin": nowdate() + " 00:00:00",
				}
			).insert(ignore_permissions=True)

	def setUp(self):
		frappe.db.delete("Employee Checkin", {"employee": self.employee})
		frappe.db.delete("Shift Assignment", {"employee": self.employee})

	def test_punch_before_roster_takes_its_shift(self):
		day = add_days(getdate(nowdate()), -5)
		checkin = frappe.get_doc(
			{"doctype": "Employee Checkin", "employee": self.employee, "time": f"{day} 08:05:00", "log_type": "IN"}
		).insert(ignore_permissions=True)
		self.assertFalse(frappe.db.get_value("Employee Checkin", checkin.name, "shift"))

		frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": self.employee,
				"company": self.company,
				"shift_type": SHIFT,
				"start_date": day,
				"end_date": day,
			}
		).insert(ignore_permissions=True).submit()

		shift, offshift = frappe.db.get_value("Employee Checkin", checkin.name, ["shift", "offshift"])
		self.assertEqual(shift, SHIFT)
		self.assertEqual(offshift, 0)
