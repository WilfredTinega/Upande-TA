# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Replace the per-employee week-off copies that run past 31 December.

``repoint_week_off_copies_to_shared_lists`` left three copies on kaitet-group
("101221 Wednesday Week Off 26-08-2026 to 31-01-2027" and two like it): no
shared list covers January 2027 yet. A Holiday List Assignment has no end
date, so the copy is matched on its own year, the same year every other
employee's shared list stops at, and is replaced like the rest.
"""

import frappe
from frappe.utils import getdate

from upande_ta.patches.v1.repoint_week_off_copies_to_shared_lists import (
	_copies,
	_weekdays_if_uniform,
	repoint,
)


def execute():
	from upande_ta.upande_ta.week_off_change import matching_list

	for copy in _copies():
		start = getdate(copy.from_date)
		if getdate(copy.to_date).year == start.year:
			continue
		weekdays = _weekdays_if_uniform(copy)
		if weekdays is None:
			continue
		target = matching_list(start, start.replace(month=12, day=31), weekdays)
		if target:
			repoint(copy.name, target)

	frappe.cache.delete_value("holidays_between_dates")
