# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Employee Exchange without the network: what is queued, received, linked
and refused. Calling another instance is covered by hand between two sites."""

import unittest

import frappe
from frappe.utils import add_to_date, now_datetime

from upande_ta.upande_ta import exchange

try:
	from frappe.tests import IntegrationTestCase as _TestCase
except ImportError:  # Frappe v15
	from frappe.tests.utils import FrappeTestCase as _TestCase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Department",
	"Designation",
	"Employee",
	"Exchange Checkin",
	"Exchange Employee",
	"Farm",
	"Gender",
	"User",
]

ME = "TESTHQ"
OTHER = "TESTKT"
PAYROLL = "EXT-90001"


class IntegrationTestExchange(_TestCase):
	SAVEPOINT = "exchange_test"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		companies = frappe.get_all("Company", limit=2, pluck="name", order_by="name asc")
		if not companies:
			raise unittest.SkipTest("no Company on this site")
		cls.company = companies[0]
		cls.other_company = companies[-1]

	def setUp(self):
		super().setUp()
		frappe.db.savepoint(self.SAVEPOINT)
		self.addCleanup(self._rollback)
		setting = frappe.get_single("Exchange Setting")
		setting.enabled = 1
		setting.instance_code = ME
		setting.set("instances", [])
		setting.append(
			"instances",
			{
				"instance_code": OTHER,
				"base_url": "http://other.invalid",
				"enabled": 1,
				"company": "Their Farm Ltd.",
				"send_employees": 1,
				"send_checkins": 1,
				"checkins_as_employee_checkin": 1,
			},
		)
		setting.set("company_map", [])
		setting.append(
			"company_map",
			{"instance": OTHER, "home_company": "Their Farm Ltd.", "company": self.other_company},
		)
		setting.save(ignore_permissions=True)
		frappe.clear_document_cache("Exchange Setting", "Exchange Setting")

	def _rollback(self):
		try:
			frappe.db.rollback(save_point=self.SAVEPOINT)
		finally:
			frappe.clear_document_cache("Exchange Setting", "Exchange Setting")

	def make_employee(self, pin="EXT-PIN-1", **extra):
		doc = frappe.get_doc(
			{
				"doctype": "Exchange Employee",
				"home_instance": OTHER,
				"payroll_number": PAYROLL,
				"full_name": "Test Visitor",
				"link_status": "Linked",  # as if fetched: no call to the other instance
				"device_pin": pin,
				"host_company": self.company,
				**extra,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def logs(self, doc):
		return frappe.get_all(
			"Exchange Sync Log",
			filters={"reference_doctype": doc.doctype, "reference_name": doc.name},
			pluck="remote_doctype",
		)

	# ──────────────────────────────────────────────────────────────────

	def test_a_new_employee_is_stamped_and_queued(self):
		doc = self.make_employee()
		self.assertEqual((doc.source_instance, doc.origin_id), (ME, f"{ME}:{doc.name}"))
		self.assertEqual(self.logs(doc), ["Exchange Employee"])

	def test_one_record_per_home_payroll_number(self):
		self.make_employee()
		with self.assertRaises(frappe.ValidationError):
			self.make_employee(pin="EXT-PIN-2")

	def test_a_home_instance_must_be_listed(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_employee(home_instance="NOWHERE")

	def test_the_company_map_places_them_or_falls_back(self):
		self.assertEqual(exchange.company_here(OTHER, "Their Farm Ltd.").company, self.other_company)
		host = exchange.company_here(OTHER, "Unmapped Ltd.")
		self.assertEqual((host.company, host.mapped), (None, False))

	def test_a_lookup_asks_only_for_that_instances_company(self):
		self.assertEqual(
			exchange._lookup_params(OTHER, PAYROLL), {"payroll_number": PAYROLL, "company": "Their Farm Ltd."}
		)

	def test_a_device_punch_becomes_an_exchange_checkin_once(self):
		employee = self.make_employee()
		when = now_datetime()
		name = exchange.record_device_punch(employee.device_pin, when, device_sn="DEV-1", log_type="in")
		self.assertTrue(name)
		checkin = frappe.get_doc("Exchange Checkin", name)
		self.assertEqual(
			(checkin.exchange_employee, checkin.log_type, checkin.arrived_via, checkin.company),
			(employee.name, "IN", "Device", self.company),
		)
		self.assertEqual(exchange.record_device_punch(employee.device_pin, when, log_type="IN"), name)
		# their home is the other instance, so it also goes there as an Employee Checkin
		self.assertEqual(sorted(self.logs(checkin)), ["Employee Checkin", "Exchange Checkin"])

	def test_a_pin_that_is_nobodys_exchange_pin_is_left_alone(self):
		self.assertIsNone(exchange.record_device_punch("NO-SUCH-PIN", now_datetime()))

	def test_a_received_employee_is_created_then_updated(self):
		data = {
			"origin_id": f"{OTHER}:EXE-0042",
			"source_instance": OTHER,
			"home_instance": OTHER,
			"payroll_number": "EXT-90042",
			"full_name": "Sent Visitor",
			"home_company": "Their Farm Ltd.",
			"host_company": "Their Farm Ltd.",
			"device_pin": "EXT-PIN-42",
			"status": "Active",
			"templates": [{"source_device_sn": "DEV-9", "fp_valid": 1, "fingerprint_template": "TMP"}],
		}
		first = exchange.receive("Exchange Employee", data)
		self.assertEqual(first["status"], "created")
		doc = frappe.get_doc("Exchange Employee", first["name"])
		self.assertEqual(
			(doc.host_company, doc.device_pin, len(doc.templates)), (self.other_company, "EXT-PIN-42", 1)
		)
		self.assertEqual(self.logs(doc), [], "a received record is never sent on")

		data["full_name"] = "Renamed Visitor"
		second = exchange.receive("Exchange Employee", data)
		self.assertEqual((second["status"], second["name"]), ("updated", first["name"]))
		self.assertEqual(
			frappe.db.get_value("Exchange Employee", first["name"], "full_name"), "Renamed Visitor"
		)

	def test_our_own_record_coming_back_is_ignored(self):
		result = exchange.receive("Exchange Checkin", {"origin_id": f"{ME}:EXC-1", "source_instance": ME})
		self.assertEqual(result["status"], "ignored")

	def test_a_punch_that_arrives_before_the_person_is_linked_later(self):
		checkin = exchange.receive(
			"Exchange Checkin",
			{
				"origin_id": f"{OTHER}:EXC-0007",
				"employee_origin_id": f"{OTHER}:EXE-0007",
				"source_instance": OTHER,
				"employee_name": "Early Punch",
				"time": str(add_to_date(now_datetime(), hours=-1)),
				"log_type": "IN",
			},
		)
		self.assertIsNone(frappe.db.get_value("Exchange Checkin", checkin["name"], "exchange_employee"))
		again = exchange.receive(
			"Exchange Checkin", {"origin_id": f"{OTHER}:EXC-0007", "source_instance": OTHER}
		)
		self.assertEqual(again["status"], "exists")

		employee = exchange.receive(
			"Exchange Employee",
			{
				"origin_id": f"{OTHER}:EXE-0007",
				"source_instance": OTHER,
				"home_instance": OTHER,
				"payroll_number": "EXT-90007",
				"full_name": "Early Punch",
				"status": "Active",
			},
		)
		self.assertEqual(
			frappe.db.get_value("Exchange Checkin", checkin["name"], "exchange_employee"), employee["name"]
		)

	def test_this_instance_cannot_be_one_of_the_others(self):
		setting = frappe.get_single("Exchange Setting")
		setting.instances[0].instance_code = ME
		with self.assertRaises(frappe.ValidationError):
			setting.save(ignore_permissions=True)
