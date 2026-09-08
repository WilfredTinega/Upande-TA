# Copyright (c) 2026, Upande LTD and Contributors

import unittest
from datetime import datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_ta.upande_ta.api.absent_marking import process_absentees

SHIFT = "_Test TA Absent Shift"
WORK_HOLIDAY_LIST = "_Test TA Absent Workdays"
REST_HOLIDAY_LIST = "_Test TA Absent Rest Days"


def _company():
	company = frappe.get_all("Company", limit=1, pluck="name")
	return company[0] if company else None


def _ensure_holiday_lists(rest_day):
	"""One list where `rest_day` is a working day, one where it is a week off.

	Both are kept in step with the date under test on every run: `rest_day`
	moves forward each day, so a list left over from yesterday would otherwise
	no longer say anything about it and the rest-day tests would quietly turn
	into "marks Absent" tests.
	"""
	today = getdate(nowdate())
	rest_day = getdate(rest_day)

	for name in (WORK_HOLIDAY_LIST, REST_HOLIDAY_LIST):
		if not frappe.db.exists("Holiday List", name):
			frappe.get_doc(
				{
					"doctype": "Holiday List",
					"holiday_list_name": name,
					"from_date": add_days(today, -90),
					"to_date": add_days(today, 90),
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value(
				"Holiday List",
				name,
				{"from_date": add_days(today, -90), "to_date": add_days(today, 90)},
			)

	# The work list must not claim the day as a holiday...
	frappe.db.delete("Holiday", {"parent": WORK_HOLIDAY_LIST, "holiday_date": rest_day})
	# ...and the rest list must, as a weekly off.
	if not frappe.db.exists("Holiday", {"parent": REST_HOLIDAY_LIST, "holiday_date": rest_day}):
		holiday_list = frappe.get_doc("Holiday List", REST_HOLIDAY_LIST)
		holiday_list.append(
			"holidays", {"holiday_date": rest_day, "description": "Weekly Off", "weekly_off": 1}
		)
		holiday_list.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Holiday List")


def _ensure_shift():
	"""A 08:00-17:00 shift with `last_sync_of_checkin` deliberately left empty.

	HRMS's own pass cannot mark anything without that watermark, so its absence
	is what proves these Absents came from the shift-end pass.
	"""
	config = {
		"start_time": "08:00:00",
		"end_time": "17:00:00",
		"enable_auto_attendance": 1,
		"process_attendance_after": add_days(getdate(nowdate()), -60),
		"working_hours_threshold_for_absent": 4,
		# Deliberately generous, like Kaitet's real shifts: a scan is accepted
		# 2 hours past the end, but the grace period must still be measured
		# from 17:00.
		"allow_check_out_after_shift_end_time": 120,
		"last_sync_of_checkin": None,
	}

	if frappe.db.exists("Shift Type", SHIFT):
		# Re-apply the config: a shift left over from an earlier run may predate
		# a change to it, and the timings are what these tests assert on.
		frappe.db.set_value("Shift Type", SHIFT, config)
		frappe.clear_cache(doctype="Shift Type")
		return

	frappe.get_doc({"doctype": "Shift Type", "__newname": SHIFT, **config}).insert(ignore_permissions=True)


def _make_employee(suffix, holiday_list, company):
	first_name = f"_Test TA Absent {suffix}"
	existing = frappe.db.get_value("Employee", {"employee_name": first_name})
	if existing:
		frappe.db.set_value(
			"Employee",
			existing,
			{"status": "Active", "holiday_list": holiday_list, "default_shift": None},
		)
		return existing

	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": first_name,
			"company": company,
			"gender": frappe.get_all("Gender", limit=1, pluck="name")[0],
			"date_of_birth": "1990-01-01",
			"date_of_joining": add_days(getdate(nowdate()), -365),
			"status": "Active",
			"holiday_list": holiday_list,
		}
	).insert(ignore_permissions=True)
	return employee.name


