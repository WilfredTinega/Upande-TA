# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""End-to-end tests for Holiday Assignment Tool, against the real HRMS resolver.

``test_holiday_assignment_tool.py`` next door tests ``plan_segments`` in
isolation. This file asks ``hrms.utils.holiday_list.get_assigned_holiday_list``
— the function every attendance, leave and payroll path in HRMS goes through —
which list is in force on each date after a run:

    employee is on list A
      run the tool: list B, D1 .. D2

    as_on D1          -> B
    as_on D2          -> B
    as_on D2 + 1      -> A     the restore

    run it again over the same window with list C, and C replaces B: the
    earlier records are cancelled, D2 + 1 is still A.

These need a database, so the whole class is skipped when this process is not
attached to a site (see :func:`_site_connected`).

Naming note: this module is *not* ``test_holiday_assignment_tool``, so
``bench run-tests --doctype "Holiday Assignment Tool"`` will not pick it up;
``--app upande_ta`` and ``--module`` will.
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
#: A second list, for the re-run that replaces the first.
LIST_C = f"{PREFIX} Replacement List"


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
		cls.prior_assignment_date = add_days(today, -10)
		cls.prior_assignment = cls._assign_holiday_list(
			cls.employee, cls.list_a, cls.prior_assignment_date
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
		employee = frappe.get_doc(
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
		cls._fill_site_mandatory_data(employee)
		# ignore_mandatory/ignore_links: other apps make their own fields
		# mandatory on Employee — Kaitet adds Unit/Division, Business Unit and
		# Employee Category, the last of them pointing at a doctype that is not
		# even installed here. None of them mean anything to these tests, and
		# without this the whole suite errors out in setUpClass on any site that
		# installs those apps.
		return employee.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True).name

	@classmethod
	def _fill_site_mandatory_data(cls, doc) -> None:
		"""Fill mandatory Data/number fields another app added to the doctype.

		These are filled rather than ignored because a site may name its records
		after one of them (Kaitet names Employee after its Employee Number), and
		a blank would collide on the second insert.
		"""
		for field in doc.meta.fields:
			if not field.reqd or doc.get(field.fieldname):
				continue
			if field.fieldtype in ("Data", "Small Text", "Text"):
				doc.set(field.fieldname, f"{PREFIX}-{frappe.generate_hash(length=8)}")
			elif field.fieldtype in ("Int", "Float", "Currency"):
				doc.set(field.fieldname, 1)

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

	def make_document(self, holiday_list=None, from_date=None, to_date=None):
		"""Load the Single, fill it in for the one employee, and run it.

		One employee is below BATCH_THRESHOLD, so ``assign_holidays`` runs inline,
		in this transaction — no worker to wait on.
		"""
		doc = frappe.get_single("Holiday Assignment Tool")
		doc.company = self.company
		doc.holiday_list = holiday_list or self.list_b
		doc.from_date = from_date or self.d1
		doc.to_date = to_date or self.d2

		doc.set("employees", [])
		doc.append("employees", {"employee": self.employee, "prior_holiday_list": self.list_a})

		doc.assign_holidays()
		return doc

	def active_assignments(self):
		return frappe.get_all(
			"Holiday List Assignment",
			filters={"assigned_to": self.employee, "docstatus": 1},
			fields=["from_date", "holiday_list"],
			order_by="from_date asc",
		)

	# ──────────────────────────────────────────────────────────────────────
	# Tests
	# ──────────────────────────────────────────────────────────────────────

	def test_prior_list_is_in_force_before_the_run(self):
		for as_on in (self.d1, self.x, self.d2_plus_1):
			self.assertEqual(self.resolved_list(as_on), self.list_a)

	def test_assign_applies_list_and_restores(self):
		self.make_document()

		self.assertEqual(self.resolved_list(self.d1), self.list_b, "period start must use the new list")
		self.assertEqual(self.resolved_list(self.x), self.list_b)
		self.assertEqual(self.resolved_list(self.d2), self.list_b, "the last day is still the new list")
		self.assertEqual(
			self.resolved_list(self.d2_plus_1), self.list_a, "the day after must restore the prior list"
		)

	def test_employees_table_is_cleared_after_the_run(self):
		"""The work list is emptied so the next run starts blank."""
		self.make_document()

		self.assertFalse(frappe.get_single("Holiday Assignment Tool").employees)
		self.assertFalse(
			frappe.db.count("Holiday Assignment Tool Employee", {"parent": "Holiday Assignment Tool"})
		)

	def test_company_and_window_are_cleared_after_the_run(self):
		"""The tool is a Single: a company and a window left behind would be the
		next person's defaults, and they belong to the run that just ended. The
		Holiday List is kept on purpose."""
		self.make_document()

		reloaded = frappe.get_single("Holiday Assignment Tool")
		self.assertIsNone(reloaded.company)
		self.assertIsNone(reloaded.from_date)
		self.assertIsNone(reloaded.to_date)
		self.assertTrue(reloaded.holiday_list)

	def test_rerun_replaces_the_earlier_run(self):
		"""Running again over the same window swaps the list — no undo needed.
		The earlier records are cancelled, not deleted, and the restore after the
		window is not duplicated."""
		self.make_document()
		self.make_document(holiday_list=self.list_c)

		for as_on in (self.d1, self.x, self.d2):
			self.assertEqual(self.resolved_list(as_on), self.list_c)
		self.assertEqual(self.resolved_list(self.d2_plus_1), self.list_a)

		self.assertEqual(
			[(row.from_date, row.holiday_list) for row in self.active_assignments()],
			[
				(frappe.utils.getdate(self.prior_assignment_date), self.list_a),
				(self.d1, self.list_c),
				(self.d2_plus_1, self.list_a),
			],
		)
		self.assertEqual(
			frappe.db.count(
				"Holiday List Assignment",
				{"assigned_to": self.employee, "docstatus": 2, "holiday_list": self.list_b},
			),
			1,
		)

	def test_single_day_inside_an_earlier_run(self):
		"""A one-day run inside an earlier, longer one: that day switches, and
		the employee goes back to the earlier run's list the day after."""
		self.make_document()
		self.make_document(holiday_list=self.list_c, from_date=self.x, to_date=self.x)

		self.assertEqual(self.resolved_list(self.d1), self.list_b)
		self.assertEqual(self.resolved_list(self.x), self.list_c)
		self.assertEqual(self.resolved_list(frappe.utils.add_days(self.x, 1)), self.list_b)
		self.assertEqual(self.resolved_list(self.d2_plus_1), self.list_a)


if __name__ == "__main__":
	unittest.main()
