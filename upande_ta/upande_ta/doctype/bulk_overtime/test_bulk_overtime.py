# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""Unit tests for ``upande_ta.upande_ta.overtime_engine`` — the arithmetic
Bulk Overtime pays by. Site-free: plain ``unittest``, no database, so they run
under ``bench run-tests`` and under a bare ``python -m unittest`` alike.

The end-to-end path (request -> Bulk Overtime -> Overtime Slip) is in
``test_bulk_overtime_end_to_end.py``.
"""

import datetime
import unittest

from upande_ta.upande_ta import overtime_engine as engine
from upande_ta.upande_ta.overtime_engine import (
	CAPPED,
	MATCHED,
	NO_ATTENDANCE,
	NO_CLOCK_OUT,
	NO_SHIFT,
	PUBLIC_HOLIDAY,
	REST_DAY,
	WORKED_LESS,
	WORKING_DAY,
	biometric_overtime,
	settle,
	settle_week,
	shift_length_hours,
	split_across_working_days,
	swapped_rest_days,
)

# Frappe crawls Link targets for test records unless told not to; this module
# needs none of them.
#: Generating a "_Test Company" pulls in erpnext's country fixtures, which this
#: site cannot install and none of these tests need.
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Additional Salary",
	"Attendance",
	"Company",
	"Department",
	"Designation",
	"Employee",
	"Farm",
	"Gender",
	"Holiday List",
	"Overtime Request",
	"Overtime Slip",
	"Overtime Type",
	"Salary Component",
	"Salary Structure",
	"Shift Type",
]


class TestShiftLength(unittest.TestCase):
	def test_day_shift(self):
		# KR - Post Harvest: 07:45 -> 16:40
		self.assertAlmostEqual(
			shift_length_hours(
				datetime.timedelta(hours=7, minutes=45), datetime.timedelta(hours=16, minutes=40)
			),
			8.9167,
			places=3,
		)

	def test_night_shift_wraps_midnight(self):
		self.assertEqual(shift_length_hours(datetime.timedelta(hours=22), datetime.timedelta(hours=6)), 8)

	def test_accepts_time_and_strings(self):
		self.assertEqual(shift_length_hours(datetime.time(8), "17:00:00"), 9)

	def test_missing_or_zero_length_is_none(self):
		self.assertIsNone(shift_length_hours(None, datetime.time(17)))
		self.assertIsNone(shift_length_hours("08:00", "08:00"))


class TestBiometricOvertime(unittest.TestCase):
	def test_working_day_counts_hours_beyond_the_shift(self):
		self.assertEqual(biometric_overtime(11, 9, WORKING_DAY), 2)

	def test_working_day_under_the_shift_is_zero(self):
		self.assertEqual(biometric_overtime(7.5, 9, WORKING_DAY), 0)

	def test_rest_day_and_holiday_count_every_hour(self):
		self.assertEqual(biometric_overtime(6, 9, REST_DAY), 6)
		self.assertEqual(biometric_overtime(6, 9, PUBLIC_HOLIDAY), 6)

	def test_part_hours_are_kept(self):
		self.assertEqual(biometric_overtime(9.5, 9, WORKING_DAY), 0.5)
		self.assertEqual(biometric_overtime(9 + 25 / 60, 9, WORKING_DAY), 0.42)

	def test_daily_cap_from_the_overtime_type(self):
		"""Maximum Overtime Hours Allowed on the Overtime Type; 0 means no cap."""
		self.assertEqual(biometric_overtime(14, 8, WORKING_DAY, maximum_hours=4), 4)
		self.assertEqual(biometric_overtime(14, 8, WORKING_DAY, maximum_hours=0), 6)

	def test_bad_working_hours_are_zero(self):
		self.assertEqual(biometric_overtime(-3, 8, REST_DAY), 0)
		self.assertEqual(biometric_overtime(None, 8, REST_DAY), 0)

	def test_unknown_day_type_is_an_error(self):
		with self.assertRaises(ValueError):
			biometric_overtime(10, 8, "Sunday")

	def test_late_arrival_is_made_up_before_overtime(self):
		# 08:00-17:00 shift, in 09:00, out 19:00: the first hour past 17:00
		# makes up the late start, the second is overtime
		self.assertEqual(biometric_overtime(10, 9, WORKING_DAY), 1)

	def test_time_before_the_shift_is_not_overtime(self):
		# in 07:00, out 17:00: ten hours on the clock, none of it overtime
		self.assertEqual(biometric_overtime(10, 9, WORKING_DAY, early_hours=1), 0)
		# in 07:00, out 18:00: only the hour past the end counts
		self.assertEqual(biometric_overtime(11, 9, WORKING_DAY, early_hours=1), 1)

	def test_early_hours_do_not_touch_rest_days(self):
		self.assertEqual(biometric_overtime(6, 9, REST_DAY, early_hours=2), 6)
		self.assertEqual(biometric_overtime(6, 9, PUBLIC_HOLIDAY, early_hours=2), 6)


class TestHoursBeforeShift(unittest.TestCase):
	start = datetime.datetime(2026, 9, 29, 8)

	def test_early_arrival(self):
		self.assertEqual(engine.hours_before_shift(datetime.datetime(2026, 9, 29, 7, 30), self.start), 0.5)

	def test_on_time_or_late_is_zero(self):
		self.assertEqual(engine.hours_before_shift(self.start, self.start), 0)
		self.assertEqual(engine.hours_before_shift(datetime.datetime(2026, 9, 29, 9), self.start), 0)

	def test_missing_punch_is_zero(self):
		self.assertEqual(engine.hours_before_shift(None, self.start), 0)
		self.assertEqual(engine.hours_before_shift(self.start, None), 0)


class TestSettle(unittest.TestCase):
	"""The pay rule: the lower of requested and biometric."""

	def settle(self, requested, biometric, attendance=True, hours=True, shift=True):
		return settle(requested, biometric, has_attendance=attendance, has_hours=hours, has_shift=shift)

	def test_worked_more_than_requested_is_capped(self):
		self.assertEqual(self.settle(2, 3.5), (2, CAPPED))

	def test_worked_less_pays_what_was_worked(self):
		self.assertEqual(self.settle(3, 1.5), (1.5, WORKED_LESS))

	def test_exact(self):
		self.assertEqual(self.settle(2, 2), (2, MATCHED))

	def test_no_overtime_worked(self):
		self.assertEqual(self.settle(2, 0), (0, WORKED_LESS))

	def test_no_attendance_pays_nothing(self):
		self.assertEqual(self.settle(2, 0, attendance=False, hours=False), (0, NO_ATTENDANCE))

	def test_missing_clock_out_pays_nothing(self):
		self.assertEqual(self.settle(2, 0, attendance=True, hours=False), (0, NO_CLOCK_OUT))

	def test_no_shift_length_pays_nothing(self):
		"""Nothing to measure "beyond the shift" against."""
		self.assertEqual(self.settle(2, 0, shift=False), (0, NO_SHIFT))


class TestSettleWeek(unittest.TestCase):
	"""A Week request pays the week's total, not an even share each day."""

	SHARES = [1.25] * 6

	def test_a_long_day_makes_up_for_a_short_one(self):
		# 7.5 h for the week; 1.47, 4.06 and 3.47 over the shift, then nothing
		settled = settle_week(7.5, self.SHARES, [1.47, 4.06, 3.47, 0, 0, 0])
		self.assertEqual([hours for hours, _status in settled], [1.47, 4.06, 1.97, 0, 0, 0])
		self.assertEqual([status for _hours, status in settled][1:3], [MATCHED, CAPPED])

	def test_worked_less_than_the_week_pays_everything_worked(self):
		settled = settle_week(7.5, self.SHARES, [0.64, 3.18, 0, 0, 0, 0])
		self.assertEqual([hours for hours, _status in settled], [0.64, 3.18, 0, 0, 0, 0])
		self.assertEqual(settled[0][1], WORKED_LESS)
		self.assertEqual(settled[1][1], MATCHED)

	def test_the_week_total_is_never_exceeded(self):
		settled = settle_week(7.5, self.SHARES, [4] * 6)
		self.assertAlmostEqual(sum(hours for hours, _status in settled), 7.5)
		self.assertEqual(settled[-1], (0, CAPPED))

	def test_hours_paid_elsewhere_come_off_the_allowance(self):
		self.assertEqual(settle_week(0, [1.25], [3]), [(0, CAPPED)])
		self.assertEqual(settle_week(-1, [1.25], [3]), [(0, CAPPED)])

	def test_lengths_must_match(self):
		with self.assertRaises(ValueError):
			settle_week(5, [1, 1], [1])


