# Copyright (c) 2026, Upande LTD and Contributors

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate
from hrms.hr.doctype.attendance.attendance import DuplicateAttendanceError, mark_attendance

EMPLOYEE_NAME = "_Test TA Supersede Absent"


class IntegrationTestAttendanceSupersede(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		company = frappe.get_all("Company", limit=1, pluck="name")
		genders = frappe.get_all("Gender", limit=1, pluck="name")
		if not company or not genders:
			raise unittest.SkipTest("site has no Company / Gender fixtures")

		cls.employee = frappe.db.get_value("Employee", {"employee_name": EMPLOYEE_NAME})
		if not cls.employee:
			cls.employee = (
				frappe.get_doc(
					{
						"doctype": "Employee",
						"first_name": EMPLOYEE_NAME,
						"company": company[0],
						"gender": genders[0],
						"date_of_birth": "1990-01-01",
						"date_of_joining": add_days(getdate(nowdate()), -365),
						"status": "Active",
					}
				)
				.insert(ignore_permissions=True)
				.name
			)

		cls.day = add_days(getdate(nowdate()), -2)

	def setUp(self):
		frappe.db.delete("Attendance", {"employee": self.employee})
		frappe.flags.pop("upande_ta_supersede_absent", None)

	def _absent(self):
		name = mark_attendance(self.employee, self.day, "Absent")
		self.assertIsNotNone(name, "could not seed the Absent this test needs")
		return name

	def _mark(self, status):
		attendance = frappe.new_doc("Attendance")
		attendance.employee = self.employee
		attendance.attendance_date = self.day
		attendance.status = status
		attendance.insert(ignore_permissions=True)
		attendance.submit()
		return attendance

	def test_present_supersedes_a_submitted_absent(self):
		absent = self._absent()

		present = self._mark("Present")

		self.assertEqual(frappe.db.get_value("Attendance", absent, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Attendance", present.name, "status"), "Present")

	def test_supersede_is_recorded_on_the_new_record(self):
		absent = self._absent()

		present = self._mark("Present")

		comment = frappe.db.get_value(
			"Comment",
			{"reference_doctype": "Attendance", "reference_name": present.name},
			"content",
		)
		self.assertIn(absent, comment or "")

	def test_draft_absent_is_removed_rather_than_cancelled(self):
		draft = frappe.new_doc("Attendance")
		draft.employee = self.employee
		draft.attendance_date = self.day
		draft.status = "Absent"
		draft.insert(ignore_permissions=True)

		self._mark("Present")

		self.assertFalse(frappe.db.exists("Attendance", draft.name))

	def test_a_second_absent_is_still_a_duplicate(self):
		self._absent()

		with self.assertRaises(DuplicateAttendanceError):
			self._mark("Absent")

	def test_present_over_present_is_still_a_duplicate(self):
		self._mark("Present")

		with self.assertRaises(DuplicateAttendanceError):
			self._mark("Present")

	def test_switch_off_restores_stock_behaviour(self):
		self._absent()
		frappe.flags["upande_ta_supersede_absent"] = False

		with self.assertRaises(DuplicateAttendanceError):
			self._mark("Present")
