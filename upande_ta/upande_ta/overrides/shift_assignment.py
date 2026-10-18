# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Keep Employee.default_shift on the shift the employee is actually on.

A Shift Assignment only takes effect on its start date, so the employee list
is updated then: straight away when the assignment is submitted, cancelled or
switched Active/Inactive and already in effect, and by the daily job for one
that starts later.

The shift in effect is the Active, submitted assignment covering the date,
the latest-starting one where two overlap. With none covering it, the last
one that started before it stays: a cancelled assignment hands the employee
back to the one before it rather than leaving the cancelled shift behind.
"""

import frappe
from frappe.utils import getdate, today


def on_change(doc, method=None):
	"""Shift Assignment on_submit / on_cancel / on_update_after_submit."""
	if doc.employee and doc.start_date and getdate(doc.start_date) <= getdate(today()):
		sync_default_shift([doc.employee])


def sync_default_shifts_daily():
	"""Scheduler: every employee with an assignment starting today."""
	employees = frappe.get_all(
		"Shift Assignment",
		filters={"docstatus": 1, "status": "Active", "start_date": today()},
		pluck="employee",
		distinct=True,
	)
	if employees:
		sync_default_shift(employees)


def sync_default_shift(employees: list | None = None, on_date=None) -> int:
	"""Set default_shift to the shift in effect on ``on_date`` (today). Every
	employee with a submitted assignment when ``employees`` is not given.
	Returns how many employees changed."""
	on_date = getdate(on_date or today())
	if employees is not None:
		employees = list({e for e in employees if e})
		if not employees:
			return 0

	ShiftAssignment = frappe.qb.DocType("Shift Assignment")
	query = (
		frappe.qb.from_(ShiftAssignment)
		.select(
			ShiftAssignment.employee,
			ShiftAssignment.shift_type,
			ShiftAssignment.start_date,
			ShiftAssignment.end_date,
			ShiftAssignment.creation,
		)
		.where(
			(ShiftAssignment.docstatus == 1)
			& (ShiftAssignment.status == "Active")
			& (ShiftAssignment.start_date <= on_date)
		)
	)
	if employees is not None:
		query = query.where(ShiftAssignment.employee.isin(employees))

	# employee -> (covers the date, start, creation, shift); highest wins
	best = {}
	for r in query.run(as_dict=True):
		covers = r.end_date is None or getdate(r.end_date) >= on_date
		key = (covers, getdate(r.start_date), r.creation, r.shift_type)
		if r.employee not in best or key[:3] > best[r.employee][:3]:
			best[r.employee] = key

	if not best:
		return 0
	current = {
		e.name: e.default_shift
		for e in frappe.get_all(
			"Employee", filters={"name": ["in", list(best)]}, fields=["name", "default_shift"]
		)
	}
	changed = 0
	for employee, key in best.items():
		if employee in current and current[employee] != key[3]:
			frappe.db.set_value("Employee", employee, "default_shift", key[3])
			changed += 1
	return changed
