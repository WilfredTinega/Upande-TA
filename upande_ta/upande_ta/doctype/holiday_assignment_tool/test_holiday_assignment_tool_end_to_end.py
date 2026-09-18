# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""End-to-end tests for Holiday Assignment Tool, against the real HRMS resolver.

``test_holiday_assignment_tool.py`` next door tests ``plan_segments`` in
isolation: it proves the *rule* produces the right boundaries, and it needs no
site. It cannot prove the thing the user actually cares about, which is that
those boundaries, once written as Holiday List Assignment records, make
``hrms.utils.holiday_list.get_assigned_holiday_list`` — the single function
every attendance, leave and payroll path in HRMS goes through to find an
employee's holidays — return the expected list on each date.

That is what this file asserts, by asking HRMS itself rather than by inspecting
what we wrote:

    employee is on list A
      run the Holiday Assignment Tool: base list B, D1 .. D2,
      one exception list C on date X inside the range

    as_on D1          -> B
    as_on X           -> C     the carve-out; the whole point of the tool
    as_on X + 1       -> B
    as_on D2          -> B
    as_on D2 + 1      -> A     the restore

    undo the assignment, and every one of those dates is A again.

The tool is a Single, so there is nothing to submit and nothing to cancel: the
two halves of the write side are the whitelisted document methods
``assign_holidays()`` and ``undo_assignment()``, which is what the desk form's
primary action calls and what these tests call directly.

These need a database, so the whole class is skipped when this process is not
attached to a site (see :func:`_site_connected`) — the module still imports,
and a bare ``python -m unittest`` run reports skips rather than errors.

The base class is resolved defensively for the same reason the phase-1 file
does it: ``frappe.tests.IntegrationTestCase`` is v16+,
``frappe.tests.utils.FrappeTestCase`` is v15 (and v17 removed it), and neither
exists when frappe is not importable at all.

Naming note: this module is *not* ``test_holiday_assignment_tool``, so
``bench run-tests --doctype "Holiday Assignment Tool"`` will not pick it up;
``--app upande_ta`` and ``--module`` will. That is deliberate — the pure suite
keeps the canonical name so it stays the fast, site-free default.
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
	except ImportError:  # no frappe at all — module still imports, class skips
		_TestCase = unittest.TestCase

# Frappe reads these off the *canonical* ``test_<doctype>`` module rather than
# off whichever test module triggered the crawl, so the copies in
# test_holiday_assignment_tool.py are the ones that take effect. Repeated here
# so this file is not silently dependent on that detail, and so a frappe version
# that reads the running module instead still skips the link crawl: pulling test
# records for Company/Employee/Holiday List drags half of ERPNext in and is
# exactly what the fixtures below exist to avoid.
EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Department",
	"Designation",
	"Employee",
	"Farm",
	"Holiday List",
]

PREFIX = "_Test BHA"
#: The list the employee is already on, and must be restored to.
LIST_A = f"{PREFIX} Prior List"
#: The list the document moves everyone onto for the period.
LIST_B = f"{PREFIX} Override List"
#: The one-day carve-out inside that period.
LIST_C = f"{PREFIX} Exception List"


def _site_connected() -> bool:
	"""True when this process is attached to a site with a live database.

	Evaluated at class setup rather than at import, so the module imports in any
	environment and the decision is made with whatever frappe has by the time
	the tests are about to run — under ``bench run-tests`` the site is init'd and
	connected before test modules are even discovered, but that ordering is not
	something a test file should have to bet on.
	"""
	if frappe is None:
		return False
	try:
		return bool(getattr(frappe.local, "site", None)) and frappe.db is not None
	except Exception:
		return False


def _first(doctype: str, filters: dict | None = None):
	names = frappe.get_all(doctype, filters=filters or {}, limit=1, pluck="name")
	return names[0] if names else None


