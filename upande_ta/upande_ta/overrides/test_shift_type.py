# Copyright (c) 2026, Upande LTD and Contributors

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate
from hrms.hr.doctype.shift_type.shift_type import ShiftType

from upande_ta.upande_ta.overrides import shift_type

DATES = ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]


def _day(weekly_off=False, public=None):
	return {"holiday_list": "_Test", "weekly_off": weekly_off, "public": public}


class IntegrationTestShiftTypeDateEffectiveHolidays(IntegrationTestCase):
	def setUp(self):
		shift_type.apply_patch()
		self.shift = frappe.new_doc("Shift Type")

	def dates(self, days):
		with (
			patch.object(shift_type, "_hrms_get_dates_for_attendance", return_value=list(DATES)),
			patch("upande_ta.upande_ta.week_off_change.days_in_force", return_value=days),
		):
			return ShiftType.get_dates_for_attendance(self.shift, "_T-EMP")

	def test_patch_is_applied_once(self):
		original = shift_type._hrms_get_dates_for_attendance
		shift_type.apply_patch()
		self.assertIs(ShiftType.get_dates_for_attendance, shift_type.get_dates_for_attendance)
		self.assertIs(shift_type._hrms_get_dates_for_attendance, original)

	def test_week_off_in_force_on_the_day_is_not_marked_absent(self):
		days = {getdate(d): _day() for d in DATES}
		days[getdate("2026-08-18")] = _day(weekly_off=True)
		days[getdate("2026-08-20")] = _day(public="Public Holiday")
		self.assertEqual(self.dates(days), ["2026-08-17", "2026-08-19"])

	def test_day_no_assignment_covers_keeps_hrms_answer(self):
		self.assertEqual(self.dates({}), DATES)

	def test_shift_holiday_list_still_wins(self):
		self.shift.holiday_list = "_Test Shift List"
		days = {getdate(d): _day(weekly_off=True) for d in DATES}
		self.assertEqual(self.dates(days), DATES)
