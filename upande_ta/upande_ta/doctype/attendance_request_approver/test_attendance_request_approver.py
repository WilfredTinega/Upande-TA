# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""How Mark Attendance splits picked dates into Attendance Requests: one per
run of consecutive dates, since a request is a single range."""

import datetime
import unittest

IGNORE_TEST_RECORD_DEPENDENCIES = ["Employee", "Farm", "User"]


def d(day):
	return datetime.date(2026, 9, day)


class TestRequestRuns(unittest.TestCase):
	def setUp(self):
		from upande_ta.upande_ta.api.attendance_request_flow import _runs

		self.runs = _runs

	def test_consecutive_dates_are_one_request(self):
		self.assertEqual(self.runs([d(19), d(20), d(21)]), [(d(19), d(21))])

	def test_a_gap_starts_another(self):
		self.assertEqual(self.runs([d(19), d(20), d(23)]), [(d(19), d(20)), (d(23), d(23))])

	def test_a_single_date(self):
		self.assertEqual(self.runs([d(24)]), [(d(24), d(24))])

	def test_across_a_month_end(self):
		self.assertEqual(
			self.runs([datetime.date(2026, 9, 30), datetime.date(2026, 10, 1)]),
			[(datetime.date(2026, 9, 30), datetime.date(2026, 10, 1))],
		)
