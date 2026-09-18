# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""Unit tests for the Holiday Assignment Tool segment engine.

``plan_segments`` is pure, so every test here runs without a site. The base
class is resolved defensively: ``frappe.tests.IntegrationTestCase`` exists on
v16+, ``frappe.tests.utils.FrappeTestCase`` on v15 (and v17 removed the latter),
and plain ``unittest.TestCase`` when frappe is not importable at all — the cases
below need none of it.
"""

import datetime
import unittest

try:  # frappe v16+
	from frappe.tests import IntegrationTestCase as _TestCase
except ImportError:  # pragma: no cover
	try:  # frappe v15
		from frappe.tests.utils import FrappeTestCase as _TestCase
	except ImportError:  # no frappe at all — pure stdlib run
		_TestCase = unittest.TestCase

from upande_ta.upande_ta import holiday_segments
from upande_ta.upande_ta.holiday_segments import plan_segments

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded.
# Nothing here touches the database, so skip the crawl entirely.
EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Department",
	"Designation",
	"Employee",
	"Farm",
	"Holiday List",
]


def d(day, month=9, year=2026):
	return datetime.date(year, month, day)


BASE = "Sunday Week Off 2026"
OTHER = "Wednesday Week Off 2026"
PRIOR = "Saturday & Sunday Week Off 2026"


class IntegrationTestHolidayAssignmentTool(_TestCase):
	"""Segment planning rules. Pure — no site, no database."""

	def test_closed_range_no_exceptions(self):
		"""Two boundaries: move on from_date, restore the day after to_date."""
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [], PRIOR),
			[(d(1), BASE), (d(1, 10), PRIOR)],
		)

	def test_open_ended_emits_no_restore(self):
		"""to_date None: open-ended, nothing is restored (Bulk Week Off behaviour)."""
		self.assertEqual(plan_segments(BASE, d(1), None, [], PRIOR), [(d(1), BASE)])

	def test_no_prior_list_emits_no_restore(self):
		"""A restore boundary is never emitted with a null holiday list."""
		self.assertEqual(plan_segments(BASE, d(1), d(30), [], None), [(d(1), BASE)])

	def test_single_exception_inside_range(self):
		"""The exception day, then the base list resumes the next day."""
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [(d(16), OTHER)], PRIOR),
			[(d(1), BASE), (d(16), OTHER), (d(17), BASE), (d(1, 10), PRIOR)],
		)

	def test_exception_on_from_date(self):
		"""The exception wins the shared from_date boundary; base starts the next day."""
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [(d(1), OTHER)], PRIOR),
			[(d(1), OTHER), (d(2), BASE), (d(1, 10), PRIOR)],
		)

	def test_exception_on_to_date(self):
		"""resume-base at to_date+1 and restore at to_date+1 collide; restore wins."""
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [(d(30), OTHER)], PRIOR),
			[(d(1), BASE), (d(30), OTHER), (d(1, 10), PRIOR)],
		)

	def test_exception_on_to_date_without_prior(self):
		"""Same collision, but with no prior list the resume-base boundary stands."""
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [(d(30), OTHER)], None),
			[(d(1), BASE), (d(30), OTHER), (d(1, 10), BASE)],
		)

	def test_consecutive_exceptions(self):
		"""Day two's exception must beat day one's resume-base boundary."""
		third = "Friday Week Off 2026"
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [(d(16), OTHER), (d(17), third)], PRIOR),
			[
				(d(1), BASE),
				(d(16), OTHER),
				(d(17), third),
				(d(18), BASE),
				(d(1, 10), PRIOR),
			],
		)

	def test_consecutive_exceptions_same_list(self):
		"""Two adjacent days on the same list collapse to one boundary."""
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [(d(16), OTHER), (d(17), OTHER)], PRIOR),
			[(d(1), BASE), (d(16), OTHER), (d(18), BASE), (d(1, 10), PRIOR)],
		)

	def test_exception_equal_to_base_is_a_noop(self):
		"""An exception naming the base list must emit no extra records."""
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), [(d(16), BASE)], PRIOR),
			[(d(1), BASE), (d(1, 10), PRIOR)],
		)

	def test_prior_equal_to_base_collapses_restore(self):
		"""Restoring the list already in force changes nothing."""
		self.assertEqual(plan_segments(BASE, d(1), d(30), [], BASE), [(d(1), BASE)])

	def test_single_day_range(self):
		self.assertEqual(
			plan_segments(BASE, d(10), d(10), [], PRIOR),
			[(d(10), BASE), (d(11), PRIOR)],
		)

	def test_single_day_range_with_exception(self):
		"""from_date and to_date+1 both collide; later generation wins each."""
		self.assertEqual(
			plan_segments(BASE, d(10), d(10), [(d(10), OTHER)], PRIOR),
			[(d(10), OTHER), (d(11), PRIOR)],
		)

	def test_exceptions_are_sorted_by_the_function(self):
		"""Callers may pass rows in grid order; the engine sorts them."""
		third = "Friday Week Off 2026"
		unsorted = [(d(20), third), (d(5), OTHER), (d(16), OTHER)]
		self.assertEqual(
			plan_segments(BASE, d(1), d(30), unsorted, PRIOR),
			[
				(d(1), BASE),
				(d(5), OTHER),
				(d(6), BASE),
				(d(16), OTHER),
				(d(17), BASE),
				(d(20), third),
				(d(21), BASE),
				(d(1, 10), PRIOR),
			],
		)

	def test_exceptions_outside_the_range_are_processed_not_rejected(self):
		"""The pure function does not police the range — validate() does. It must
		still return a legal (sorted, deduped) boundary list."""
		result = plan_segments(BASE, d(10), d(20), [(d(1), OTHER), (d(25), OTHER)], PRIOR)
		dates = [date for date, _list in result]
		self.assertEqual(dates, sorted(dates))
		self.assertEqual(len(dates), len(set(dates)))

	def test_no_duplicate_dates_and_no_repeated_list(self):
		"""The two invariants every caller depends on: {assigned_to, from_date} is
		unique in Holiday List Assignment, and a record that changes nothing is
		waste."""
		result = plan_segments(
			BASE, d(1), d(30), [(d(1), OTHER), (d(2), BASE), (d(30), OTHER)], PRIOR
		)
		dates = [date for date, _list in result]
		self.assertEqual(len(dates), len(set(dates)))
		lists = [holiday_list for _date, holiday_list in result]
		self.assertTrue(all(a != b for a, b in zip(lists, lists[1:], strict=False)))

	def test_accepts_iso_strings_and_datetimes(self):
		"""No getdate() needed: strings and datetimes coerce to plain dates."""
		result = plan_segments(
			BASE,
			"2026-09-01",
			"2026-09-30",
			[{"exception_date": datetime.datetime(2026, 9, 16, 8, 30), "holiday_list": OTHER}],
			PRIOR,
		)
		self.assertEqual(
			result,
			[(d(1), BASE), (d(16), OTHER), (d(17), BASE), (d(1, 10), PRIOR)],
		)
		self.assertTrue(all(isinstance(date, datetime.date) for date, _list in result))

	def test_missing_inputs_return_nothing(self):
		self.assertEqual(plan_segments(None, d(1), d(30), [], PRIOR), [])
		self.assertEqual(plan_segments(BASE, None, d(30), [], PRIOR), [])
		self.assertEqual(plan_segments(BASE, d(1), d(30), None, None), [(d(1), BASE)])

	def test_blank_exception_rows_are_ignored(self):
		"""A half-filled grid row must not emit a boundary with a null list."""
		self.assertEqual(
			plan_segments(
				BASE, d(1), d(30), [(d(16), None), (None, OTHER), (d(16), OTHER)], PRIOR
			),
			[(d(1), BASE), (d(16), OTHER), (d(17), BASE), (d(1, 10), PRIOR)],
		)

	def test_september_worked_example(self):
		"""The case this doctype was built for."""
		self.assertEqual(
			plan_segments(
				"Sunday Week Off 2026",
				datetime.date(2026, 9, 1),
				datetime.date(2026, 9, 30),
				[(datetime.date(2026, 9, 16), "Wednesday Week Off 2026")],
				"Saturday & Sunday Week Off 2026",
			),
			[
				(datetime.date(2026, 9, 1), "Sunday Week Off 2026"),
				(datetime.date(2026, 9, 16), "Wednesday Week Off 2026"),
				(datetime.date(2026, 9, 17), "Sunday Week Off 2026"),
				(datetime.date(2026, 10, 1), "Saturday & Sunday Week Off 2026"),
			],
		)

	def test_engine_module_is_frappe_free(self):
		"""Guards the reason this module exists: it must stay importable and
		testable without a site."""
		self.assertFalse(
			[name for name, value in vars(holiday_segments).items() if name == "frappe"]
		)
		self.assertEqual(
			getattr(holiday_segments.plan_segments, "__module__", ""),
			"upande_ta.upande_ta.holiday_segments",
		)


if __name__ == "__main__":
	unittest.main()
