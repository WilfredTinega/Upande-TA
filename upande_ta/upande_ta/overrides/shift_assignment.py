# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""A roster submitted after the days it covers takes the punches already in.

HRMS resolves a check-in's shift once, when it is inserted. A punch made
before the Shift Assignment existed is stored ``offshift`` with no shift,
auto attendance never reads it, and when the assignment lands the same run
marks the day Absent. Here the check-ins in the assignment's window are
re-resolved on submit, so the next run marks them Present and the Absent
gives way (``overrides/attendance.py``).
"""

from frappe.utils import getdate, today

from upande_ta.upande_ta.shift_change import _restamp_checkins


def restamp_checkins_on_submit(doc, method=None):
	if not doc.employee or not doc.start_date:
		return
	start = getdate(doc.start_date)
	end = getdate(today())
	if doc.end_date:
		end = min(end, getdate(doc.end_date))
	if start > end:
		return
	_restamp_checkins(doc.employee, start, end)
