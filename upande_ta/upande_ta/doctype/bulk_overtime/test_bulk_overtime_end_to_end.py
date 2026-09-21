# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""End-to-end tests for the overtime chain::

    Overtime Request (approved) -> Bulk Overtime -> Overtime Slip -> Additional Salary

``test_bulk_overtime.py`` next door proves the arithmetic in isolation and needs
no site. This file proves the wiring: that an approved request is found, checked
against the right attendance record, classified by the holiday list in force on
that date, paid the lower of requested and worked, and turned into a submitted
Overtime Slip that creates the Additional Salary payroll reads.

Needs a database, so the class is skipped when this process is not attached to a
site. Everything it creates is rolled back by IntegrationTestCase.
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
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Additional Salary",
	"Attendance",
	"Bulk Overtime",
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

PREFIX = "_Test BOT"
SHIFT = f"{PREFIX} Shift"
HOLIDAY_LIST = f"{PREFIX} Holidays"
#: One type carries every rate: 1.5x normally, 2x on a weekly off or a public
#: holiday. That is what the Overtime Slip pays by.
OT_TYPE = f"{PREFIX} Overtime Type"
COMPONENT = f"{PREFIX} Overtime"

#: Shift is 08:00-17:00, so nine hours cover the day and anything beyond is
#: overtime on a working day.
SHIFT_HOURS = 9.0


def _site_connected() -> bool:
	if frappe is None:
		return False
	try:
		return bool(getattr(frappe.local, "site", None)) and frappe.db is not None
	except Exception:
		return False


