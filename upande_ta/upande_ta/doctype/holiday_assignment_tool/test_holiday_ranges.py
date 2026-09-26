# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""The holiday-list range rules upande_ta applies on every HRMS version."""

import datetime
import unittest

import frappe

from upande_ta.upande_ta.holiday_ranges import (
	build_effective_date_ranges_for_holiday_assignments as effective,
)
from upande_ta.upande_ta.holiday_ranges import (
	fill_employee_holiday_list_date_gaps_with_company_holiday_list as fill,
)

IGNORE_TEST_RECORD_DEPENDENCIES = ["Company", "Employee", "Holiday List"]


def d(day, month=3):
	return datetime.date(2030, month, day)


def row(holiday_list, from_date, list_to=None):
	return frappe._dict(holiday_list=holiday_list, from_date=from_date, holiday_list_to_date=list_to)


class TestHolidayRanges(unittest.TestCase):
	def test_an_assignment_runs_until_the_next_starts(self):
		got = effective({"e": [row("A", d(1)), row("B", d(10))]}, d(1), d(31))["e"]
		self.assertEqual(
			got,
			[
				{"holiday_list": "A", "from_date": d(1), "to_date": d(9)},
				{"holiday_list": "B", "from_date": d(10), "to_date": d(31)},
			],
		)

	def test_the_lists_own_end_cuts_it_short(self):
		got = effective({"e": [row("A", d(1), list_to=d(15))]}, d(1), d(31))["e"]
		self.assertEqual(got, [{"holiday_list": "A", "from_date": d(1), "to_date": d(15)}])

	def test_ranges_are_clipped_to_the_window(self):
		got = effective({"e": [row("A", d(1, 1))]}, d(5), d(6))["e"]
		self.assertEqual(got, [{"holiday_list": "A", "from_date": d(5), "to_date": d(6)}])

	def test_an_assignment_after_the_window_is_ignored(self):
		self.assertEqual(effective({"e": [row("A", d(20))]}, d(1), d(10)), {})

	def test_company_fills_the_employees_gaps(self):
		own = [{"holiday_list": "E", "from_date": d(10), "to_date": d(15)}]
		company = [{"holiday_list": "C", "from_date": d(1), "to_date": d(31)}]
		self.assertEqual(
			[(r["holiday_list"], r["from_date"], r["to_date"]) for r in fill(own, company, d(1), d(31))],
			[("C", d(1), d(9)), ("E", d(10), d(15)), ("C", d(16), d(31))],
		)

	def test_no_employee_ranges_means_the_company_s(self):
		company = [{"holiday_list": "C", "from_date": d(1), "to_date": d(31)}]
		self.assertEqual(fill([], company, d(1), d(31)), company)
