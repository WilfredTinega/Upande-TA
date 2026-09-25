# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""End-to-end tests for changing a week off from the Monthly Attendance Sheet,
checked through HRMS' own resolver.

    employee is on a Thursday week off, with a public holiday on a Friday
      change: from Friday F to E

    as_on F .. E   -> a replica whose only weekly off is Friday, public holiday kept
    as_on E + 1    -> the Thursday list again
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
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company", "Department", "Designation", "Employee", "Farm", "Holiday List"]

PREFIX = "_Test WOC"
BASE = f"{PREFIX} Thursday Week Off"
#: Mondays through Sundays of two weeks in March 2030; 7 and 14 are Thursdays.
YEAR = 2030
PUBLIC_HOLIDAY = "2030-03-15"  # a Friday


def _site_connected() -> bool:
	if frappe is None:
		return False
	try:
		return bool(getattr(frappe.local, "site", None)) and frappe.db is not None
	except Exception:
		return False


class IntegrationTestWeekOffChange(_TestCase):
	@classmethod
	def setUpClass(cls):
		if not _site_connected():
			raise unittest.SkipTest("week-off change tests need a site")
		super().setUpClass()

		from frappe.utils import add_days, getdate

		cls.company = frappe.db.get_value("Company", {}, "name")
		gender = frappe.db.get_value("Gender", {}, "name")
		if not (cls.company and gender):
			raise unittest.SkipTest("needs a Company and a Gender")

		if not frappe.db.exists("Holiday List", BASE):
			holidays, date = [], getdate(f"{YEAR}-01-03")  # the first Thursday
			while date.year == YEAR:
				holidays.append({"holiday_date": date, "description": "Thursday", "weekly_off": 1})
				date = add_days(date, 7)
			holidays.append({"holiday_date": PUBLIC_HOLIDAY, "description": "Public Holiday", "weekly_off": 0})
			frappe.get_doc(
				{
					"doctype": "Holiday List",
					"holiday_list_name": BASE,
					"from_date": f"{YEAR}-01-01",
					"to_date": f"{YEAR}-12-31",
					"holidays": sorted(holidays, key=lambda h: str(h["holiday_date"])),
				}
			).insert(ignore_permissions=True)

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
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": cls.employee,
				"holiday_list": BASE,
				"from_date": f"{YEAR}-01-01",
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()

	SAVEPOINT = "week_off_change_test"

	def setUp(self):
		super().setUp()
		frappe.db.savepoint(self.SAVEPOINT)
		self.addCleanup(self._rollback)

	def _rollback(self):
		try:
			frappe.db.rollback(save_point=self.SAVEPOINT)
		except Exception:
			pass

	def _change(self, from_date, to_date):
		from upande_ta.upande_ta.week_off_change import change_week_off

		return change_week_off(self.employee, from_date, to_date)

	def _on(self, date):
		from hrms.utils.holiday_list import get_holiday_list_for_employee

		return get_holiday_list_for_employee(self.employee, raise_exception=False, as_on=date)

	def _week_offs(self, holiday_list):
		return [
			str(d)
			for d in frappe.get_all(
				"Holiday",
				filters={"parent": holiday_list, "weekly_off": 1},
				pluck="holiday_date",
				order_by="holiday_date",
			)
		]

	def test_the_clicked_weekday_is_the_only_week_off_in_the_window(self):
		result = self._change("2030-03-08", "2030-03-24")  # Friday .. Sunday
		replica = result["holiday_list"]

		self.assertEqual(result["weekday"], "Friday")
		self.assertEqual(self._week_offs(replica), ["2030-03-08", "2030-03-22"])
		self.assertTrue(
			frappe.db.exists("Holiday", {"parent": replica, "holiday_date": PUBLIC_HOLIDAY, "weekly_off": 0}),
			"the public holiday on Friday 15th is kept, as a public holiday",
		)

	def test_hrms_resolves_the_window_and_the_return(self):
		result = self._change("2030-03-08", "2030-03-24")

		self.assertEqual(self._on("2030-03-07"), BASE)
		self.assertEqual(self._on("2030-03-08"), result["holiday_list"])
		self.assertEqual(self._on("2030-03-24"), result["holiday_list"])
		self.assertEqual(self._on("2030-03-25"), BASE)
		self.assertEqual(result["restored_to"], BASE)

	def test_changing_the_same_window_again_replaces_it(self):
		first = self._change("2030-03-08", "2030-03-24")
		second = self._change("2030-03-09", "2030-03-24")  # Saturday instead

		self.assertEqual(second["weekday"], "Saturday")
		self.assertEqual(self._on("2030-03-10"), second["holiday_list"])
		# the day before the new window keeps the first change
		self.assertEqual(self._on("2030-03-08"), first["holiday_list"])
		self.assertEqual(self._on("2030-03-25"), BASE)

	def test_a_wider_window_cancels_a_change_inside_it(self):
		first = self._change("2030-03-15", "2030-03-24")  # Friday
		second = self._change("2030-03-09", "2030-03-31")  # Saturday, around it

		replaced_lists = {
			frappe.db.get_value("Holiday List Assignment", name, "holiday_list") for name in second["replaced"]
		}
		self.assertEqual(replaced_lists, {first["holiday_list"], BASE}, "the change and its return")
		self.assertEqual(self._on("2030-03-20"), second["holiday_list"])
		self.assertEqual(self._on("2030-04-01"), BASE)

	def test_the_same_change_again_is_refused(self):
		"""Nothing in the window would change."""
		self._change("2030-03-08", "2030-03-24")
		with self.assertRaises(frappe.ValidationError):
			self._change("2030-03-08", "2030-03-24")

	def test_the_current_week_off_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._change("2030-03-07", "2030-03-20")  # a Thursday

	def test_an_end_before_the_start_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._change("2030-03-08", "2030-03-01")

	def test_a_single_day(self):
		result = self._change("2030-03-09", "2030-03-09")
		self.assertEqual(self._week_offs(result["holiday_list"]), ["2030-03-09"])
		self.assertEqual(self._on("2030-03-10"), BASE)


	def test_the_setting_switches_it_off(self):
		frappe.db.set_single_value("Biometric Setting", "disable_week_off_change", 1)
		with self.assertRaises(frappe.ValidationError) as caught:
			self._change("2030-03-08", "2030-03-24")
		self.assertIn("disabled", frappe.utils.strip_html(str(caught.exception)))
		with self.assertRaises(frappe.ValidationError):
			self._remove("2030-03-07", "2030-03-20")

	def test_two_days_can_be_chosen(self):
		from upande_ta.upande_ta.week_off_change import change_week_off

		result = change_week_off(self.employee, "2030-03-12", "2030-03-24", '["Wednesday", "Tuesday"]')

		self.assertEqual(result["weekday"], "Tuesday & Wednesday")
		self.assertEqual(
			self._week_offs(result["holiday_list"]), ["2030-03-12", "2030-03-13", "2030-03-19", "2030-03-20"]
		)
		self.assertIn("Tuesday & Wednesday", result["holiday_list"])

	def test_an_unknown_weekday_is_refused(self):
		from upande_ta.upande_ta.week_off_change import change_week_off

		with self.assertRaises(frappe.ValidationError):
			change_week_off(self.employee, "2030-03-12", "2030-03-24", '["Funday"]')

	def test_a_one_day_change_can_be_widened(self):
		"""What 200875 hit: Tuesday was set for one day, and setting it again
		from the same day to a later End Date said it already was."""
		self._change("2030-03-12", "2030-03-12")  # Tuesday, one day
		wider = self._change("2030-03-12", "2030-03-31")

		self.assertEqual(self._week_offs(wider["holiday_list"]), ["2030-03-12", "2030-03-19", "2030-03-26"])
		self.assertTrue(
			frappe.db.exists("Holiday", {"parent": wider["holiday_list"], "holiday_date": PUBLIC_HOLIDAY}),
			"the public holiday after the one-day change is still read from the list beyond it",
		)
		self.assertEqual(self._on("2030-03-20"), wider["holiday_list"])
		self.assertEqual(self._on("2030-04-01"), BASE)

	def test_absent_on_the_new_week_off_is_cancelled_and_deleted(self):
		absent = self._mark("2030-03-08", "Absent")  # the Friday about to become the week off
		present = self._mark("2030-03-22", "Present")

		result = self._change("2030-03-08", "2030-03-24")

		self.assertEqual(result["absent_removed"], ["2030-03-08"])
		self.assertFalse(frappe.db.exists("Attendance", absent))
		self.assertTrue(frappe.db.exists("Attendance", present), "a day worked is left alone")

	def _mark(self, date, status):
		attendance = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee,
				"attendance_date": date,
				"status": status,
				"company": self.company,
			}
		)
		# the fixture's year is in the future, where HRMS refuses attendance
		attendance.flags.ignore_validate = True
		attendance.insert(ignore_permissions=True)
		attendance.submit()
		return attendance.name

	# ──────────────────────────────────────────────────────────────────────
	# Removing a week off
	# ──────────────────────────────────────────────────────────────────────

	def _remove(self, from_date, to_date):
		from upande_ta.upande_ta.week_off_change import remove_week_off

		return remove_week_off(self.employee, from_date, to_date)

	def _move_to_thursday_and_friday(self, from_date):
		"""A two-day list from ``from_date``, for this test only."""
		from frappe.utils import add_days, getdate

		name = f"{PREFIX} Thursday & Friday Week Off"
		if not frappe.db.exists("Holiday List", name):
			holidays, date = [], getdate(f"{YEAR}-01-03")
			while date.year == YEAR:
				holidays.append({"holiday_date": date, "description": "Thursday", "weekly_off": 1})
				if add_days(date, 1).year == YEAR:
					holidays.append({"holiday_date": add_days(date, 1), "description": "Friday", "weekly_off": 1})
				date = add_days(date, 7)
			frappe.get_doc(
				{
					"doctype": "Holiday List",
					"holiday_list_name": name,
					"from_date": f"{YEAR}-01-01",
					"to_date": f"{YEAR}-12-31",
					"holidays": holidays,
				}
			).insert(ignore_permissions=True)
		assignment = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": self.employee,
				"holiday_list": name,
				"from_date": from_date,
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()
		return name

	def test_removing_the_only_week_off_leaves_none_in_the_window(self):
		result = self._remove("2030-03-07", "2030-03-20")  # Thursdays 7 and 14

		self.assertEqual(self._week_offs(result["holiday_list"]), [])
		self.assertTrue(
			frappe.db.exists("Holiday", {"parent": result["holiday_list"], "holiday_date": PUBLIC_HOLIDAY}),
			"public holidays are kept",
		)
		self.assertEqual(self._on("2030-03-10"), result["holiday_list"])
		self.assertEqual(self._on("2030-03-21"), BASE)

	def test_removing_one_day_of_two_keeps_the_other(self):
		two_day = self._move_to_thursday_and_friday("2030-06-01")
		result = self._remove("2030-06-07", "2030-06-16")  # Fridays 7 and 14

		self.assertEqual(self._week_offs(result["holiday_list"]), ["2030-06-13"], "Thursday 13th stays")
		self.assertEqual(self._on("2030-06-17"), two_day)

	def test_removing_a_day_that_is_not_a_week_off_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._remove("2030-03-08", "2030-03-20")  # a Friday

	def test_changing_a_two_day_week_off_leaves_one(self):
		self._move_to_thursday_and_friday("2030-06-01")
		result = self._change("2030-06-06", "2030-06-16")  # Thursday

		self.assertEqual(self._week_offs(result["holiday_list"]), ["2030-06-06", "2030-06-13"])


if __name__ == "__main__":
	unittest.main()
