# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""Group By Greenhouse on the Monthly Attendance Sheet."""

import unittest
from unittest.mock import patch

import frappe

from upande_ta.upande_ta.overrides import monthly_attendance_sheet as mas


class TestGroupByGreenhouse(unittest.TestCase):
	def _group(self, rows, employees):
		with (
			patch.object(mas, "greenhouse_table", return_value=("Employee Greenhouse", "Warehouse")),
			patch.object(mas.frappe, "get_all", return_value=[frappe._dict(r) for r in rows]),
		):
			return mas.group_by_greenhouse(employees)

	def test_an_employee_in_two_greenhouses_is_under_both(self):
		employees = {"E1": {"name": "E1"}, "E2": {"name": "E2"}}
		grouped, order = self._group(
			[
				{"parent": "E1", "greenhouse": "Karen GH 05 - KR"},
				{"parent": "E1", "greenhouse": "Karen GH 06 - KR"},
				{"parent": "E2", "greenhouse": "Karen GH 05 - KR"},
			],
			employees,
		)
		self.assertEqual(order, ["Karen GH 05 - KR", "Karen GH 06 - KR"])
		self.assertEqual(set(grouped["Karen GH 05 - KR"]), {"E1", "E2"})
		self.assertEqual(set(grouped["Karen GH 06 - KR"]), {"E1"})

	def test_an_employee_in_no_greenhouse_is_left_off(self):
		grouped, order = self._group([], {"E1": {"name": "E1"}})
		self.assertEqual((grouped, order), ({}, []))

	def test_a_site_without_the_table_groups_nothing(self):
		with patch.object(mas, "greenhouse_table", return_value=None):
			self.assertEqual(mas.group_by_greenhouse({"E1": {}}), ({}, []))
