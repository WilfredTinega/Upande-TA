# Copyright (c) 2026, Upande LTD and Contributors

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

EMPLOYEE_NAME = "_Test TA Week Off Absent"
AUTO_SHIFT = "_Test TA Auto Attendance Shift"
MANUAL_SHIFT = "_Test TA Manual Shift"


class IntegrationTestCancelAutoAbsentOnWeekOff(IntegrationTestCase):
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
		for name, auto in ((AUTO_SHIFT, 1), (MANUAL_SHIFT, 0)):
			if not frappe.db.exists("Shift Type", name):
				frappe.get_doc(
					{
						"doctype": "Shift Type",
						"name": name,
						"start_time": "08:00:00",
						"end_time": "17:00:00",
						"enable_auto_attendance": auto,
						"process_attendance_after": add_days(getdate(nowdate()), -60),
						"last_sync_of_checkin": nowdate() + " 00:00:00",
					}
				).insert(ignore_permissions=True)

	def setUp(self):
		frappe.db.delete("Attendance", {"employee": self.employee})
		frappe.db.delete("Holiday List Assignment", {"assigned_to": self.employee})
		base = getdate(nowdate())
		self.week_off = [add_days(base, -10), add_days(base, -9), add_days(base, -3)]
		self.working_day = add_days(base, -8)
		self.holiday_list = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": f"_Test TA Week Off {frappe.generate_hash(length=6)}",
				"from_date": add_days(base, -30),
				"to_date": add_days(base, 30),
				"holidays": [
					{"holiday_date": day, "description": "Week Off", "weekly_off": 1} for day in self.week_off
				],
			}
		).insert(ignore_permissions=True)

	def absent(self, date, shift, owner="Administrator"):
		attendance = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee,
				"attendance_date": date,
				"status": "Absent",
				"shift": shift,
				"company": self.company,
			}
		).insert(ignore_permissions=True)
		attendance.submit()
		if owner != "Administrator":
			frappe.db.set_value("Attendance", attendance.name, "owner", owner, update_modified=False)
		return attendance.name

	def assign(self):
		frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": self.employee,
				"holiday_list": self.holiday_list.name,
				"from_date": add_days(getdate(nowdate()), -20),
			}
		).insert(ignore_permissions=True).submit()

	def docstatus(self, name):
		return frappe.db.get_value("Attendance", name, "docstatus")

	def test_auto_absent_on_new_week_off_is_cancelled(self):
		on_week_off = self.absent(self.week_off[0], AUTO_SHIFT)
		self.assign()
		self.assertEqual(self.docstatus(on_week_off), 2)

	def test_absent_on_a_working_day_stays(self):
		working = self.absent(self.working_day, AUTO_SHIFT)
		self.assign()
		self.assertEqual(self.docstatus(working), 1)

	def test_shift_without_auto_attendance_is_left_alone(self):
		manual_shift = self.absent(self.week_off[1], MANUAL_SHIFT)
		self.assign()
		self.assertEqual(self.docstatus(manual_shift), 1)

	def test_absent_marked_by_hand_stays(self):
		by_hand = self.absent(self.week_off[2], AUTO_SHIFT, owner="hr@example.com")
		self.assign()
		self.assertEqual(self.docstatus(by_hand), 1)
