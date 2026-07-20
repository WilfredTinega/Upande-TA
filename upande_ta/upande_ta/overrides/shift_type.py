# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Make Shift Type auto-attendance holiday-aware by *date*.

Without this, once an employee's week off changes the Shift Type resolves the
holiday from whatever list is current now and applies it to every date — so a
day that used to be the employee's weekly off is no longer seen as a holiday and
gets marked **Absent**. We resolve the holiday list that was in force *on each
date* via Holiday List Assignments, falling back to the employee's static
``Employee.holiday_list`` when they have no assignment (unchanged behaviour).

Patched at runtime (before_request / before_job) so nothing in hrms core is
edited — the same pattern this app already uses for the Monthly Attendance Sheet.
"""

import frappe

_patched = False


def _effective_holiday_dates(employee, start_date, end_date):
	from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee as erp_get

	from upande_ta.upande_ta.holiday_list import (
		get_holiday_dates_between,
		get_holiday_dates_between_range,
		get_holiday_list_for_employee,
	)

	# Date-effective when the employee has any Holiday List Assignment covering
	# the range; otherwise fall back to their static list (current v15 behaviour).
	if get_holiday_list_for_employee(employee, as_on=start_date) or get_holiday_list_for_employee(
		employee, as_on=end_date
	):
		return get_holiday_dates_between_range(
			employee, start_date, end_date, raise_exception_for_holiday_list=False
		)

	static_hl = erp_get(employee, False)
	return get_holiday_dates_between(static_hl, start_date, end_date) if static_hl else []


def _get_holiday_list(self, employee, date=None):
	if self.holiday_list:
		return self.holiday_list

	from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee as erp_get

	from upande_ta.upande_ta.holiday_list import get_holiday_list_for_employee

	return get_holiday_list_for_employee(employee, as_on=date) or erp_get(employee, False)


def _should_mark_attendance(self, employee, attendance_date):
	from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday

	if self.mark_auto_attendance_on_holidays:
		return True

	holiday_list = self.get_holiday_list(employee, attendance_date)
	if is_holiday(holiday_list, attendance_date):
		return False
	return True


def _get_dates_for_attendance(self, employee):
	from hrms.utils import get_date_range

	start_date, end_date = self.get_start_and_end_dates(employee)

	# no shift assignment found, no need to process absent attendance records
	if start_date is None:
		return []

	date_range = get_date_range(start_date, end_date)

	# skip marking absent on holidays — resolved per date, with a shift-level
	# holiday_list still taking precedence when one is set on the Shift Type.
	if self.holiday_list:
		from upande_ta.upande_ta.holiday_list import get_holiday_dates_between

		holiday_dates = get_holiday_dates_between(self.holiday_list, start_date, end_date)
	else:
		holiday_dates = _effective_holiday_dates(employee, start_date, end_date)

	marked_attendance_dates = self.get_marked_attendance_dates_between(employee, start_date, end_date)

	return sorted(set(date_range) - set(holiday_dates) - set(marked_attendance_dates))


def apply_patch(*args, **kwargs):
	global _patched

	# Runs on before_request AND before_job (i.e. before every background job,
	# payroll included). It must NEVER raise — a failure here would abort the
	# job. Degrade to a logged no-op instead.
	try:
		from hrms.hr.doctype.shift_type.shift_type import ShiftType

		if _patched and getattr(ShiftType.get_holiday_list, "_upande_ta_patched", False):
			return

		_get_holiday_list._upande_ta_patched = True

		ShiftType.get_holiday_list = _get_holiday_list
		ShiftType.should_mark_attendance = _should_mark_attendance
		ShiftType.get_dates_for_attendance = _get_dates_for_attendance
		_patched = True
	except Exception:
		frappe.log_error(
			title="upande_ta shift_type.apply_patch failed (holiday date-effective patch skipped)",
			message=frappe.get_traceback(),
		)