class TestSwappedRestDays(unittest.TestCase):
	"""A rest day worked for a working day off in the same week is a moved off."""

	MONDAY = datetime.date(2026, 9, 7)

	def week(self, *days):
		"""(day type, worked, off) from Monday on."""
		return [
			(self.MONDAY + datetime.timedelta(days=i), kind, worked, off)
			for i, (kind, worked, off) in enumerate(days)
		]

	def test_a_day_off_moves_the_rest_day(self):
		week = self.week(
			(WORKING_DAY, True, False),
			(WORKING_DAY, False, True),
			(WORKING_DAY, True, False),
			(REST_DAY, True, False),
		)
		self.assertEqual(swapped_rest_days(week), {week[3][0]: week[1][0]})

	def test_a_rest_day_worked_with_no_day_off_is_overtime(self):
		week = self.week((WORKING_DAY, True, False), (REST_DAY, True, False))
		self.assertEqual(swapped_rest_days(week), {})

	def test_a_day_off_without_working_the_rest_day_is_no_swap(self):
		week = self.week((WORKING_DAY, False, True), (REST_DAY, False, False))
		self.assertEqual(swapped_rest_days(week), {})

	def test_each_day_off_pairs_with_one_rest_day(self):
		week = self.week(
			(WORKING_DAY, False, True),
			(REST_DAY, True, False),
			(PUBLIC_HOLIDAY, True, False),
			(REST_DAY, True, False),
		)
		self.assertEqual(swapped_rest_days(week), {week[1][0]: week[0][0]})

	def test_a_day_on_leave_or_ahead_is_not_a_day_off(self):
		week = self.week((WORKING_DAY, False, False), (REST_DAY, True, False))
		self.assertEqual(swapped_rest_days(week), {})


