# Copyright (c) 2026, Upande LTD and Contributors

"""The payroll window the Monthly Attendance Sheet and the dashboard default to.

Pure date arithmetic, so none of it needs a site: the window is worked out
from a date and two day-of-month numbers and nothing else.
"""

import datetime
import unittest

from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import payroll_window

#: The two cycles actually configured on Kaitet, plus the code's own fallback.
CYCLES = ((20, 19), (21, 20), (23, 22))


class TestPayrollWindow(unittest.TestCase):
	def test_it_rolls_the_day_the_period_closes(self):
		"""The bug this replaced: the window stayed pinned to the period that
		had just ended, so the day after payroll closed every default still
		showed the month before."""
		# 20th to the 19th: on the 19th the old window is still the live one
		self.assertEqual(
			payroll_window(datetime.date(2026, 9, 19), 20, 19),
			(datetime.date(2026, 8, 20), datetime.date(2026, 9, 19)),
		)
		# and on the 20th the next one has begun
		self.assertEqual(
			payroll_window(datetime.date(2026, 9, 20), 20, 19),
			(datetime.date(2026, 9, 20), datetime.date(2026, 10, 19)),
		)
		# a few days in, it is still that new one — not the one that closed
		self.assertEqual(
			payroll_window(datetime.date(2026, 9, 23), 20, 19),
			(datetime.date(2026, 9, 20), datetime.date(2026, 10, 19)),
		)

	def test_the_other_cycles_roll_the_same_way(self):
		self.assertEqual(
			payroll_window(datetime.date(2026, 9, 23), 21, 20),
			(datetime.date(2026, 9, 21), datetime.date(2026, 10, 20)),
		)
		self.assertEqual(
			payroll_window(datetime.date(2026, 9, 23), 23, 22),
			(datetime.date(2026, 9, 23), datetime.date(2026, 10, 22)),
		)

	def test_it_crosses_the_turn_of_the_year(self):
		for day in (datetime.date(2026, 12, 25), datetime.date(2027, 1, 10)):
			with self.subTest(day=day):
				self.assertEqual(
					payroll_window(day, 20, 19),
					(datetime.date(2026, 12, 20), datetime.date(2027, 1, 19)),
				)

	def test_a_day_that_no_month_has_is_clamped(self):
		"""A 31st start in February is the 28th, not a crash."""
		self.assertEqual(
			payroll_window(datetime.date(2026, 3, 1), 31, 30),
			(datetime.date(2026, 2, 28), datetime.date(2026, 3, 30)),
		)

	def test_a_window_inside_one_month(self):
		self.assertEqual(
			payroll_window(datetime.date(2026, 9, 23), 1, 28),
			(datetime.date(2026, 9, 1), datetime.date(2026, 9, 28)),
		)

	def test_every_day_falls_in_its_own_window(self):
		"""Swept across seventeen months, because an off-by-one here is exactly
		what showed the wrong month."""
		for from_day, to_day in CYCLES:
			day = datetime.date(2026, 1, 1)
			while day < datetime.date(2027, 6, 1):
				start, end = payroll_window(day, from_day, to_day)
				self.assertLessEqual(start, day, f"{day} is before its window {start}..{end}")
				self.assertLessEqual(day, end, f"{day} is after its window {start}..{end}")
				day += datetime.timedelta(days=1)

	def test_the_windows_are_contiguous(self):
		"""No gap and no overlap: the next window opens the day after this one
		closes, so no date can fall between two payrolls."""
		for from_day, to_day in CYCLES:
			day = datetime.date(2026, 1, 1)
			while day < datetime.date(2027, 6, 1):
				_start, end = payroll_window(day, from_day, to_day)
				next_start, _next_end = payroll_window(end + datetime.timedelta(days=1), from_day, to_day)
				self.assertEqual(next_start, end + datetime.timedelta(days=1))
				day = end + datetime.timedelta(days=1)
