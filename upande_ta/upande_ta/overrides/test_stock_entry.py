from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from upande_ta.upande_ta.overrides.stock_entry import material_request_employee_query


def _make_material_request_with_items(item_rows, material_request_type="Material Issue"):
	"""item_rows: list of dicts appended to the standard Items table
	as-is (each needs at minimum item_code/qty; employee/
	issued_via_stock_entry are optional -- e.g. {"item_code": "_Test Item",
	"qty": 1, "employee": <name>, "issued_via_stock_entry": "STE-0001"}).
	Unset keys fall back to sensible defaults (item_code="_Test Item",
	qty=1, "_Test UOM", etc.) so callers only need to specify what's
	different.

	material_request_type defaults to "Material Issue" (this app's primary
	use case, where upande_stores' own validate hook requires employee on
	every row); pass "Material Transfer" for a test that needs a
	blank-employee row to coexist with an employee-having one.
	"""
	farm = frappe.get_all("Farm", limit=1, pluck="name")
	business_unit = frappe.get_all("Business Unit", limit=1, pluck="name")
	mr = frappe.get_doc(
		{
			"doctype": "Material Request",
			"material_request_type": material_request_type,
			"transaction_date": today(),
			"company": "_Test Company",
			"custom_farm": farm[0] if farm else None,
			"custom_business_unit": business_unit[0] if business_unit else None,
			"items": [],
		}
	)
	for row in item_rows:
		defaults = {
			"item_code": "_Test Item",
			"qty": 1,
			"uom": "_Test UOM",
			"stock_uom": "_Test UOM",
			"conversion_factor": 1,
			"schedule_date": today(),
			"warehouse": "_Test Warehouse - _TC",
		}
		defaults.update(row)
		mr.append("items", defaults)
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
		self.employees = frappe.get_all("Employee", filters={"status": "Active"}, limit=2, pluck="name")
		if len(self.employees) < 2:
			self.skipTest("Need at least 2 Active Employee records on this site.")

	def test_excludes_already_issued_employees(self):
		emp1, emp2 = self.employees
		mr = _make_material_request_with_items(
			[
				{"item_code": "_Test Item", "qty": 1, "employee": emp1},
				{"item_code": "_Test Item 2", "qty": 1, "employee": emp2, "issued_via_stock_entry": "STE-0001"},
			]
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

	def test_falls_back_when_employee_field_not_queryable(self):
		# Material Request Item is a core ERPNext doctype -- its table always
		# exists whether or not upande_stores is installed, so the guard must
		# key off upande_stores' own `employee` column, not table existence.
		# Can't actually uninstall upande_stores on this shared dev site, so
		# monkeypatch the guard's own check to simulate a site without it and
		# confirm the query degrades to the unrestricted Employee search
		# instead of raising.
		emp1, emp2 = self.employees
		mr = _make_material_request_with_items(
			[
				{"item_code": "_Test Item", "qty": 1, "employee": emp1},
				{"item_code": "_Test Item 2", "qty": 1, "employee": emp2, "issued_via_stock_entry": "STE-0001"},
			]
		)
		with patch("upande_ta.upande_ta.overrides.stock_entry.frappe.db.has_column", return_value=False):
			results = material_request_employee_query(
				"Employee", "", "name", 0, 20, {"material_request": mr.name}
			)
		names = [r[0] for r in results]
		self.assertIsInstance(results, list)
		self.assertGreater(len(results), 0)
		# Unrestricted fallback: emp2 (already issued) is no longer excluded.
		self.assertIn(emp1, names)
		self.assertIn(emp2, names)

	def test_fallback_does_not_filter_by_status(self):
		non_active = frappe.get_all("Employee", filters={"status": ["!=", "Active"]}, limit=1, pluck="name")
		if not non_active:
			self.skipTest("No non-Active Employee record on this site to verify the status filter is gone.")
		results = material_request_employee_query(
			"Employee", "", "name", 0, 1000, {"material_request": ""}
		)
		names = [r[0] for r in results]
		self.assertIn(non_active[0], names)

	def test_honors_pagination_offset(self):
		employees = frappe.get_all("Employee", filters={"status": "Active"}, limit=3, pluck="name")
		if len(employees) < 3:
			self.skipTest("Need at least 3 Active Employee records on this site.")
		mr = _make_material_request_with_items(
			[{"item_code": "_Test Item", "qty": 1, "employee": e} for e in employees]
		)
		page1 = material_request_employee_query("Employee", "", "name", 0, 2, {"material_request": mr.name})
		page2 = material_request_employee_query("Employee", "", "name", 2, 2, {"material_request": mr.name})
		self.assertEqual(len(page1), 2)
		self.assertGreaterEqual(len(page2), 1)
		self.assertEqual(set(r[0] for r in page1) & set(r[0] for r in page2), set())

	def test_filters_by_item_codes_when_provided(self):
		emp1, emp2 = self.employees
		mr = _make_material_request_with_items(
			[
				{"item_code": "_Test Item", "qty": 10, "employee": emp1},
				{"item_code": "_Test Item 2", "qty": 5, "employee": emp2},
			]
		)
		results = material_request_employee_query(
			"Employee", "", "name", 0, 20, {"material_request": mr.name, "item_codes": ["_Test Item"]}
		)
		names = [r[0] for r in results]
		self.assertIn(emp1, names)
		self.assertNotIn(emp2, names)

	def test_blank_employee_rows_are_never_offered(self):
		emp1 = self.employees[0]
		mr = _make_material_request_with_items(
			[
				{"item_code": "_Test Item", "qty": 10, "employee": emp1},
				{"item_code": "_Test Item 2", "qty": 5},
			],
			material_request_type="Material Transfer",
		)
		results = material_request_employee_query(
			"Employee",
			"",
			"name",
			0,
			20,
			{"material_request": mr.name, "item_codes": ["_Test Item", "_Test Item 2"]},
		)
		names = [r[0] for r in results]
		self.assertEqual(names, [emp1])

	def test_no_item_codes_filter_returns_all_unissued_regardless_of_item_code(self):
		emp1, emp2 = self.employees
		mr = _make_material_request_with_items(
			[
				{"item_code": "_Test Item", "qty": 10, "employee": emp1},
				{"item_code": "_Test Item 2", "qty": 5, "employee": emp2},
			]
		)
		results = material_request_employee_query("Employee", "", "name", 0, 20, {"material_request": mr.name})
		names = [r[0] for r in results]
		self.assertIn(emp1, names)
		self.assertIn(emp2, names)

	def test_same_employee_allocated_two_filtered_items_is_offered_once(self):
		emp = self.employees[0]
		mr = _make_material_request_with_items(
			[
				{"item_code": "_Test Item", "qty": 10, "employee": emp},
				{"item_code": "_Test Item 2", "qty": 5, "employee": emp},
			]
		)
		results = material_request_employee_query(
			"Employee",
			"",
			"name",
			0,
			20,
			{"material_request": mr.name, "item_codes": ["_Test Item", "_Test Item 2"]},
		)
		names = [r[0] for r in results]
		self.assertEqual(names.count(emp), 1)