class IntegrationTestBulkOvertimeEndToEnd(_TestCase):
	# ──────────────────────────────────────────────────────────────────────
	# Fixtures
	# ──────────────────────────────────────────────────────────────────────

	@classmethod
	def setUpClass(cls):
		if not _site_connected():
			raise unittest.SkipTest("Bulk Overtime end-to-end tests need a site; none is configured.")

		super().setUpClass()

		from frappe.utils import add_days, getdate, nowdate

		companies = frappe.get_all("Company", limit=1, pluck="name")
		if not companies:
			raise unittest.SkipTest("no Company on this site")
		cls.company = companies[0]

		genders = frappe.get_all("Gender", limit=1, pluck="name")
		if not genders:
			raise unittest.SkipTest("no Gender records on this site")

		today = getdate(nowdate())
		# A closed period in the past: overtime is paid once it has been worked.
		cls.to_date = add_days(today, -1)
		cls.from_date = add_days(cls.to_date, -6)
		cls.working_day = cls.from_date  # attendance: 11.5h on a 9h shift
		cls.rest_day = add_days(cls.from_date, 1)  # weekly off, 4h worked
		cls.public_holiday = add_days(cls.from_date, 2)  # holiday, 5h worked
		cls.short_day = add_days(cls.from_date, 3)  # 10h: less OT than requested
		cls.no_punch_day = add_days(cls.from_date, 4)  # Present, no working hours

		cls._quieten_workflow_emails("Overtime Request", "Bulk Overtime")

		cls._make_shift()
		cls._make_holiday_list()
		cls._make_overtime_type()
		cls.employee = cls._make_employee(add_days(today, -400), genders[0])
		cls._assign_holiday_list(cls.employee, HOLIDAY_LIST, add_days(today, -365))
		# Additional Salary refuses an employee with no salary structure, so the
		# pay-out half of the chain needs one even on a Fixed Hourly Rate type.
		cls._assign_salary_structure(add_days(today, -365))

		cls._mark_attendance(cls.working_day, 11.5)
		cls._mark_attendance(cls.rest_day, 4)
		cls._mark_attendance(cls.public_holiday, 5)
		cls._mark_attendance(cls.short_day, 10)
		cls._mark_attendance(cls.no_punch_day, 0)


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
	def _make_shift(cls):
		if frappe.db.exists("Shift Type", SHIFT):
			return
		frappe.get_doc(
			{"doctype": "Shift Type", "name": SHIFT, "start_time": "08:00:00", "end_time": "17:00:00"}
		).insert(ignore_permissions=True)

	@classmethod
	def _make_holiday_list(cls):
		from frappe.utils import add_days, getdate, nowdate

		if frappe.db.exists("Holiday List", HOLIDAY_LIST):
			frappe.delete_doc("Holiday List", HOLIDAY_LIST, force=True, ignore_permissions=True)

		today = getdate(nowdate())
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": HOLIDAY_LIST,
				"from_date": add_days(today, -365),
				"to_date": add_days(today, 365),
				"holidays": [
					{"holiday_date": cls.rest_day, "description": "Weekly Off", "weekly_off": 1},
					{"holiday_date": cls.public_holiday, "description": "Public Holiday", "weekly_off": 0},
				],
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _make_overtime_type(cls):
		if not frappe.db.exists("Salary Component", COMPONENT):
			frappe.get_doc(
				{
					"doctype": "Salary Component",
					"salary_component": COMPONENT,
					"type": "Earning",
					"salary_component_abbr": "TOT",
				}
			).insert(ignore_permissions=True)

		# Fixed Hourly Rate keeps the test independent of Salary Structures:
		# the amount is rate x multiplier x hours, nothing else.
		if frappe.db.exists("Overtime Type", OT_TYPE):
			frappe.db.set_value("Overtime Type", OT_TYPE, "maximum_overtime_hours_allowed", 0)
			return
		frappe.get_doc(
			{
				"doctype": "Overtime Type",
				"name": OT_TYPE,
				"overtime_salary_component": COMPONENT,
				"standard_multiplier": 1.5,
				"applicable_for_weekend": 1,
				"weekend_multiplier": 2.0,
				"applicable_for_public_holiday": 1,
				"public_holiday_multiplier": 2.0,
				"overtime_calculation_method": "Fixed Hourly Rate",
				"hourly_rate": 100,
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _make_employee(cls, date_of_joining, gender) -> str:
		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": f"{PREFIX} Employee",
				"company": cls.company,
				"gender": gender,
				"status": "Active",
				"date_of_birth": "1990-01-01",
				"date_of_joining": date_of_joining,
				"default_shift": SHIFT,
			}
		)
		cls._fill_mandatory_fields(employee)
		# upande_payroll prices overtime off the employee's Basic Pay, and
		# refuses rather than paying nothing for hours actually worked
		if employee.meta.has_field("basic_pay"):
			employee.basic_pay = 50000
		return employee.insert(ignore_permissions=True).name

	@classmethod
	def _fill_mandatory_fields(cls, doc):
		"""Sites make their own Employee fields mandatory — an Employee Number,
		a Unit/Division, an Employee Category — through Custom Fields and
		Property Setters alike, so the meta is what to read. None of them are
		what this file tests, so anything still empty gets a plausible value
		rather than one hard-coded for a single site."""
		for df in doc.meta.fields:
			if not df.reqd or doc.get(df.fieldname):
				continue
			if df.fieldtype == "Link":
				value = frappe.db.get_value(df.options, {}, "name")
				if not value:
					raise unittest.SkipTest(f"no {df.options} to satisfy {doc.doctype}.{df.fieldname}")
				doc.set(df.fieldname, value)
			elif df.fieldtype == "Select":
				doc.set(df.fieldname, (df.options or "").split("\n")[0])
			elif df.fieldtype in ("Int", "Float", "Currency"):
				doc.set(df.fieldname, 1)
			elif df.fieldtype in ("Date", "Datetime"):
				doc.set(df.fieldname, frappe.utils.nowdate())
			else:
				doc.set(df.fieldname, frappe.generate_hash(length=10))

	@classmethod
	def _basic_component(cls) -> str:
		"""ERPNext ships a "Basic" earning, but not every site keeps that name."""
		basic = f"{PREFIX} Basic"
		if frappe.db.exists("Salary Component", "Basic"):
			return "Basic"
		if not frappe.db.exists("Salary Component", basic):
			frappe.get_doc(
				{
					"doctype": "Salary Component",
					"salary_component": basic,
					"type": "Earning",
					"salary_component_abbr": "TBAS",
				}
			).insert(ignore_permissions=True)
		return basic

	@classmethod
	def _assign_salary_structure(cls, from_date):
		structure_name = f"{PREFIX} Structure"
		currency = frappe.db.get_value("Company", cls.company, "default_currency")

		if not frappe.db.exists("Salary Structure", structure_name):
			structure = frappe.get_doc(
				{
					"doctype": "Salary Structure",
					"name": structure_name,
					"company": cls.company,
					"payroll_frequency": "Monthly",
					"currency": currency,
					"earnings": [
						{"salary_component": cls._basic_component(), "amount": 50000, "amount_based_on_formula": 0}
					],
				}
			)
			structure.insert(ignore_permissions=True)
			structure.submit()

		assignment = frappe.get_doc(
			{
				"doctype": "Salary Structure Assignment",
				"employee": cls.employee,
				"salary_structure": structure_name,
				"company": cls.company,
				"currency": currency,
				"from_date": from_date,
				"base": 50000,
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()

	@classmethod
	def _assign_holiday_list(cls, employee, holiday_list, from_date):
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

	@classmethod
	def _mark_attendance(cls, date, working_hours):
		attendance = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": cls.employee,
				"attendance_date": date,
				"status": "Present",
				"company": cls.company,
				"shift": SHIFT,
				"working_hours": working_hours,
			}
		)
		attendance.insert(ignore_permissions=True)
		attendance.submit()

	#: IntegrationTestCase rolls back once at class cleanup, not between tests.
	SAVEPOINT = "bulk_overtime_end_to_end"

	def setUp(self):
		super().setUp()
		frappe.db.savepoint(self.SAVEPOINT)
		self.addCleanup(self._rollback_to_savepoint)

	def _rollback_to_savepoint(self):
		try:
			frappe.db.rollback(save_point=self.SAVEPOINT)
		except Exception:
			pass

	# ──────────────────────────────────────────────────────────────────────
	# Helpers
	# ──────────────────────────────────────────────────────────────────────

	def make_request(self, dates_and_hours: dict, submit: bool = True):
		"""One Overtime Request per date, as the desk creates them."""
		names = []
		for date, hours in dates_and_hours.items():
			request = frappe.get_doc(
				{
					"doctype": "Overtime Request",
					"company": self.company,
					"overtime_date": date,
					"reason": "Peak season",
					"overtime_type": OT_TYPE,
					"default_requested_hours": hours,
					"employees": [{"employee": self.employee, "requested_hours": hours}],
				}
			)
			request.insert(ignore_permissions=True)
			if submit:
				request.submit()
			names.append(request.name)
		return names

	def make_range_request(self, from_date, to_date, hours, submit: bool = True):
		"""One request covering a range, the way the Week and Month pickers save
		it. Its hours are *per day*: the range says which days are covered, not
		how the hours are shared out over them."""
		request = frappe.get_doc(
			{
				"doctype": "Overtime Request",
				"company": self.company,
				"request_for": "Date Range",
				"overtime_date": from_date,
				"to_date": to_date,
				"reason": "Peak season",
				"overtime_type": OT_TYPE,
				"default_requested_hours": hours,
				"employees": [{"employee": self.employee, "requested_hours": hours}],
			}
		)
		request.insert(ignore_permissions=True)
		if submit:
			request.submit()
		return request

	def make_bulk_overtime_for(self, requests):
		"""A batch that pays only the requests named, the way the picker does."""
		doc = frappe.get_doc(
			{
				"doctype": "Bulk Overtime",
				"company": self.company,
				"from_date": self.from_date,
				"to_date": self.to_date,
			}
		)
		doc.get_overtime(overtime_requests=requests)
		return doc

	def make_bulk_overtime(self):
		doc = frappe.get_doc(
			{
				"doctype": "Bulk Overtime",
				"company": self.company,
				"from_date": self.from_date,
				"to_date": self.to_date,
			}
		)
		doc.get_overtime()
		return doc

	def rows_by_date(self, doc):
		from frappe.utils import getdate

		return {getdate(row.overtime_date): row for row in self.own_rows(doc)}

	def own_rows(self, doc) -> list:
		"""Only this test's employee. These run against a working site, which
		has approved requests of its own that Bulk Overtime rightly picks up."""
		return [row for row in doc.bulk_overtime_entries if row.employee == self.employee]

	# ──────────────────────────────────────────────────────────────────────
	# Tests
	# ──────────────────────────────────────────────────────────────────────

	def test_only_approved_requests_are_picked_up(self):
		self.make_request({self.working_day: 2}, submit=False)
		doc = self.make_bulk_overtime()
		self.assertEqual(len(self.own_rows(doc)), 0, "a draft request must not be paid")

		self.make_request({self.working_day: 2})
		doc = self.make_bulk_overtime()
		self.assertEqual(len(self.own_rows(doc)), 1)

	def test_hours_and_day_types_across_the_period(self):
		self.make_request(
			{
				self.working_day: 2,  # worked 2.5 over the shift -> capped at 2
				self.rest_day: 6,  # worked 4 on a weekly off -> all 4 paid
				self.public_holiday: 5,  # worked 5 on a holiday -> all 5 paid
				self.short_day: 3,  # worked 1 over the shift -> 1 paid
				self.no_punch_day: 2,  # Present, no hours -> nothing
			}
		)
		rows = self.rows_by_date(self.make_bulk_overtime())

		self.assertEqual(
			(rows[self.working_day].day_type, rows[self.working_day].biometric_hours, rows[self.working_day].approved_hours, rows[self.working_day].status),
			("Working Day", 2.5, 2, "Capped at Request"),
		)
		self.assertEqual(
			(rows[self.rest_day].day_type, rows[self.rest_day].approved_hours, rows[self.rest_day].status),
			("Rest Day", 4, "Worked Less"),
		)
		self.assertEqual(
			(rows[self.public_holiday].day_type, rows[self.public_holiday].approved_hours, rows[self.public_holiday].status),
			("Public Holiday", 5, "Matched"),
		)
		self.assertEqual(rows[self.short_day].approved_hours, 1)
		self.assertEqual(
			(rows[self.no_punch_day].approved_hours, rows[self.no_punch_day].status), (0, "No Clock-Out")
		)

	def test_overtime_type_comes_from_the_request(self):
		self.make_request({self.working_day: 2, self.rest_day: 4})
		rows = self.rows_by_date(self.make_bulk_overtime())

		self.assertEqual(rows[self.working_day].overtime_type, OT_TYPE)
		self.assertEqual(rows[self.rest_day].overtime_type, OT_TYPE)

	def test_daily_cap_comes_from_the_overtime_type(self):
		self.make_request({self.working_day: 3})  # 2.5 hours of overtime worked

		frappe.db.set_value("Overtime Type", OT_TYPE, "maximum_overtime_hours_allowed", 1)
		rows = self.rows_by_date(self.make_bulk_overtime())
		self.assertEqual(rows[self.working_day].biometric_hours, 1, "the type's daily cap applies")

		frappe.db.set_value("Overtime Type", OT_TYPE, "maximum_overtime_hours_allowed", 0)
		rows = self.rows_by_date(self.make_bulk_overtime())
		self.assertEqual(rows[self.working_day].biometric_hours, 2.5)

	def test_manual_override_survives_a_refetch_and_is_capped_at_the_request(self):
		self.make_request({self.no_punch_day: 2})
		doc = self.make_bulk_overtime()

		row = self.own_rows(doc)[0]
		row.manual_override = 1
		row.approved_hours = 2
		row.override_reason = "Scanner missed the clock-out"
		doc.get_overtime()

		row = self.own_rows(doc)[0]
		self.assertEqual((row.manual_override, row.approved_hours), (1, 2))

		row.approved_hours = 5
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_submit_creates_a_slip_and_an_additional_salary(self):
		self.make_request({self.working_day: 2, self.rest_day: 4})
		doc = self.make_bulk_overtime()
		doc.insert(ignore_permissions=True)
		doc.submit()

		slips = frappe.get_all(
			"Overtime Slip",
			filters={"custom_bulk_overtime": doc.name, "docstatus": 1},
			fields=["name", "employee", "total_overtime_duration"],
		)
		self.assertEqual(len(slips), 1, "one slip per employee")
		self.assertEqual(slips[0].employee, self.employee)
		self.assertEqual(slips[0].total_overtime_duration, 6, "2 working-day hours + 4 rest-day hours")

		details = frappe.get_all(
			"Overtime Details",
			filters={"parent": slips[0].name},
			fields=["date", "overtime_type", "overtime_duration", "reference_document"],
			order_by="date asc",
		)
		self.assertEqual([d.overtime_type for d in details], [OT_TYPE, OT_TYPE])
		self.assertTrue(all(d.reference_document for d in details), "each line links its attendance")

		amounts = frappe.get_all(
			"Additional Salary",
			filters={"ref_doctype": "Overtime Slip", "ref_docname": slips[0].name, "docstatus": 1},
			fields=["salary_component", "amount"],
		)
		# This chain carries hours, not money: the Additional Salary must exist
		# and name the right component, but what it is worth is priced by the
		# payroll formula that owns that rule, and is not asserted here.
		self.assertEqual(len(amounts), 1)
		self.assertEqual(amounts[0].salary_component, COMPONENT)

	def test_a_day_is_not_paid_twice(self):
		self.make_request({self.working_day: 2})
		first = self.make_bulk_overtime()
		first.insert(ignore_permissions=True)

		second = self.make_bulk_overtime()
		self.assertEqual(len(self.own_rows(second)), 0)

	def test_cancel_unwinds_the_slip_and_the_additional_salary(self):
		self.make_request({self.working_day: 2})
		doc = self.make_bulk_overtime()
		doc.insert(ignore_permissions=True)
		doc.submit()
		doc.cancel()

		self.assertFalse(
			frappe.get_all("Overtime Slip", filters={"custom_bulk_overtime": doc.name, "docstatus": 1})
		)
		self.assertFalse(
			frappe.get_all(
				"Additional Salary",
				filters={"ref_doctype": "Overtime Slip", "docstatus": 1, "employee": self.employee},
			)
		)

	# ──────────────────────────────────────────────────────────────────────
	# A request that covers a range, expanded one day at a time
	# ──────────────────────────────────────────────────────────────────────

	def test_a_range_becomes_one_row_per_attended_day(self):
		"""The five days with attendance each get a row of their own; the two
		with none are dropped, because a range is a standing permission rather
		than a promise about each day."""
		self.make_range_request(self.from_date, self.to_date, 2)
		doc = self.make_bulk_overtime()

		self.assertEqual(
			sorted(self.rows_by_date(doc)),
			sorted(
				[self.working_day, self.rest_day, self.public_holiday, self.short_day, self.no_punch_day]
			),
		)

	def test_a_range_requests_its_hours_every_day(self):
		"""Requested hours are per day, so each expanded row carries the whole
		amount rather than a share of it — and each day is still settled
		against its own attendance."""
		self.make_range_request(self.from_date, self.to_date, 2)
		doc = self.make_bulk_overtime()

		rows = self.rows_by_date(doc)
		self.assertEqual([row.requested_hours for row in rows.values()], [2] * len(rows))
		self.assertEqual(rows[self.working_day].approved_hours, 2)  # 11.5h on a 9h shift, capped
		self.assertEqual(rows[self.short_day].status, "Worked Less")  # 10h on a 9h shift
		self.assertEqual(rows[self.no_punch_day].status, "No Clock-Out")

	def test_a_range_is_clipped_to_the_period(self):
		"""The request may run well past the batch; only the days inside it are
		paid here, and the rest wait for the batch that covers them."""
		from frappe.utils import add_days, getdate

		self.make_range_request(add_days(self.from_date, -10), add_days(self.to_date, 10), 2)
		doc = self.make_bulk_overtime()

		dates = sorted(self.rows_by_date(doc))
		self.assertGreaterEqual(dates[0], getdate(self.from_date))
		self.assertLessEqual(dates[-1], getdate(self.to_date))

	def test_a_single_day_keeps_its_row_without_attendance(self):
		"""A day asked for by name is reported either way: a missing clock-in on
		that day is exactly what HR needs to see."""
		from frappe.utils import add_days, getdate

		bare_day = add_days(self.from_date, 5)  # no attendance of any kind
		self.make_request({bare_day: 2})
		doc = self.make_bulk_overtime()

		rows = self.rows_by_date(doc)
		self.assertIn(getdate(bare_day), rows)
		self.assertEqual(rows[getdate(bare_day)].status, "No Attendance")

	def test_a_range_covering_that_same_day_drops_it(self):
		"""The other side of the rule above, so the two cannot drift apart."""
		from frappe.utils import add_days

		bare_day = add_days(self.from_date, 5)
		self.make_range_request(bare_day, add_days(bare_day, 1), 2)
		doc = self.make_bulk_overtime()

		self.assertEqual(len(self.own_rows(doc)), 0)

	# ──────────────────────────────────────────────────────────────────────
	# Get from Overtime Request: paying the requests HR picks
	# ──────────────────────────────────────────────────────────────────────

	def test_the_picker_offers_the_requests_in_the_period(self):
		names = self.make_request({self.working_day: 2, self.short_day: 3})
		doc = self.make_bulk_overtime()

		offered = {r.name: r for r in doc.get_approved_requests()}
		for name in names:
			self.assertIn(name, offered)
			self.assertEqual(offered[name].employees, 1)
			self.assertEqual(offered[name].days_in_period, 1)

	def test_the_picker_counts_only_the_days_inside_the_period(self):
		"""A request running past the batch is offered for what this batch
		would actually pay, not for its whole length."""
		from frappe.utils import add_days

		request = self.make_range_request(self.from_date, add_days(self.to_date, 10), 2)
		doc = self.make_bulk_overtime()

		offered = {r.name: r for r in doc.get_approved_requests()}[request.name]
		self.assertEqual(offered.days_in_period, 7)  # the batch is a 7-day period

	def test_only_the_picked_request_is_paid(self):
		picked, other = self.make_request({self.working_day: 2}), self.make_request({self.short_day: 3})
		doc = self.make_bulk_overtime_for(picked)

		dates = sorted(self.rows_by_date(doc))
		self.assertEqual(dates, [self.working_day], "the request that was not picked must be left alone")
		self.assertTrue(all(row.overtime_request in picked for row in self.own_rows(doc)))
		self.assertNotIn(other[0], [row.overtime_request for row in self.own_rows(doc)])

	def test_picking_every_request_matches_the_sweep(self):
		names = self.make_request({self.working_day: 2, self.short_day: 3})
		self.assertEqual(
			sorted(self.rows_by_date(self.make_bulk_overtime_for(names))),
			sorted(self.rows_by_date(self.make_bulk_overtime())),
		)

	def test_a_picked_request_is_still_clipped_to_the_period(self):
		"""Picking a request does not let it pay outside the batch's dates."""
		from frappe.utils import add_days, getdate

		request = self.make_range_request(add_days(self.from_date, -10), add_days(self.to_date, 10), 2)
		doc = self.make_bulk_overtime_for([request.name])

		dates = sorted(self.rows_by_date(doc))
		self.assertGreaterEqual(dates[0], getdate(self.from_date))
		self.assertLessEqual(dates[-1], getdate(self.to_date))

	def test_picking_a_request_outside_the_period_pays_nothing(self):
		self.make_request({self.working_day: 2})
		doc = self.make_bulk_overtime()
		result = doc.get_overtime(overtime_requests=["HR-OTR-NOT-A-REQUEST"])

		self.assertEqual(result["rows"], 0)
		self.assertEqual(result["picked"], 1)

	# ──────────────────────────────────────────────────────────────────────
	# The approval workflow
	# ──────────────────────────────────────────────────────────────────────

	def _apply(self, doc, *actions):
		from frappe.model.workflow import apply_workflow

		for action in actions:
			doc = apply_workflow(doc, action)
		return doc

	def _needs_workflow(self, doctype):
		if not frappe.db.exists("Workflow", {"document_type": doctype, "is_active": 1}):
			raise unittest.SkipTest(f"no active Workflow on {doctype}; run bench migrate")

	def test_approving_a_request_through_the_workflow_pays_it(self):
		self._needs_workflow("Overtime Request")
		name = self.make_request({self.working_day: 2}, submit=False)[0]

		request = self._apply(frappe.get_doc("Overtime Request", name), "Submit for Approval", "Approve")
		self.assertEqual((request.workflow_state, request.docstatus), ("Approved", 1))
		self.assertEqual(len(self.own_rows(self.make_bulk_overtime())), 1)

	def test_a_rejected_request_is_never_paid(self):
		"""The reason Rejected is docstatus 0 and not 1: Bulk Overtime pays
		from ``docstatus = 1``, so a rejection that submitted the document
		would be paid exactly like an approval."""
		self._needs_workflow("Overtime Request")
		name = self.make_request({self.working_day: 2}, submit=False)[0]

		request = self._apply(frappe.get_doc("Overtime Request", name), "Submit for Approval", "Reject")
		self.assertEqual(request.workflow_state, "Rejected")
		self.assertEqual(request.docstatus, 0, "a rejection must never reach docstatus 1")
		self.assertEqual(len(self.own_rows(self.make_bulk_overtime())), 0)

	def test_a_request_awaiting_approval_is_not_paid(self):
		self._needs_workflow("Overtime Request")
		name = self.make_request({self.working_day: 2}, submit=False)[0]

		request = self._apply(frappe.get_doc("Overtime Request", name), "Submit for Approval")
		self.assertEqual((request.workflow_state, request.docstatus), ("Pending Approval", 0))
		self.assertEqual(len(self.own_rows(self.make_bulk_overtime())), 0)

	def test_a_rejected_batch_cuts_no_slips(self):
		"""The same rule on the payout side: rejecting a batch must not fire
		on_submit and create the Overtime Slips it was refused."""
		self._needs_workflow("Bulk Overtime")
		self.make_request({self.working_day: 2})
		batch = self.make_bulk_overtime()
		batch.insert(ignore_permissions=True)

		batch = self._apply(batch, "Submit for Approval", "Reject")
		self.assertEqual(batch.docstatus, 0, "a rejected batch must never reach docstatus 1")
		self.assertFalse(
			frappe.get_all("Overtime Slip", filters={"custom_bulk_overtime": batch.name}),
			"a rejected batch must cut no slips",
		)

	def test_approving_a_batch_cuts_its_slips(self):
		self._needs_workflow("Bulk Overtime")
		self.make_request({self.working_day: 2})
		batch = self.make_bulk_overtime()
		batch.insert(ignore_permissions=True)

		batch = self._apply(batch, "Submit for Approval", "Approve")
		self.assertEqual((batch.workflow_state, batch.docstatus), ("Approved", 1))
		self.assertTrue(
			frappe.get_all("Overtime Slip", filters={"custom_bulk_overtime": batch.name, "docstatus": 1})
		)


if __name__ == "__main__":
	unittest.main()
