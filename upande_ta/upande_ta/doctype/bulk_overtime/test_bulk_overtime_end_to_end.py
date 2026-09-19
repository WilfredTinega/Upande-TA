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
	"Company",
	"Department",
	"Employee",
	"Farm",
	"Holiday List",
	"Overtime Type",
	"Salary Component",
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
		return (
			frappe.get_doc(
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
			.insert(ignore_permissions=True)
			.name
		)

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
						{"salary_component": "Basic", "amount": 50000, "amount_based_on_formula": 0}
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

		return {getdate(row.overtime_date): row for row in doc.bulk_overtime_entries}

	# ──────────────────────────────────────────────────────────────────────
	# Tests
	# ──────────────────────────────────────────────────────────────────────

	def test_only_approved_requests_are_picked_up(self):
		self.make_request({self.working_day: 2}, submit=False)
		doc = self.make_bulk_overtime()
		self.assertEqual(len(doc.bulk_overtime_entries), 0, "a draft request must not be paid")

		self.make_request({self.working_day: 2})
		doc = self.make_bulk_overtime()
		self.assertEqual(len(doc.bulk_overtime_entries), 1)

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

		row = doc.bulk_overtime_entries[0]
		row.manual_override = 1
		row.approved_hours = 2
		row.override_reason = "Scanner missed the clock-out"
		doc.get_overtime()

		row = doc.bulk_overtime_entries[0]
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

		# The one type pays 1.5x on the working day and, through its own weekend
		# multiplier, 2x on the weekly off: 2h x 1.5 x 100 + 4h x 2 x 100 = 1100.
		amounts = frappe.get_all(
			"Additional Salary",
			filters={"ref_doctype": "Overtime Slip", "ref_docname": slips[0].name, "docstatus": 1},
			fields=["salary_component", "amount"],
		)
		self.assertEqual(len(amounts), 1)
		self.assertEqual(amounts[0].salary_component, COMPONENT)
		self.assertEqual(amounts[0].amount, 1100)

	def test_a_day_is_not_paid_twice(self):
		self.make_request({self.working_day: 2})
		first = self.make_bulk_overtime()
		first.insert(ignore_permissions=True)

		second = self.make_bulk_overtime()
		self.assertEqual(len(second.bulk_overtime_entries), 0)

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


if __name__ == "__main__":
	unittest.main()
