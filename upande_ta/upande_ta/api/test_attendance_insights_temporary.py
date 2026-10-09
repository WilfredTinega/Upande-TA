# Copyright (c) 2026, Upande LTD and Contributors

from collections import defaultdict
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from upande_ta.upande_ta.api import attendance_insights as ai

# Biometric Setting "Show Temporary Workers on Attendance Insights": ticked, the
# Temporary employment type is shown even when an Attendance Filters row hides it
# from the Monthly Attendance Sheet; unticked, it is left out. Employees excluded by
# name stay excluded. Settings are mocked: these run on a bare site (CI) as well.

TEMPORARY = {"T1", "T2"}


def _excluded(show, sheet_excluded, by_name=()):
	settings = frappe._dict(
		attendance_employee_filters=[frappe._dict(employee=e, excluded=1) for e in by_name]
	)
	frappe.local.request_cache = defaultdict(dict)
	with (
		patch.object(ai, "get_disabled_employee_names", return_value=set(sheet_excluded)),
		patch.object(ai.frappe.db, "exists", return_value=True),
		patch.object(ai.frappe, "get_meta") as meta,
		patch.object(ai.frappe, "get_all", return_value=sorted(TEMPORARY)),
		patch.object(ai.frappe.db, "get_single_value", return_value=1 if show else 0),
		patch.object(ai.frappe, "get_single", return_value=settings),
	):
		meta.return_value.has_field.return_value = True
		try:
			return set(ai.excluded_employees())
		finally:
			frappe.local.request_cache = defaultdict(dict)


class UnitTestInsightsTemporaryWorkers(UnitTestCase):
	def test_ticked_shows_temporary_hidden_from_the_sheet(self):
		# The sheet hides the Temporary employment type (T1, T2) and a permanent P1.
		self.assertEqual(_excluded(True, {"T1", "T2", "P1"}), {"P1"})

	def test_ticked_keeps_a_temporary_excluded_by_name(self):
		self.assertEqual(_excluded(True, {"T1", "T2"}, by_name=["T2"]), {"T2"})

	def test_unticked_hides_every_temporary(self):
		self.assertEqual(_excluded(False, {"P1"}), {"P1", "T1", "T2"})
