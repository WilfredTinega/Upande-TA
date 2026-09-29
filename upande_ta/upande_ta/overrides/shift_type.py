# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Shift Type auto-attendance must not mark Absent on a day that was a week
off or holiday *on that day*.

HRMS works out the days to mark absent from ``process_attendance_after`` up to
today, and drops the holidays of the one list that is in force *today*
(``get_holiday_list(employee)`` with no date). Once an employee's week off
changes, every earlier week off under the old assignment stops being a holiday
to it, and the next hourly run marks those days Absent — as far back as
``process_attendance_after``. Deleting such an Absent does not help either: the
day has no attendance again, so the next run marks it again.

Here each day takes the holiday list in force on that day, the way
``week_off_change.days_in_force`` resolves it (the employee's Holiday List
Assignments, the company's filling the gaps). A day no assignment covers keeps
what HRMS would have said. A holiday list set on the Shift Type itself still
wins for every day, as in HRMS.

Patched at runtime (before_request / before_job), like the Monthly Attendance
Sheet, so hrms core is not edited.
"""

import frappe
from frappe.utils import getdate

_hrms_get_dates_for_attendance = None


def get_dates_for_attendance(self, employee: str) -> list:
	dates = _hrms_get_dates_for_attendance(self, employee)
	if not dates or self.holiday_list:
		return dates

	from upande_ta.upande_ta.week_off_change import days_in_force

	days = days_in_force(employee, getdate(dates[0]), getdate(dates[-1]))
	return [
		date
		for date in dates
		if not (days.get(getdate(date), {}).get("weekly_off") or days.get(getdate(date), {}).get("public"))
	]


get_dates_for_attendance._upande_ta_patched = True


def apply_patch(*args, **kwargs):
	"""Runs before every request and every background job, so it must never
	raise: a failure here would abort the job. It logs and leaves HRMS as it
	is instead."""
	global _hrms_get_dates_for_attendance

	try:
		from hrms.hr.doctype.shift_type.shift_type import ShiftType

		if getattr(ShiftType.get_dates_for_attendance, "_upande_ta_patched", False):
			return

		_hrms_get_dates_for_attendance = ShiftType.get_dates_for_attendance
		ShiftType.get_dates_for_attendance = get_dates_for_attendance
	except Exception:
		frappe.log_error(
			title="upande_ta shift_type.apply_patch failed",
			message=frappe.get_traceback(),
		)