def _assign_shift(employee, company):
	frappe.db.delete("Shift Assignment", {"employee": employee})
	assignment = frappe.get_doc(
		{
			"doctype": "Shift Assignment",
			"employee": employee,
			"shift_type": SHIFT,
			"company": company,
			"start_date": add_days(getdate(nowdate()), -60),
			"status": "Active",
		}
	).insert(ignore_permissions=True)
	assignment.submit()


def _checkin(employee, day, hour, log_type):
	return frappe.get_doc(
		{
			"doctype": "Employee Checkin",
			"employee": employee,
			"log_type": log_type,
			"time": datetime.combine(getdate(day), datetime.min.time()) + timedelta(hours=hour),
		}
	).insert(ignore_permissions=True)


class IntegrationTestAbsentMarking(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.company = _company()
		if not cls.company or not frappe.get_all("Gender", limit=1, pluck="name"):
			# A bare site with no Company or no Gender master cannot carry an
			# Employee; nothing here is testable and nothing here is at risk.
			raise unittest.SkipTest("site has no Company / Gender fixtures")

		cls.day = add_days(getdate(nowdate()), -1)
		_ensure_holiday_lists(cls.day)
		_ensure_shift()

		cls.absentee = _make_employee("Absentee", WORK_HOLIDAY_LIST, cls.company)
		cls.scanner = _make_employee("Scanner", WORK_HOLIDAY_LIST, cls.company)
		cls.rester = _make_employee("Rester", REST_HOLIDAY_LIST, cls.company)
		for employee in (cls.absentee, cls.scanner, cls.rester):
			_assign_shift(employee, cls.company)

		cls.employees = (cls.absentee, cls.scanner, cls.rester)

		# 18:00 on the day itself: the window closed at 17:00, so a 60 minute
		# grace period has just elapsed.
		cls.due_at = datetime.combine(getdate(cls.day), datetime.min.time()) + timedelta(hours=18)

		# Every tunable is a Biometric Setting field; the tests set them
		# explicitly rather than leaning on the site's own configuration.
		cls.settings = frappe._dict(
			{
				"absent_grace_minutes": 60,
				"absent_lookback_days": 1,
				"absent_max_lookback_days": 31,
				"absent_batch_size": 100,
				"absent_skip_when_no_device_activity": 0,
				"absent_shift_scope": "Shifts with Auto Attendance Enabled",
			}
		)

	def setUp(self):
		frappe.db.delete("Attendance", {"employee": ("in", self.employees)})
		frappe.db.delete("Employee Checkin", {"employee": ("in", self.employees)})
		# Reset the watermark: one test drives HRMS's own pass and sets it, and
		# these tests must each start from a shift that cannot mark anything on
		# its own.
		frappe.db.set_value("Shift Type", SHIFT, "last_sync_of_checkin", None)
		frappe.flags.pop("upande_ta_absent_rest_days", None)

	def _run(self, now=None, **overrides):
		settings = frappe._dict(self.settings)
		settings.update(overrides)
		return process_absentees(
			from_date=self.day,
			to_date=self.day,
			settings=settings,
			now=now or self.due_at,
		)

	def _window(self, summary):
		for window in summary["windows"]:
			if window["shift"] == SHIFT:
				return window
		return None

	def _status(self, employee):
		return frappe.db.get_value(
			"Attendance",
			{"employee": employee, "attendance_date": self.day, "docstatus": 1},
			"status",
		)

	def test_grace_is_measured_from_the_shift_end_not_the_checkout_allowance(self):
		"""Kaitet's shifts allow check-out hours after the end; anchoring the
		grace there would put a 60 minute grace mos of a day late."""
		summary = self._run()
		window = self._window(summary)

		self.assertEqual(window["shift_end"], "2026-01-01 17:00:00".replace("2026-01-01", str(self.day)))
		self.assertEqual(window["window_end"], "2026-01-01 19:00:00".replace("2026-01-01", str(self.day)))
		self.assertEqual(window["due_at"], "2026-01-01 18:00:00".replace("2026-01-01", str(self.day)))

	def test_marks_absent_after_shift_end_without_last_sync_of_checkin(self):
		"""The whole point: no watermark, no day-long wait."""
		self.assertIsNone(frappe.db.get_value("Shift Type", SHIFT, "last_sync_of_checkin"))

		summary = self._run()

		self.assertEqual(self._status(self.absentee), "Absent")
		self.assertIn(self.absentee, self._window(summary)["marked_employees"])

	def test_does_not_mark_employee_who_scanned(self):
		_checkin(self.scanner, self.day, 8, "IN")

		self._run()

		self.assertIsNone(self._status(self.scanner))

	def test_does_not_mark_on_the_employees_own_rest_day(self):
		self._run()

		self.assertIsNone(self._status(self.rester))
		self.assertEqual(self._window(self._run())["rest_day"], 1)

	def test_window_is_not_due_before_the_grace_period_elapses(self):
		one_minute_early = self.due_at - timedelta(minutes=1)

		summary = self._run(now=one_minute_early)

		self.assertIsNone(self._window(summary))
		self.assertIsNone(self._status(self.absentee))

	def test_grace_period_lengthens_the_wait(self):
		summary = self._run(absent_grace_minutes=180)

		self.assertIsNone(self._window(summary))
		self.assertIsNone(self._status(self.absentee))

	def test_idle_window_is_treated_as_a_device_outage(self):
		summary = self._run(absent_skip_when_no_device_activity=1)

		self.assertEqual(self._window(summary)["skipped"], "no device activity in window")
		self.assertIsNone(self._status(self.absentee))

	def test_second_run_marks_nobody_twice(self):
		self._run()
		summary = self._run()

		self.assertEqual(summary["marked"], 0)
		self.assertEqual(
			frappe.db.count(
				"Attendance", {"employee": self.absentee, "attendance_date": self.day, "docstatus": 1}
			),
			1,
		)

	def test_approved_leave_is_left_to_the_leave_application(self):
		leave_type = frappe.get_all("Leave Type", limit=1, pluck="name")
		if not leave_type:
			self.skipTest("site has no Leave Type")

		# Only the row this pass reads is needed; a full Leave Application needs
		# an allocation, an approver and a workflow that are not under test.
		frappe.db.sql(
			"""
			INSERT INTO `tabLeave Application`
				(name, employee, leave_type, from_date, to_date, status, docstatus, creation, modified, owner, modified_by)
			VALUES (%(name)s, %(employee)s, %(leave_type)s, %(day)s, %(day)s, 'Approved', 1, NOW(), NOW(), 'Administrator', 'Administrator')
			""",
			{
				"name": "_TEST-TA-ABSENT-LEAVE",
				"employee": self.absentee,
				"leave_type": leave_type[0],
				"day": self.day,
			},
		)
		try:
			summary = self._run()

			self.assertIsNone(self._status(self.absentee))
			self.assertEqual(self._window(summary)["on_leave"], 1)
		finally:
			frappe.db.delete("Leave Application", {"name": "_TEST-TA-ABSENT-LEAVE"})

	def test_late_checkin_replaces_the_absent_instead_of_being_skipped(self):
		"""The scan arrives after the day was marked Absent — stock ERPNext
		flags such a log `skip_auto_attendance` and loses it for good."""
		self._run()
		absent = frappe.db.get_value(
			"Attendance",
			{"employee": self.absentee, "attendance_date": self.day, "docstatus": 1},
			"name",
		)
		self.assertIsNotNone(absent)

		logs = [
			_checkin(self.absentee, self.day, 8, "IN").name,
			_checkin(self.absentee, self.day, 16, "OUT").name,
		]
		frappe.db.set_value(
			"Shift Type",
			SHIFT,
			"last_sync_of_checkin",
			datetime.combine(getdate(add_days(self.day, 1)), datetime.min.time()) + timedelta(hours=6),
		)

		frappe.get_doc("Shift Type", SHIFT).process_auto_attendance()

		self.assertEqual(frappe.db.get_value("Attendance", absent, "docstatus"), 2)
		self.assertEqual(self._status(self.absentee), "Present")
		for log in logs:
			row = frappe.db.get_value(
				"Employee Checkin", log, ["attendance", "skip_auto_attendance"], as_dict=True
			)
			self.assertIsNotNone(row.attendance, "check-in was left unlinked")
			self.assertFalse(row.skip_auto_attendance, "check-in was flagged out of auto attendance")

	def test_reclaim_clears_the_skip_flag_left_by_a_past_collision(self):
		from upande_ta.upande_ta.api.absent_marking import _reclaim_skipped_checkins

		self._run()
		log = _checkin(self.absentee, self.day, 8, "IN")
		frappe.db.set_value("Employee Checkin", log.name, "skip_auto_attendance", 1)

		result = _reclaim_skipped_checkins(days=3)

		self.assertIn(log.name, result["checkins"])
		self.assertFalse(frappe.db.get_value("Employee Checkin", log.name, "skip_auto_attendance"))

	def test_whitelisted_entry_point_accepts_browser_types(self):
		"""The desk sends strings; whitelisted type hints are enforced at
		runtime by pydantic, so a hint narrower than this raises FrappeTypeError
		on the real call rather than in review."""
		from upande_ta.upande_ta.api.absent_marking import mark_absentees_now

		result = mark_absentees_now(from_date=str(self.day), to_date=str(self.day), dry_run="1")

		self.assertEqual(result["dry_run"], 1)
		self.assertEqual(result["from_date"], str(self.day))

	def test_company_default_holiday_list_does_not_authorise_marking(self):
		"""A company list proves public holidays, not week offs.

		Kaitet Ltd. and Karen Roses both default to "Local Holidays 2026", which
		has no weekly_off rows at all. Reading it as the employee's rest days
		would mark everyone without their own list Absent on their week off.
		"""
		company_default = frappe.db.get_value("Company", self.company, "default_holiday_list")
		frappe.db.set_value("Employee", self.absentee, "holiday_list", None)
		frappe.db.set_value("Company", self.company, "default_holiday_list", WORK_HOLIDAY_LIST)
		try:
			summary = self._run()

			self.assertIsNone(self._status(self.absentee))
			self.assertEqual(self._window(summary)["no_week_off_list"], 1)
		finally:
			frappe.db.set_value("Company", self.company, "default_holiday_list", company_default)
			frappe.db.set_value("Employee", self.absentee, "holiday_list", WORK_HOLIDAY_LIST)

	def test_default_shift_loses_to_an_active_assignment_for_another_shift(self):
		""" "Tied to the shift assigned to that employee": an assignment wins.

		HRMS unions assigned employees with everyone carrying the shift as their
		`default_shift`, which is how one day collected an Absent from one
		shift's run beside a Present from another's.
		"""
		other_shift = "_Test TA Absent Other Shift"
		if not frappe.db.exists("Shift Type", other_shift):
			frappe.get_doc(
				{
					"doctype": "Shift Type",
					"__newname": other_shift,
					"start_time": "18:00:00",
					"end_time": "22:00:00",
					"enable_auto_attendance": 1,
					"process_attendance_after": add_days(getdate(nowdate()), -60),
				}
			).insert(ignore_permissions=True)

		frappe.db.delete("Shift Assignment", {"employee": self.absentee})
		assignment = frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": self.absentee,
				"shift_type": other_shift,
				"company": self.company,
				"start_date": add_days(getdate(nowdate()), -60),
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		assignment.submit()
		frappe.db.set_value("Employee", self.absentee, "default_shift", SHIFT)
		try:
			summary = self._run()

			self.assertIsNone(self._status(self.absentee))
			self.assertNotIn(self.absentee, self._window(summary)["marked_employees"])
		finally:
			frappe.db.set_value("Employee", self.absentee, "default_shift", None)
			frappe.db.delete("Shift Assignment", {"employee": self.absentee})
			_assign_shift(self.absentee, self.company)

	def test_shift_ownership_is_confirmed_per_employee(self):
		from upande_ta.upande_ta.api.absent_marking import _shift_belongs_to

		at = datetime.combine(getdate(self.day), datetime.min.time()) + timedelta(hours=9)

		self.assertTrue(_shift_belongs_to(self.absentee, SHIFT, at))
		self.assertFalse(_shift_belongs_to(self.absentee, "_Test TA Absent Nonexistent", at))

	def _holiday_list_assignment(self, applicable_for, assigned_to, holiday_list):
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": applicable_for,
				"assigned_to": assigned_to,
				"holiday_list": holiday_list,
				"from_date": add_days(self.day, -30),
			}
		).insert(ignore_permissions=True)
		doc.submit()
		self.addCleanup(lambda: frappe.db.delete("Holiday List Assignment", {"name": doc.name}))
		return doc

	def test_employee_holiday_list_assignment_decides_the_week_off(self):
		"""The live shape on kaitet16: Bulk Week Off assigns the rest days
		through a dated Holiday List Assignment, and that must outrank the
		list still sitting on the Employee record."""
		self._holiday_list_assignment("Employee", self.absentee, REST_HOLIDAY_LIST)

		summary = self._run()

		self.assertIsNone(self._status(self.absentee))
		self.assertGreaterEqual(self._window(summary)["rest_day"], 1)

	def test_company_wide_assignment_does_not_authorise_marking(self):
		"""A company-level assignment is a public holiday list. It cannot say
		whether the day is this employee's week off, so nobody is marked on it."""
		frappe.db.set_value("Employee", self.absentee, "holiday_list", None)
		self._holiday_list_assignment("Company", self.company, WORK_HOLIDAY_LIST)
		self.addCleanup(frappe.db.set_value, "Employee", self.absentee, "holiday_list", WORK_HOLIDAY_LIST)

		summary = self._run()

		self.assertIsNone(self._status(self.absentee))
		self.assertEqual(self._window(summary)["no_week_off_list"], 1)

	def test_maximum_days_back_caps_a_typed_range(self):
		"""The cap is a setting, not a constant: a range wider than it is
		trimmed back to the most recent days."""
		wide_from = add_days(self.day, -20)

		summary = process_absentees(
			from_date=wide_from,
			to_date=self.day,
			dry_run=1,
			settings=frappe._dict({**self.settings, "absent_max_lookback_days": 2}),
			now=self.due_at,
		)

		self.assertEqual(summary["from_date"], str(add_days(self.day, -1)))
		self.assertEqual(summary["to_date"], str(self.day))

	def test_maximum_days_back_of_zero_means_no_cap(self):
		summary = process_absentees(
			from_date=add_days(self.day, -20),
			to_date=self.day,
			dry_run=1,
			settings=frappe._dict({**self.settings, "absent_max_lookback_days": 0}),
			now=self.due_at,
		)

		self.assertEqual(summary["from_date"], str(add_days(self.day, -20)))

	def test_lookback_of_one_day_covers_that_day(self):
		summary = process_absentees(
			dry_run=1,
			settings=frappe._dict({**self.settings, "absent_lookback_days": 0}),
			now=self.due_at,
			to_date=self.day,
		)

		self.assertEqual(summary["from_date"], str(self.day))

	def test_batch_size_comes_from_the_settings(self):
		summary = self._run(absent_batch_size=1)

		self.assertEqual(summary["batch_size"], 1)
		self.assertEqual(self._status(self.absentee), "Absent")

	def test_zero_grace_marks_as_soon_as_the_shift_ends(self):
		"""A blank grace period is an answer, not a missing value: mark at the
		shift's end rather than falling back to some number in the code."""
		at_shift_end = datetime.combine(getdate(self.day), datetime.min.time()) + timedelta(hours=17)

		summary = self._run(now=at_shift_end, absent_grace_minutes=0)

		self.assertEqual(self._window(summary)["due_at"], str(at_shift_end))
		self.assertEqual(self._status(self.absentee), "Absent")
