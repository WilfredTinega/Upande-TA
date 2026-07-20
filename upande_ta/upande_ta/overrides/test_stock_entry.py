import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from upande_ta.upande_ta.overrides.stock_entry import material_request_employee_query


def _make_material_request_with_employees(employee_status_pairs):
	"""employee_status_pairs: list of (employee, issued_via_stock_entry_or_None)."""
	farm = frappe.get_all("Farm", limit=1, pluck="name")
	business_unit = frappe.get_all("Business Unit", limit=1, pluck="name")
	mr = frappe.get_doc(
		{
			"doctype": "Material Request",
			"material_request_type": "Material Issue",
			"transaction_date": today(),
			"company": "_Test Company",
			"custom_farm": farm[0] if farm else None,
			"custom_business_unit": business_unit[0] if business_unit else None,
			"items": [
				{
					"item_code": "_Test Item",
					"qty": 1,
					"uom": "_Test UOM",
					"stock_uom": "_Test UOM",
					"conversion_factor": 1,
					"schedule_date": today(),
					"warehouse": "_Test Warehouse - _TC",
				}
			],
		}
	)
	for employee, issued_via in employee_status_pairs:
		mr.append("custom_employee_data", {"employee": employee, "issued_via_stock_entry": issued_via})
	# ignore_links=True: issued_via_stock_entry ("STE-0001" in tests) is a Link
	# to Stock Entry that intentionally doesn't exist as a real record here --
	# only the query function's own emptiness check on the value matters, not
	# whether it resolves to a real document.
	mr.insert(ignore_permissions=True, ignore_links=True)
	return mr


class IntegrationTestMaterialRequestEmployeeQuery(IntegrationTestCase):
	def setUp(self):
		if not frappe.get_all("Farm", limit=1) or not frappe.get_all("Business Unit", limit=1):
			self.skipTest("No Farm/Business Unit record on this site to build a valid test Material Request.")
		# Employee has no bundled test fixture guaranteeing a specific record
		# exists, so look up real ones rather than hardcoding a name like
		# "HR-EMP-00001" (mirrors upande_stores' get_test_employees -- not
		# imported from there, since cross-app Python imports aren't allowed).
		self.employees = frappe.get_all("Employee", filters={"status": "Active"}, limit=2, pluck="name")
		if len(self.employees) < 2:
			self.skipTest("Need at least 2 Active Employee records on this site.")

	def test_excludes_already_issued_employees(self):
		emp1, emp2 = self.employees
		mr = _make_material_request_with_employees(
			[(emp1, None), (emp2, "STE-0001")]
		)
		results = material_request_employee_query(
			"Employee", "", "name", 0, 20, {"material_request": mr.name}
		)
		names = [r[0] for r in results]
		self.assertIn(emp1, names)
		self.assertNotIn(emp2, names)

	def test_falls_back_to_unrestricted_search_without_material_request(self):
		results = material_request_employee_query("Employee", "", "name", 0, 20, {"material_request": ""})
		self.assertIsInstance(results, list)
		self.assertGreater(len(results), 0)

	def test_honors_pagination_offset(self):
		employees = frappe.get_all("Employee", filters={"status": "Active"}, limit=3, pluck="name")
		if len(employees) < 3:
			self.skipTest("Need at least 3 Active Employee records on this site.")
		mr = _make_material_request_with_employees([(e, None) for e in employees])
		page1 = material_request_employee_query("Employee", "", "name", 0, 2, {"material_request": mr.name})
		page2 = material_request_employee_query("Employee", "", "name", 2, 2, {"material_request": mr.name})
		self.assertEqual(len(page1), 2)
		self.assertGreaterEqual(len(page2), 1)
		self.assertEqual(set(r[0] for r in page1) & set(r[0] for r in page2), set())
