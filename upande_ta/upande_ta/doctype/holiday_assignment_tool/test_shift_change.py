# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""End-to-end tests for changing a shift from the Monthly Attendance Sheet,
checked through HRMS' own resolver.

    employee is on the Day shift from 1 Jan 2030, open-ended
      change: Night from F to E

    F - 1          -> Day (the old assignment, end-dated F - 1)
    F .. E         -> Night
    E + 1          -> Day again (a carry-over assignment)
"""

import unittest

try:
	import frappe
except ImportError:  # pragma: no cover
	frappe = None

try:  # frappe v16+
	from frappe.tests import IntegrationTestCase as _TestCase
except ImportError:  # pragma: no cover
	try:
		from frappe.tests.utils import FrappeTestCase as _TestCase
	except ImportError:
		_TestCase = unittest.TestCase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company", "Department", "Designation", "Employee", "Farm", "Shift Type"]

PREFIX = "_Test SC"
DAY = f"{PREFIX} Day"
NIGHT = f"{PREFIX} Night"


def _site_connected() -> bool:
	if frappe is None:
		return False
	try:
		return bool(getattr(frappe.local, "site", None)) and frappe.db is not None
	except Exception:
		return False


class IntegrationTestShiftChange(_TestCase):
	@classmethod
	def setUpClass(cls):
		if not _site_connected():
			raise unittest.SkipTest("shift change tests need a site")
		super().setUpClass()

		cls.company = frappe.db.get_value("Company", {}, "name")
		gender = frappe.db.get_value("Gender", {}, "name")
		if not (cls.company and gender):
			raise unittest.SkipTest("needs a Company and a Gender")

		for name, start, end in ((DAY, "08:00:00", "17:00:00"), (NIGHT, "18:00:00", "02:00:00")):
			if not frappe.db.exists("Shift Type", name):
				frappe.get_doc(
					{
						"doctype": "Shift Type",
						"__newname": name,
						"name": name,
						"start_time": start,
						"end_time": end,
					}
				).insert(ignore_permissions=True, set_name=name)

		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": f"{PREFIX} Employee",
				"company": cls.company,
				"gender": gender,
				"status": "Active",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2029-01-01",
			}
		)
		for field in employee.meta.fields:
			if field.reqd and not employee.get(field.fieldname):
				if field.fieldtype in ("Data", "Small Text", "Text"):
					employee.set(field.fieldname, f"{PREFIX}-{frappe.generate_hash(length=8)}")
				elif field.fieldtype in ("Int", "Float", "Currency"):
					employee.set(field.fieldname, 1)
		cls.employee = employee.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True).name

		assignment = frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": cls.employee,
				"company": cls.company,
				"shift_type": DAY,
				"start_date": "2030-01-01",
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()
		cls.base = assignment.name

	SAVEPOINT = "shift_change_test"

	def setUp(self):
		super().setUp()
		frappe.db.savepoint(self.SAVEPOINT)
		self.addCleanup(self._rollback)

	def _rollback(self):
		try:
			frappe.db.rollback(save_point=self.SAVEPOINT)
		except Exception:
			pass

	def _change(self, from_date, to_date, shift_type=NIGHT):
		from upande_ta.upande_ta.shift_change import change_shift

		return change_shift(self.employee, from_date, to_date, shift_type)

	def _on(self, date):
		from upande_ta.upande_ta.shift_change import shifts_in_force

		return shifts_in_force(self.employee, frappe.utils.getdate(date), frappe.utils.getdate(date))[
			frappe.utils.getdate(date)
		]["shift_type"]

	def _hrms_on(self, date, time="12:00:00"):
		from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift

		shift = get_employee_shift(self.employee, frappe.utils.get_datetime(f"{date} {time}"), True)
		return shift.shift_type.name if shift else None

	def test_the_window_gets_the_new_shift_and_the_old_one_comes_back(self):
		result = self._change("2030-03-08", "2030-03-24")

		self.assertEqual(self._on("2030-03-07"), DAY)
		self.assertEqual(self._on("2030-03-08"), NIGHT)
		self.assertEqual(self._on("2030-03-24"), NIGHT)
		self.assertEqual(self._on("2030-03-25"), DAY)
		self.assertEqual(result["restored_to"], DAY)
		self.assertEqual(result["trimmed"], [self.base])

	def test_hrms_resolves_it_the_same_way(self):
		self._change("2030-03-08", "2030-03-24")

		self.assertEqual(self._hrms_on("2030-03-07"), DAY)
		self.assertEqual(self._hrms_on("2030-03-10", "20:00:00"), NIGHT)
		self.assertEqual(self._hrms_on("2030-03-25"), DAY)

	def test_the_old_assignment_is_end_dated_and_stays_active(self):
		self._change("2030-03-08", "2030-03-24")
		end_date, status = frappe.db.get_value("Shift Assignment", self.base, ["end_date", "status"])

		self.assertEqual(str(end_date), "2030-03-07")
		self.assertEqual(status, "Active")

	def test_a_past_previous_assignment_goes_inactive(self):
		"""Once its new end date has passed, only the new shift marks attendance."""
		from unittest.mock import patch

		with patch("upande_ta.upande_ta.shift_change.today", return_value="2030-03-20"):
			self._change("2030-03-08", "2030-03-24")

		end_date, status = frappe.db.get_value("Shift Assignment", self.base, ["end_date", "status"])
		self.assertEqual(str(end_date), "2030-03-07")
		self.assertEqual(status, "Inactive")
		with patch("upande_ta.upande_ta.shift_change.today", return_value="2030-03-20"):
			self.assertEqual(self._on("2030-03-07"), DAY, "its own days still read as Day")

	def test_the_schedule_starts_today_for_an_open_ended_shift(self):
		from unittest.mock import patch

		from upande_ta.upande_ta.shift_change import get_shift_schedule

		with patch("upande_ta.upande_ta.shift_change.today", return_value="2030-03-05"):
			schedule = get_shift_schedule(self.employee)
		self.assertEqual(str(schedule["next_from"]), "2030-03-05")
		self.assertEqual(schedule["current"]["name"], self.base)

	def test_the_schedule_starts_after_a_shift_that_ends(self):
		from unittest.mock import patch

		from upande_ta.upande_ta.shift_change import get_shift_schedule

		self._change("2030-03-01", "2030-03-24")
		with patch("upande_ta.upande_ta.shift_change.today", return_value="2030-03-05"):
			schedule = get_shift_schedule(self.employee)
		self.assertEqual(schedule["current"]["shift_type"], NIGHT)
		self.assertEqual(str(schedule["next_from"]), "2030-03-25")

	def test_entries_chain_on_one_after_another(self):
		"""Each entry added from the popup starts where the last one ended,
		and the table runs on to show all of them."""
		from unittest.mock import patch

		from upande_ta.upande_ta.shift_change import get_shift_schedule

		with patch("upande_ta.upande_ta.shift_change.today", return_value="2030-03-05"):
			self._change("2030-03-05", "2030-03-31")
			self._change("2030-04-01", "2030-05-10")
			schedule = get_shift_schedule(self.employee)

		self.assertEqual(str(schedule["next_from"]), "2030-05-11")
		self.assertEqual(
			[(str(r["from_date"]), str(r["to_date"]), r["shift_type"]) for r in schedule["ranges"]],
			[("2030-03-05", "2030-03-31", NIGHT), ("2030-04-01", "2030-05-10", NIGHT)],
		)

	def test_day_and_night_take_turns_by_calendar_week(self):
		"""Wednesday 6 March 2030: Day to Sunday 10th, then Night, Day, Night."""
		from upande_ta.upande_ta.shift_change import change_shift

		result = change_shift(self.employee, "2030-03-06", "2030-03-31", NIGHT, '["' + DAY + '"]')

		self.assertEqual(
			[(str(b["from_date"]), str(b["to_date"]), b["shift_type"]) for b in result["blocks"]],
			[
				("2030-03-06", "2030-03-10", NIGHT),
				("2030-03-11", "2030-03-17", DAY),
				("2030-03-18", "2030-03-24", NIGHT),
				("2030-03-25", "2030-03-31", DAY),
			],
		)
		self.assertEqual(self._hrms_on("2030-03-06", "20:00:00"), NIGHT)
		self.assertEqual(self._hrms_on("2030-03-12"), DAY)
		self.assertEqual(self._hrms_on("2030-03-20", "20:00:00"), NIGHT)
		self.assertEqual(self._on("2030-04-01"), DAY, "back on the old shift after the End Date")

	def test_a_rotation_needs_no_separate_shift_type(self):
		from upande_ta.upande_ta.shift_change import change_shift

		result = change_shift(self.employee, "2030-03-06", "2030-03-24", "", [NIGHT, DAY])

		self.assertEqual(
			[(str(b["from_date"]), str(b["to_date"]), b["shift_type"]) for b in result["blocks"]],
			[
				("2030-03-06", "2030-03-10", NIGHT),
				("2030-03-11", "2030-03-17", DAY),
				("2030-03-18", "2030-03-24", NIGHT),
			],
		)

	def test_no_shift_at_all_is_refused(self):
		from upande_ta.upande_ta.shift_change import change_shift

		with self.assertRaises(frappe.ValidationError):
			change_shift(self.employee, "2030-03-06", "2030-03-24", "", [])

	def test_two_weeks_per_shift(self):
		from upande_ta.upande_ta.shift_change import change_shift

		result = change_shift(self.employee, "2030-03-04", "2030-04-14", NIGHT, [DAY], weeks_per_shift=2)

		self.assertEqual(
			[(str(b["from_date"]), str(b["to_date"]), b["shift_type"]) for b in result["blocks"]],
			[
				("2030-03-04", "2030-03-17", NIGHT),
				("2030-03-18", "2030-03-31", DAY),
				("2030-04-01", "2030-04-14", NIGHT),
			],
		)

	def test_a_wider_window_cancels_a_change_inside_it(self):
		first = self._change("2030-03-15", "2030-03-24")
		self._change("2030-03-09", "2030-03-31", DAY)

		inside = frappe.get_all(
			"Shift Assignment", filters={"employee": self.employee, "shift_type": NIGHT}, pluck="name"
		)
		self.assertTrue(inside)
		self.assertTrue(all(frappe.db.get_value("Shift Assignment", n, "docstatus") == 2 for n in inside))
		self.assertEqual(self._on("2030-03-20"), DAY)
		self.assertEqual(self._on("2030-04-01"), DAY)
		self.assertTrue(first["created"])

	def test_the_same_shift_again_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._change("2030-03-08", "2030-03-24", DAY)

	def test_an_end_before_the_start_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._change("2030-03-08", "2030-03-01")

	def test_the_setting_switches_it_off(self):
		frappe.db.set_single_value("Biometric Setting", "disable_shift_change", 1)
		with self.assertRaises(frappe.ValidationError) as caught:
			self._change("2030-03-08", "2030-03-24")
		self.assertIn("disabled", frappe.utils.strip_html(str(caught.exception)))

	def test_the_schedule_lists_thirty_days_in_runs(self):
		from upande_ta.upande_ta.shift_change import get_shift_schedule

		self._change("2030-03-08", "2030-03-14")
		schedule = get_shift_schedule(self.employee, "2030-03-01")

		self.assertEqual(str(schedule["to_date"]), "2030-03-30")
		self.assertEqual(schedule["shift_type"], DAY)
		self.assertEqual(
			[(str(r["from_date"]), str(r["to_date"]), r["shift_type"]) for r in schedule["ranges"]],
			[
				("2030-03-01", "2030-03-07", DAY),
				("2030-03-08", "2030-03-14", NIGHT),
				("2030-03-15", "2030-03-30", DAY),
			],
		)
		night = schedule["ranges"][1]
		self.assertEqual((str(night["start_date"]), str(night["end_date"])), ("2030-03-08", "2030-03-14"))
		self.assertIsNone(schedule["ranges"][2]["end_date"], "the carry-over is open-ended like the original")

	def test_the_sheet_row_shows_the_new_shift_straight_away(self):
		"""A change late in the period covers fewer days than the old shift,
		and the row still moves to it."""
		from upande_ta.upande_ta.overrides.monthly_attendance_sheet import apply_patch, build_shift_resolver

		apply_patch()
		filters = frappe._dict(filter_based_on="Date Range", start_date="2030-03-01", end_date="2030-03-31")
		self._change("2030-03-25", "2030-04-30")

		self.assertEqual(build_shift_resolver([self.employee], filters)(self.employee), NIGHT)

	def test_absent_marked_by_the_old_shift_is_removed(self):
		absent = self._mark("2030-03-10", "Absent")
		present = self._mark("2030-03-11", "Present")

		result = self._change("2030-03-08", "2030-03-24")

		self.assertEqual(result["absent_removed"], ["2030-03-10"])
		self.assertFalse(frappe.db.exists("Attendance", absent))
		self.assertTrue(frappe.db.exists("Attendance", present))
		self.assertEqual(result["attendance_on_other_shift"], ["2030-03-11"])

	def _mark(self, date, status):
		attendance = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee,
				"attendance_date": date,
				"status": status,
				"company": self.company,
				"shift": DAY,
			}
		)
		# the fixture's year is in the future, where HRMS refuses attendance
		attendance.flags.ignore_validate = True
		attendance.insert(ignore_permissions=True)
		attendance.submit()
		return attendance.name