class IntegrationTestHolidayAssignmentToolEndToEnd(_TestCase):
	"""The write side, checked through ``get_assigned_holiday_list``."""

	# ──────────────────────────────────────────────────────────────────────
	# Fixtures
	# ──────────────────────────────────────────────────────────────────────

	@classmethod
	def setUpClass(cls):
		if not _site_connected():
			raise unittest.SkipTest(
				"Holiday Assignment Tool end-to-end tests need a site; none is configured."
			)

		super().setUpClass()

		# Everything created from here is rolled back by the class cleanup
		# IntegrationTestCase registers, so nothing below is committed. Existing
		# masters are reused rather than created: a Company insert builds a whole
		# chart of accounts, and these tests have no opinion about any of it.
		from frappe.utils import add_days, getdate, nowdate

		cls.company = _first("Company")
		if not cls.company:
			raise unittest.SkipTest("no Company on this site")

		cls.gender = _first("Gender")
		if not cls.gender:
			raise unittest.SkipTest("no Gender records on this site")

		today = getdate(nowdate())

		# A window well clear of today, so a real Holiday List Assignment sitting
		# on today's date for a real employee cannot interact with any of this.
		cls.d1 = add_days(today, 30)
		cls.d2 = add_days(today, 44)
		cls.x = add_days(today, 37)
		cls.x_plus_1 = add_days(cls.x, 1)
		cls.d2_plus_1 = add_days(cls.d2, 1)

		# Every list must span every boundary, including the restore at
		# to_date + 1: HolidayListAssignment.validate_assignment_start_date
		# refuses a from_date outside its list's own period.
		cls.list_start = add_days(today, -365)
		cls.list_end = add_days(today, 365)

		cls.list_a = cls._ensure_holiday_list(LIST_A)
		cls.list_b = cls._ensure_holiday_list(LIST_B)
		cls.list_c = cls._ensure_holiday_list(LIST_C)

		cls.employee = cls._make_employee(add_days(today, -400))

		# The prior state the tool has to restore. Dated before the document's
		# period so it is the list in force on every date until D1.
		cls.prior_assignment = cls._assign_holiday_list(
			cls.employee, cls.list_a, add_days(today, -10)
		)

	@classmethod
	def _ensure_holiday_list(cls, name: str) -> str:
		"""A Holiday List with no holidays in it.

		The holidays themselves are irrelevant here: every assertion is about
		*which list* is in force on a date, which is decided entirely by the
		Holiday List Assignment records. An existing list is re-dated rather than
		reused as-is, so a leftover from an earlier run whose period no longer
		covers the window cannot turn these into tests of nothing.
		"""
		if frappe.db.exists("Holiday List", name):
			frappe.db.set_value(
				"Holiday List", name, {"from_date": cls.list_start, "to_date": cls.list_end}
			)
			return name

		return (
			frappe.get_doc(
				{
					"doctype": "Holiday List",
					"holiday_list_name": name,
					"from_date": cls.list_start,
					"to_date": cls.list_end,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def _make_employee(cls, date_of_joining) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": f"{PREFIX} Employee",
					"company": cls.company,
					"gender": cls.gender,
					"status": "Active",
					"date_of_birth": "1990-01-01",
					"date_of_joining": date_of_joining,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def _assign_holiday_list(cls, employee: str, holiday_list: str, from_date) -> str:
		assignment = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": employee,
				"holiday_list": holiday_list,
				"from_date": from_date,
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()
		return assignment.name

	#: Per-test savepoint. IntegrationTestCase rolls the transaction back once,
	#: at *class* cleanup — not between tests — so without this every test after
	#: the first would try to create a second Holiday List Assignment for the
	#: same employee on the same from_date and die on DuplicateAssignment.
	SAVEPOINT = "hat_end_to_end"

	def setUp(self):
		super().setUp()
		frappe.db.savepoint(self.SAVEPOINT)
		self.addCleanup(self._rollback_to_savepoint)

	def _rollback_to_savepoint(self):
		try:
			frappe.db.rollback(save_point=self.SAVEPOINT)
		except Exception:
			# The savepoint is gone because something already rolled past it;
			# the class-level rollback still cleans up.
			pass

	# ──────────────────────────────────────────────────────────────────────
	# Helpers
	# ──────────────────────────────────────────────────────────────────────

	def resolved_list(self, as_on):
		"""What HRMS says the employee's holiday list is on ``as_on``.

		Deliberately ``get_assigned_holiday_list`` and not
		``get_holiday_list_for_employee``: the latter falls back to the company's
		own assignment and then to Employee.holiday_list, which would mask a
		missing boundary as a pass.
		"""
		from hrms.utils.holiday_list import get_assigned_holiday_list

		return get_assigned_holiday_list(self.employee, as_on=as_on)

	def make_document(self, with_exception: bool = True):
		"""Load the Single, fill it in for the one employee, and run it.

		``assign_holidays`` saves the document itself — that is how the child
		rows get the database identity ``assignments_json`` is written back onto
		— so nothing here inserts or submits anything.

		The previous run's rows are cleared first: a Single is re-used, and
		although each test rolls back to its own savepoint, being explicit costs
		nothing and makes the fixture readable.

		One employee is below BATCH_THRESHOLD, so ``assign_holidays`` runs
		``create_assignments`` inline, in this transaction — no worker, and no
		need for the test to wait on anything.
		"""
		doc = frappe.get_single("Holiday Assignment Tool")
		doc.action = "Assign Holidays"
		doc.company = self.company
		doc.holiday_list = self.list_b
		doc.from_date = self.d1
		doc.to_date = self.d2

		doc.set("employees", [])
		doc.append(
			"employees",
			{
				"employee": self.employee,
				# What the desk fetch stamps on the row, and what the restore
				# boundary is built from.
				"prior_holiday_list": self.list_a,
			},
		)

		doc.set("exceptions", [])
		if with_exception:
			doc.append("exceptions", {"exception_date": self.x, "holiday_list": self.list_c})

		doc.assign_holidays()
		return doc

	def undo(self, doc):
		"""The reversal action, as the desk form's primary button calls it."""
		doc.action = "Undo Assignment"
		doc.undo_assignment()

	def assert_override_in_force(self):
		"""The five dates that define the feature."""
		self.assertEqual(self.resolved_list(self.d1), self.list_b, "period start must use the base list")
		self.assertEqual(self.resolved_list(self.x), self.list_c, "the exception date must use its own list")
		self.assertEqual(
			self.resolved_list(self.x_plus_1), self.list_b, "the base list must resume the day after"
		)
		self.assertEqual(self.resolved_list(self.d2), self.list_b, "the last day is still the base list")
		self.assertEqual(
			self.resolved_list(self.d2_plus_1), self.list_a, "the day after must restore the prior list"
		)

	def assert_restored_to_prior(self):
		for as_on in (self.d1, self.x, self.x_plus_1, self.d2, self.d2_plus_1):
			self.assertEqual(
				self.resolved_list(as_on),
				self.list_a,
				f"after the undo {as_on} must resolve back to the prior list",
			)

	# ──────────────────────────────────────────────────────────────────────
	# Tests
	# ──────────────────────────────────────────────────────────────────────

	def test_prior_list_is_in_force_before_the_document(self):
		"""The baseline the other tests are measured against: with no Bulk
		Holiday Assignment in play, every date in the window resolves to A."""
		for as_on in (self.d1, self.x, self.d2_plus_1):
			self.assertEqual(self.resolved_list(as_on), self.list_a)

	def test_assign_applies_base_list_exception_and_restore(self):
		"""Run the tool, then ask HRMS about each of the five dates."""
		self.make_document()
		self.assert_override_in_force()

	def test_assign_records_its_assignments_on_the_row(self):
		"""``assignments_json`` is the backlink the undo path unwinds from, so it
		has to survive a reload — it is written with db_set, not by save()."""
		import json

		doc = self.make_document()
		doc.reload()

		names = json.loads(doc.employees[0].assignments_json or "[]")
		# from_date, the exception, the day after the exception, the restore.
		self.assertEqual(len(names), 4, f"expected four boundaries, got {names}")
		for name in names:
			self.assertEqual(frappe.db.get_value("Holiday List Assignment", name, "docstatus"), 1)

	def test_undo_restores_every_date_to_the_prior_list(self):
		"""The whole run unwinds: nothing it created is left in force."""
		doc = self.make_document()
		self.assert_override_in_force()

		self.undo(doc)

		self.assert_restored_to_prior()

		# The prior assignment must not have been collateral damage.
		self.assertEqual(
			frappe.db.get_value("Holiday List Assignment", self.prior_assignment, "docstatus"), 1
		)

	def test_undo_clears_the_backlinks(self):
		"""An undone row must not keep pointing at records it no longer owns —
		the Single is re-used, and a second undo would otherwise try to unwind
		them twice."""
		doc = self.make_document()
		self.undo(doc)
		doc.reload()

		self.assertFalse((doc.employees[0].assignments_json or "").strip())

	def test_without_exceptions_the_carve_out_is_absent(self):
		"""The control case: no exception row means the exception date is just
		another day on the base list. Proves the C result above comes from the
		exception and not from the ordering of two identical assignments."""
		self.make_document(with_exception=False)

		self.assertEqual(self.resolved_list(self.d1), self.list_b)
		self.assertEqual(self.resolved_list(self.x), self.list_b)
		self.assertEqual(self.resolved_list(self.d2), self.list_b)
		self.assertEqual(self.resolved_list(self.d2_plus_1), self.list_a)


if __name__ == "__main__":
	unittest.main()
