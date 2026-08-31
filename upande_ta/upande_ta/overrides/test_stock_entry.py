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


def _make_material_request_with_employee_rows(employee_rows):
	"""employee_rows: list of dicts appended to custom_employee_data as-is
	(e.g. {"employee": ..., "item_code": ..., "qty": ...} or
	{"employee": ..., "issued_via_stock_entry": ...}). Separate from
	_make_material_request_with_employees (which only supports the item-less
	shape) because upande_stores' own Material Request validate hooks --
	which also run here, since this is a real insert on a site with
	upande_stores installed -- require qty whenever item_code is set, and
	rebuild the Items table from any item_code-bearing rows.
	"""
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
	for row in employee_rows:
		mr.append("custom_employee_data", row)
	mr.insert(ignore_permissions=True, ignore_links=True)
	return mr


class IntegrationTestMaterialRequestEmployeeQuery(IntegrationTestCase):
	def setUp(self):
		# Farm / Business Unit / custom_employee_data all belong to upande_stores,
		# which isn't installed on every site this app runs on (the CI deploy
		# simulation installs frappe + erpnext + hrms + upande_ta only). Check the
		# doctypes exist before querying them -- frappe.get_all() on a doctype that
		# was never installed raises DoesNotExistError instead of returning [].
		for doctype in ("Farm", "Business Unit", "Employee Request"):
			if not frappe.db.exists("DocType", doctype):
				self.skipTest(f"{doctype} is not installed on this site (upande_stores absent).")
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
		mr = _make_material_request_with_employees([(e, None) for e in employees])
		page1 = material_request_employee_query("Employee", "", "name", 0, 2, {"material_request": mr.name})
		page2 = material_request_employee_query("Employee", "", "name", 2, 2, {"material_request": mr.name})
		self.assertEqual(len(page1), 2)
		self.assertGreaterEqual(len(page2), 1)
		self.assertEqual(set(r[0] for r in page1) & set(r[0] for r in page2), set())

	def test_filters_by_item_codes_when_provided(self):
		emp1, emp2 = self.employees
		mr = _make_material_request_with_employee_rows(
			[
				{"employee": emp1, "item_code": "_Test Item", "qty": 10},
				{"employee": emp2, "item_code": "_Test Item 2", "qty": 5},
			]
		)
		results = material_request_employee_query(
			"Employee", "", "name", 0, 20, {"material_request": mr.name, "item_codes": ["_Test Item"]}
		)
		names = [r[0] for r in results]
		self.assertIn(emp1, names)
		self.assertNotIn(emp2, names)

	def test_blank_item_code_rows_are_not_filtered_by_item_codes(self):
		emp1, emp2 = self.employees
		mr = _make_material_request_with_employee_rows(
			[
				{"employee": emp1, "item_code": "_Test Item", "qty": 10},
				{"employee": emp2},
			]
		)
		results = material_request_employee_query(
			"Employee", "", "name", 0, 20, {"material_request": mr.name, "item_codes": ["_Test Item"]}
		)
		names = [r[0] for r in results]
		self.assertIn(emp1, names)
		self.assertIn(emp2, names)

	def test_no_item_codes_filter_returns_all_unissued_regardless_of_item_code(self):
		emp1, emp2 = self.employees
		mr = _make_material_request_with_employee_rows(
			[
				{"employee": emp1, "item_code": "_Test Item", "qty": 10},
				{"employee": emp2, "item_code": "_Test Item 2", "qty": 5},
			]
		)
		results = material_request_employee_query("Employee", "", "name", 0, 20, {"material_request": mr.name})
		names = [r[0] for r in results]
		self.assertIn(emp1, names)
		self.assertIn(emp2, names)
