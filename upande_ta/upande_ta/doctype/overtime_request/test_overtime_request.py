# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""Tests for the Overtime Request date range.

A request covers a period rather than a single date: one day, an ISO week
picked by its number, a calendar month, or any two dates. Whichever the user
picks, ``validate`` settles ``overtime_date``/``to_date`` so everything
downstream — the overlap guard here, the per-day expansion in Bulk Overtime —
reads one pair of dates and nothing else.

The pure date arithmetic is tested without a site. The rest needs a database,
so those classes are skipped when this process is not attached to one, and
everything they create is rolled back by IntegrationTestCase.
"""

import unittest

try:
	import frappe
except ImportError:  # pragma: no cover — bare stdlib run, outside a bench
	frappe = None

try:  # frappe v16+
	from frappe.tests import IntegrationTestCase as _TestCase
except ImportError:  # pragma: no cover
	try:  # frappe v15
		from frappe.tests.utils import FrappeTestCase as _TestCase
	except ImportError:
		_TestCase = unittest.TestCase

EXTRA_TEST_RECORD_DEPENDENCIES = []
#: This site's own records are used rather than generated ones: creating a
#: "_Test Company" drags in erpnext's country fixtures, which is both slow and
#: a change to the site none of these tests need.
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Department",
	"Designation",
	"Employee",
	"Farm",
	"Holiday List",
	"Overtime Type",
	"Salary Component",
	"Shift Type",
]


PREFIX = "_Test OTR"
OT_TYPE = f"{PREFIX} Overtime Type"
COMPONENT = f"{PREFIX} Overtime"


def _site_connected() -> bool:
	if frappe is None:
		return False
	try:
		return bool(getattr(frappe.local, "site", None)) and frappe.db is not None
	except Exception:
		return False


class TestWeekAndMonthArithmetic(unittest.TestCase):
	"""ISO weeks, without a site. A week belongs to the year that numbers it,
	which is why these cases are worth pinning down."""

	def setUp(self):
		if frappe is None:
			raise unittest.SkipTest("frappe is not importable")
		from upande_ta.upande_ta.doctype.overtime_request import overtime_request as module

		self.module = module

	def test_week_label_pads_to_two_digits(self):
		self.assertEqual(self.module.week_label(5), "Week 05")
		self.assertEqual(self.module.week_label(38), "Week 38")

	def test_week_number_reads_every_shape_the_field_has_worn(self):
		self.assertEqual(self.module.week_number("Week 38"), 38)
		self.assertEqual(self.module.week_number("40"), 40)
		# the browser's own week picker, which this field used before
		self.assertEqual(self.module.week_number("2026-W38"), 38)

	def test_week_start_is_the_monday(self):
		import datetime

		for year, week, expected in [
			(2026, "Week 20", datetime.date(2026, 5, 11)),
			(2026, "Week 38", datetime.date(2026, 9, 14)),
			(2026, "Week 40", datetime.date(2026, 9, 28)),
		]:
			with self.subTest(week=week):
				start = self.module.week_start(year, week)
				self.assertEqual(start, expected)
				self.assertEqual(start.weekday(), 0, "an ISO week starts on Monday")

	def test_week_one_can_begin_in_the_previous_december(self):
		import datetime

		self.assertEqual(self.module.week_start(2026, "Week 01"), datetime.date(2025, 12, 29))

	def test_only_long_years_have_a_week_53(self):
		self.assertEqual(self.module.weeks_in_year(2026), 53)
		self.assertEqual(self.module.weeks_in_year(2025), 52)

	def test_month_start_is_the_first(self):
		import datetime

		self.assertEqual(self.module.month_start("2026-02"), datetime.date(2026, 2, 1))


class IntegrationTestOvertimeRequest(_TestCase):
	"""The range as a saved document."""

	@classmethod
	def setUpClass(cls):
		if not _site_connected():
			raise unittest.SkipTest("Overtime Request tests need a site; none is configured.")

		super().setUpClass()

		# This site's own records, not generated ones. Employee in particular
		# carries mandatory custom fields on some sites (Employee Number,
		# Unit/Division, Employee Category), and none of them are what is under
		# test here.
		cls._quieten_workflow_emails("Overtime Request")

		cls.overtime_type = frappe.db.get_value("Overtime Type", {}, "name") or cls._make_overtime_type()

		cls.company, cls.employees = cls._pick_employees()
		if len(cls.employees) < 2:
			raise unittest.SkipTest("need two active employees with no approved overtime")

	@classmethod
	def _quieten_workflow_emails(cls, *doctypes):
		"""Stop the approval workflow's e-mail from taking the submit with it.

		``process_workflow_actions`` enqueues the approver e-mail with
		``now=frappe.in_test`` (frappe/workflow/doctype/workflow_action), so
		under the test runner it runs inline inside this transaction rather
		than in a worker after commit — and when it unwinds, the submit that
		triggered it is rolled back with it, leaving a document that reports
		docstatus 1 in memory and 0 in the database.

		Production is unaffected: there the job is enqueued after commit. The
		workflow itself stays active, so these tests still exercise the part
		that matters — Approved mapping to docstatus 1.
		"""
		for doctype in doctypes:
			for name in frappe.get_all(
				"Workflow", filters={"document_type": doctype, "is_active": 1}, pluck="name"
			):
				frappe.db.set_value("Workflow", name, "send_email_alert", 0)
				frappe.clear_document_cache("Workflow", name)

	@classmethod
	def _make_overtime_type(cls) -> str:
		"""A type of our own, for a site that carries none. Nothing here is
		under test — the request only stores the link — so it is the cheapest
		type that will save: a Fixed Hourly Rate needs no Salary Structure."""
		if not frappe.db.exists("Salary Component", COMPONENT):
			frappe.get_doc(
				{
					"doctype": "Salary Component",
					"salary_component": COMPONENT,
					"type": "Earning",
					"salary_component_abbr": "TOTR",
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Overtime Type", OT_TYPE):
			frappe.get_doc(
				{
					"doctype": "Overtime Type",
					"name": OT_TYPE,
					"overtime_salary_component": COMPONENT,
					"standard_multiplier": 1.5,
					"applicable_for_weekend": 0,
					"overtime_calculation_method": "Fixed Hourly Rate",
					"hourly_rate": 100,
				}
			).insert(ignore_permissions=True)
		return OT_TYPE

	@classmethod
	def _pick_employees(cls):
		"""Two active employees of one company that no submitted request names,
		so the overlap tests cannot collide with the site's real data."""
		spoken_for = set(
			frappe.get_all(
				"Overtime Request Employee",
				filters={"parenttype": "Overtime Request", "docstatus": 1},
				pluck="employee",
			)
		)
		for company in frappe.get_all("Employee", filters={"status": "Active"}, pluck="company", distinct=True):
			free = [
				name
				for name in frappe.get_all(
					"Employee", filters={"status": "Active", "company": company}, pluck="name", limit=50
				)
				if name not in spoken_for
			]
			if len(free) >= 2:
				return company, free[:2]
		return None, []

	def _request(self, request_for, **kwargs):
		doc = frappe.get_doc(
			{
				"doctype": "Overtime Request",
				"company": self.company,
				"overtime_type": self.overtime_type,
				"request_for": request_for,
				"reason": "test",
				"default_requested_hours": 2,
				"employees": [{"employee": e, "requested_hours": 2} for e in self.employees],
				**kwargs,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	# ──────────────────────────────────────────────────────────────────────
	# The range each kind of request settles on
	# ──────────────────────────────────────────────────────────────────────

	def test_single_day_collapses_the_range(self):
		doc = self._request("Single Day", overtime_date="2030-09-24")
		self.assertEqual(str(doc.to_date), "2030-09-24")
		self.assertEqual(doc.number_of_days, 1)

	def test_a_week_number_fills_monday_to_sunday(self):
		for week, year, expected in [
			("Week 20", 2030, ("2030-05-13", "2030-05-19")),
			("Week 38", 2030, ("2030-09-16", "2030-09-22")),
			("Week 40", 2030, ("2030-09-30", "2030-10-06")),
		]:
			with self.subTest(week=week):
				doc = self._request("Week", week=week, week_year=year)
				self.assertEqual((str(doc.overtime_date), str(doc.to_date)), expected)
				self.assertEqual(doc.number_of_days, 7)
				self.assertEqual(doc.week, week)
				self.assertEqual(doc.week_year, year)

	def test_week_one_spans_the_new_year(self):
		doc = self._request("Week", week="Week 01", week_year=2030)
		self.assertEqual((str(doc.overtime_date), str(doc.to_date)), ("2029-12-31", "2030-01-06"))

	def test_week_53_is_refused_by_name_in_a_short_year(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._request("Week", week="Week 53", week_year=2030)
		message = frappe.utils.strip_html(str(caught.exception))
		self.assertIn("Week 53", message)
		self.assertIn("52", message, "the message should say how far that year runs")

	def test_an_unchosen_week_is_the_one_we_are_in(self):
		from frappe.utils import add_days, getdate, today

		doc = self._request("Week")
		monday = add_days(getdate(today()), -getdate(today()).weekday())
		self.assertEqual(getdate(doc.overtime_date), monday)
		self.assertEqual(doc.number_of_days, 7)

	def test_a_bare_date_snaps_to_its_week(self):
		doc = self._request("Week", overtime_date="2030-10-01")  # a Tuesday
		self.assertEqual((str(doc.overtime_date), str(doc.to_date)), ("2030-09-30", "2030-10-06"))
		self.assertEqual((doc.week, doc.week_year), ("Week 40", 2030))

	def test_a_month_runs_first_to_last(self):
		doc = self._request("Month", month="2030-11")
		self.assertEqual((str(doc.overtime_date), str(doc.to_date)), ("2030-11-01", "2030-11-30"))
		self.assertEqual(doc.number_of_days, 30)

	def test_a_date_range_is_kept_as_given(self):
		doc = self._request("Date Range", overtime_date="2030-12-01", to_date="2030-12-05")
		self.assertEqual(doc.number_of_days, 5)

	def test_the_unused_period_fields_are_cleared(self):
		doc = self._request("Month", month="2030-11")
		self.assertIsNone(doc.week)
		self.assertIsNone(doc.week_year)

	# ──────────────────────────────────────────────────────────────────────
	# Guards
	# ──────────────────────────────────────────────────────────────────────

	def test_a_backwards_range_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._request("Date Range", overtime_date="2030-12-10", to_date="2030-12-01")

	def test_an_absurd_range_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._request("Date Range", overtime_date="2030-01-01", to_date="2036-01-01")

	def test_hours_are_per_day_across_every_employee(self):
		doc = self._request("Week", week="Week 20", week_year=2030)
		# 2 employees x 2 hours x 7 days
		self.assertEqual(doc.number_of_employees, 2)
		self.assertEqual(doc.total_requested_hours, 28)

	def test_overlapping_approvals_clash(self):
		week = self._request("Week", week="Week 20", week_year=2030)  # 13 to 19 May 2030
		week.submit()

		overlapping = self._request("Date Range", overtime_date="2030-05-19", to_date="2030-05-22")
		with self.assertRaises(frappe.ValidationError) as caught:
			overlapping.submit()
		self.assertIn("Already approved", frappe.utils.strip_html(str(caught.exception)))

	def test_a_neighbouring_range_does_not_clash(self):
		week = self._request("Week", week="Week 22", week_year=2030)  # 27 May to 2 June 2030
		week.submit()

		adjacent = self._request("Date Range", overtime_date="2030-06-03", to_date="2030-06-05")
		adjacent.submit()  # the day after the week ends is free
		self.assertEqual(adjacent.docstatus, 1)

	def test_an_unsubmitted_request_does_not_block_anything(self):
		self._request("Week", week="Week 24", week_year=2030)  # left as a draft

		approved = self._request("Week", week="Week 24", week_year=2030)
		approved.submit()
		self.assertEqual(approved.docstatus, 1)
