# Copyright (c) 2026, Upande LTD and Contributors

"""The payroll window the Monthly Attendance Sheet and the dashboard default to.

Pure date arithmetic, so none of it needs a site: the window is worked out
from a date and two day-of-month numbers and nothing else.
"""

import datetime
import unittest

import frappe

from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import (
    DEFAULT_PAYROLL_DAYS,
    payroll_window,
    payroll_window_for,
)

#: Plain unittest, not IntegrationTestCase: living in a doctype folder makes
#: frappe generate test records for Biometric Setting, which drags in Sales
#: Invoice fixtures. Nothing here needs a fixture — it writes one Single and
#: rolls it back.

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


class TestPayrollWindowPerCompany(unittest.TestCase):
	"""Resolving the window for a company, from the Payroll Dates table.

	The same answer has to come back on the TA dashboard, the Attendance
	Insights register and the Monthly Attendance Sheet — they all read this one
	function, so a company's cycle means one thing everywhere.
	"""

	def setUp(self):
		self.settings = frappe.get_single("Biometric Setting")
		if not self.settings.meta.has_field("attendance_payroll_periods"):
			raise unittest.SkipTest("this site has no Payroll Dates table")

	def tearDown(self):
		# the rows written here are a fixture, not a change to the site
		frappe.db.rollback()
		frappe.clear_document_cache("Biometric Setting", "Biometric Setting")

	def _set_rows(self, rows):
		self.settings.set("attendance_payroll_periods", [])
		for company, from_day, to_day in rows:
			self.settings.append(
				"attendance_payroll_periods",
				{"company": company, "from": str(from_day), "to": str(to_day)},
			)
		# ignore_mandatory: a bare site (CI) has none of Biometric Setting's
		# connection fields filled in, and none of them are what is under test
		# here — only the Payroll Dates rows are
		self.settings.flags.ignore_mandatory = True
		self.settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Biometric Setting", "Biometric Setting")

	def test_each_company_gets_its_own_cycle(self):
		companies = frappe.get_all("Company", pluck="name", limit=2)
		if len(companies) < 2:
			raise unittest.SkipTest("need two companies")
		first, second = companies
		self._set_rows([(first, 20, 19), (second, 21, 20)])

		on = datetime.date(2026, 9, 23)
		self.assertEqual(
			payroll_window_for(first, on), (datetime.date(2026, 9, 20), datetime.date(2026, 10, 19))
		)
		self.assertEqual(
			payroll_window_for(second, on), (datetime.date(2026, 9, 21), datetime.date(2026, 10, 20))
		)

	def test_a_blank_company_row_is_the_site_default(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		self._set_rows([(None, 15, 14)])

		on = datetime.date(2026, 9, 23)
		expected = (datetime.date(2026, 9, 15), datetime.date(2026, 10, 14))
		self.assertEqual(payroll_window_for(None, on), expected)
		# a company with no row of its own falls through to it
		self.assertEqual(payroll_window_for(companies[0], on), expected)

	def test_nothing_configured_falls_back(self):
		self._set_rows([])
		on = datetime.date(2026, 9, 23)
		self.assertEqual(payroll_window_for(None, on), payroll_window(on, *DEFAULT_PAYROLL_DAYS))

	def test_a_cycle_no_month_can_hold_is_clamped_not_raised(self):
		"""date.replace(day=31) in February is a ValueError; the window that the
		Insights register used to build by hand would have raised here."""
		self._set_rows([(None, 31, 30)])
		self.assertEqual(
			payroll_window_for(None, datetime.date(2026, 2, 15)),
			(datetime.date(2026, 1, 31), datetime.date(2026, 2, 28)),
		)