class TestEngineIsFrameworkFree(unittest.TestCase):
	def test_no_frappe_import(self):
		import inspect

		self.assertNotIn("import frappe", inspect.getsource(engine))


if __name__ == "__main__":
	unittest.main()


class TestSplitAcrossWorkingDays(unittest.TestCase):
	"""A Week request's total, shared out over the working days of the week."""

	WEEK = [WORKING_DAY] * 6 + [REST_DAY]

	def test_six_working_days_share_evenly(self):
		self.assertEqual(split_across_working_days(12, self.WEEK), [2, 2, 2, 2, 2, 2, 0])

	def test_rest_days_and_holidays_get_nothing(self):
		week = [WORKING_DAY, PUBLIC_HOLIDAY, WORKING_DAY, REST_DAY]
		self.assertEqual(split_across_working_days(5, week), [2.5, 0, 2.5, 0])

	def test_rounding_lands_on_the_last_working_day(self):
		shares = split_across_working_days(10, self.WEEK)
		self.assertEqual(shares[:5], [1.67] * 5)
		self.assertEqual(shares[5], 1.65)
		self.assertAlmostEqual(sum(shares), 10, places=6)

	def test_a_week_with_no_working_day_shares_nothing(self):
		self.assertEqual(split_across_working_days(8, [REST_DAY, PUBLIC_HOLIDAY]), [0, 0])

	def test_nothing_requested_is_nothing_shared(self):
		self.assertEqual(split_across_working_days(0, self.WEEK), [0] * 7)

	def test_an_unknown_day_type_is_refused(self):
		with self.assertRaises(ValueError):
			split_across_working_days(4, ["Half Day"])
