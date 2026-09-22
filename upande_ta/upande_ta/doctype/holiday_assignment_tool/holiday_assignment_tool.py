# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Holiday Assignment Tool — controller.

Put a set of employees on a Holiday List for a date range, then hand them back
to the list they were on before. Nothing more: the tool keeps no history and
has no undo. Every run leaves ordinary submitted **Holiday List Assignment**
records behind, and those are the record.

To change a run, run the tool again: the newest run wins its window. Any
earlier assignment that starts inside the new window is cancelled (not
deleted, so it stays on the Holiday List Assignment list as history) and the
employee is handed back, after the window, to whatever they would have been on
had the new run never happened.

This doctype is a **Single**, like HRMS's Shift Assignment Tool: one shared
form that does a job. It is never submitted, so the run is a whitelisted
document method, :meth:`HolidayAssignmentTool.assign_holidays`, called from the
form's primary action. The ``employees`` rows are only the work list for the
next run: they are cleared as soon as a run finishes, so the form is always
empty and ready for the next one.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, escape_html, get_link_to_form, getdate, strip_html

from upande_ta.upande_ta.holiday_segments import plan_segments

#: At or below this many employees the work runs inline, inside the request the
#: primary action fired, so the user sees the outcome immediately. Above it, it
#: is handed to a background worker. Same threshold, for the same reason, as
#: HRMS's Shift Assignment Tool (hrms/hr/doctype/shift_assignment_tool).
BATCH_THRESHOLD = 30

#: Realtime event carrying the outcome of a run. For a batched run it is the
#: only thing that reaches the user, because the msgprints below are raised in
#: a worker process whose request nobody is waiting on.
ASSIGN_EVENT = "completed_holiday_assignment_tool"

#: Savepoint name used to isolate one employee from the next. Without it a
#: single bad employee poisons the whole transaction and every later insert
#: fails too, so one unassignable employee would lose the other 499.
SAVEPOINT = "before_holiday_assignment"

#: Employees named individually in a summary message before it collapses to a
#: count. A 500-row document must not render a 500-name msgprint.
MAX_NAMES_IN_MESSAGE = 20


class HolidayAssignmentTool(Document):
	"""Move a set of employees onto a Holiday List for a date range.

	The rule that decides *which list, from which date* lives in
	``upande_ta.upande_ta.holiday_segments.plan_segments`` (pure, unit-tested).
	This class only validates the inputs and performs the writes that rule
	implies.
	"""

	# ──────────────────────────────────────────────────────────────────────
	# Validation
	# ──────────────────────────────────────────────────────────────────────

	def validate(self):
		self.validate_mandatory()
		self.validate_date_range()
		self.validate_from_date()
		self.check_duplicate_employees()

	def validate_mandatory(self):
		# Employees first: an empty table is the likelier mistake, and it reads
		# better than a field-level complaint. Company is a field on the form
		# now — it used to be hidden and set only by the Select Employees
		# dialog, which meant a run whose company came up empty refused against
		# something nobody could see, let alone fill in.
		if not self.employees:
			frappe.throw(_("Please select employees before assigning holidays."))
		for fieldname in ("holiday_list", "from_date", "company"):
			if not self.get(fieldname):
				frappe.throw(_("{0} is required.").format(frappe.bold(_(self.meta.get_label(fieldname)))))

	def validate_date_range(self):
		"""``to_date`` is optional — blank means open-ended, i.e. the assignment
		runs until something else changes it. When it is set it may not precede
		``from_date``."""
		if not self.to_date:
			return
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(
				_("End date {0} cannot be before start date {1}.").format(
					frappe.bold(str(self.to_date)), frappe.bold(str(self.from_date))
				)
			)

	def validate_from_date(self):
		"""HRMS refuses a Holiday List Assignment whose from_date falls outside
		the Holiday List's own period, so catch it here rather than at
		assignment time."""
		if not self.from_date or not self.holiday_list:
			return

		period = frappe.db.get_value("Holiday List", self.holiday_list, ["from_date", "to_date"])
		if not period or not period[0] or not period[1]:
			return
		list_start, list_end = period

		date = getdate(self.from_date)
		if date < getdate(list_start) or date > getdate(list_end):
			frappe.throw(
				_("From Date {0} is outside the Holiday List {1} period ({2} to {3}).").format(
					frappe.bold(str(date)),
					frappe.bold(self.holiday_list),
					frappe.bold(str(list_start)),
					frappe.bold(str(list_end)),
				)
			)

	def check_duplicate_employees(self):
		employees = []
		for row in self.employees:
			if row.employee in employees:
				frappe.throw(_("Employee {0} is added more than once.").format(frappe.bold(row.employee)))
			employees.append(row.employee)

	# ──────────────────────────────────────────────────────────────────────
	# Run
	# ──────────────────────────────────────────────────────────────────────

	@frappe.whitelist()
	def assign_holidays(self):
		"""Create the Holiday List Assignments, inline or in the background.

		The document is saved first so ``validate`` runs and the background
		worker has the employee rows to re-read. The job is enqueued with
		``enqueue_after_commit=True`` because it reads those rows back: queued
		eagerly, it would race the uncommitted save and see the previous run's
		list, or none at all.
		"""
		self.save()
		self.validate_end_date_over_existing()

		if len(self.employees) <= BATCH_THRESHOLD:
			self.create_assignments()
			return

		frappe.enqueue(
			create_assignments_in_background,
			# a long job does not belong on the queue that serves interactive work
			queue="long",
			timeout=3000,
			enqueue_after_commit=True,
			docname=self.name,
		)
		frappe.msgprint(
			_(
				"Creation of Holiday List Assignments for {0} employees has been queued. It may take a few minutes."
			).format(len(self.employees)),
			alert=True,
			indicator="blue",
		)

	def validate_end_date_over_existing(self):
		"""Refuse an open-ended run over an assignment that is already in force.

		Without a To Date there is no boundary to restore anyone at, so the new
		list would take over from ``from_date`` and never give the employee
		back — the arrangement they are on now would simply end, with nothing
		recorded about when it was meant to. Asking for the end date up front
		keeps the chain intact: the override is a period, and the list they
		were on resumes the day after it.

		Checked at run time rather than in ``validate``, so the work list can
		still be saved while the dates are being decided.
		"""
		if self.to_date or not self.from_date or not self.employees:
			return

		from hrms.utils.holiday_list import get_assigned_holiday_list

		on = getdate(self.from_date)
		in_force = []
		for row in self.employees:
			current = get_assigned_holiday_list(row.employee, as_on=on)
			if current:
				in_force.append((row, current))

		if not in_force:
			return

		shown = "<br>".join(
			"{0}: {1}".format(
				frappe.bold(row.employee_name or row.employee), frappe.bold(current)
			)
			for row, current in in_force[:10]
		)
		if len(in_force) > 10:
			shown += "<br>" + _("... and {0} more").format(len(in_force) - 10)

		frappe.throw(
			_(
				"Set the <b>To Date</b> before changing these employees: each is already on a "
				"holiday list as of {0}, and with no end date the new list would take over from "
				"that day and never hand them back.<br><br>{1}<br><br>"
				"With an end date they move onto {2} for the period and return to their own list "
				"the day after."
			).format(frappe.bold(frappe.format(self.from_date, "Date")), shown, frappe.bold(self.holiday_list)),
			title=_("End Date Required"),
		)

	def create_assignments(self):
		"""Per employee: plan the boundaries and create one submitted Holiday
		List Assignment for each. Then empty the work list.

		Each employee is wrapped in its own savepoint and is therefore
		all-or-nothing: an employee whose second boundary is rejected keeps none
		of the first, because a half-applied override (moved onto the holiday
		list, never restored) is worse than not being moved at all.
		"""
		from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import (
			DuplicateAssignment,
		)

		success, failure, skipped = [], [], []
		total = len(self.employees)
		count = 0

		for row in self.employees:
			count += 1

			restore_to = self._list_after_window(row)
			if self.to_date and not restore_to:
				# No Holiday List Assignment in force after the window means there
				# is no list to put this employee back on at to_date + 1. Falling
				# back to the company list would silently move them somewhere
				# they have never been, so the row is skipped and reported.
				skipped.append(self._describe(row))
				self._publish_progress(count, total)
				continue

			try:
				frappe.db.savepoint(SAVEPOINT)
				names, replaced = self._create_assignments_for(row, restore_to)

			except DuplicateAssignment as e:
				# Should not happen — the window is cleared first and the restore
				# is skipped when its date is taken — but HRMS is the final word
				# on uniqueness, so report it rather than crash the run.
				frappe.db.rollback(save_point=SAVEPOINT)
				failure.append(self._describe(row, _("already assigned: {0}").format(_first_line(e))))
				frappe.log_error(
					f"Holiday Assignment Tool: duplicate Holiday List Assignment for employee {row.employee}.",
					reference_doctype="Holiday List Assignment",
				)

			except frappe.ValidationError as e:
				# Most often HRMS's own "Assignment start date cannot be outside
				# holiday list dates": validate() can only check from_date, and
				# the restore boundary depends on the employee's own prior list.
				frappe.db.rollback(save_point=SAVEPOINT)
				failure.append(self._describe(row, _first_line(e)))
				frappe.log_error(
					f"Holiday Assignment Tool: Holiday List Assignment rejected for employee {row.employee}.",
					reference_doctype="Holiday List Assignment",
				)

			except Exception:
				frappe.db.rollback(save_point=SAVEPOINT)
				failure.append(self._describe(row, _("failed — see the Error Log")))
				frappe.log_error(
					f"Holiday Assignment Tool: Holiday List Assignment creation failed for employee {row.employee}.",
					reference_doctype="Holiday List Assignment",
				)

			else:
				entry = self._describe(row)
				entry["count"] = len(names)
				entry["replaced"] = len(replaced)
				if names:
					entry["doc"] = get_link_to_form("Holiday List Assignment", names[0])
				success.append(entry)

			self._publish_progress(count, total)

		self._clear_employees()
		self._report(success, failure, skipped)
		# after _report: the skipped message quotes self.to_date
		self._clear_run_scope()

	def _create_assignments_for(self, row, restore_to) -> tuple[list, list]:
		"""Replace whatever starts inside the window, then create and submit one
		Holiday List Assignment per planned boundary. Returns ``(created,
		cancelled)`` names, created in boundary (ascending date) order.

		No Employee Transfer is created: HRMS resolves an employee's holidays
		from Holiday List Assignment, not from ``Employee.holiday_list``, and
		these overrides are temporary, so moving the permanent pointer would
		only leave it lying about the employee's steady state once the period
		ends.
		"""
		replaced = self._cancel_assignments_in_window(row.employee)

		boundaries = plan_segments(self.holiday_list, self.from_date, self.to_date, [], restore_to)

		names = []
		for boundary_date, holiday_list in boundaries:
			if boundary_date != getdate(self.from_date) and self._assignment_starts_on(
				row.employee, boundary_date
			):
				# Something already takes over the day after the window; it is
				# what the employee returns to, so no restore is needed.
				continue

			assignment = frappe.get_doc(
				{
					"doctype": "Holiday List Assignment",
					"applicable_for": "Employee",
					"assigned_to": row.employee,
					"holiday_list": holiday_list,
					"from_date": boundary_date,
				}
			)
			assignment.insert(ignore_permissions=True)
			assignment.submit()
			names.append(assignment.name)

		return names, replaced

	def _list_after_window(self, row):
		"""The list the employee goes back to at ``to_date + 1``: whatever is in
		force that day *before* this run touches anything. Read before the window
		is cleared, so an earlier run's own restore still counts. Falls back to
		the row's ``prior_holiday_list`` (the fetch's Employee.holiday_list
		guess for employees never given an assignment). Open-ended runs restore
		nothing."""
		if not self.to_date:
			return None
		from hrms.utils.holiday_list import get_assigned_holiday_list

		return (
			get_assigned_holiday_list(row.employee, as_on=add_days(getdate(self.to_date), 1))
			or row.prior_holiday_list
		)

	def _cancel_assignments_in_window(self, employee) -> list:
		"""Cancel the employee's submitted assignments starting inside
		``from_date .. to_date`` (just ``from_date`` when open-ended), so the new
		run owns every day of its window. Cancelled, not deleted: they stay on
		the Holiday List Assignment list as the record of what was there."""
		window_end = self.to_date or self.from_date
		names = frappe.get_all(
			"Holiday List Assignment",
			filters={
				"applicable_for": "Employee",
				"assigned_to": employee,
				"docstatus": 1,
				"from_date": ["between", [self.from_date, window_end]],
			},
			pluck="name",
		)
		for name in names:
			assignment = frappe.get_doc("Holiday List Assignment", name)
			assignment.flags.ignore_permissions = True
			assignment.cancel()
		return names

	def _assignment_starts_on(self, employee, date) -> bool:
		return bool(
			frappe.db.exists(
				"Holiday List Assignment",
				{"assigned_to": employee, "from_date": date, "docstatus": 1},
			)
		)

	def _clear_employees(self):
		"""Empty the work list once a run is done, so the next run starts from a
		blank table instead of re-running whoever was picked last time. The
		outcome is already in the Holiday List Assignment records and in the
		summary the user is shown."""
		frappe.db.delete(
			"Holiday Assignment Tool Employee",
			{"parenttype": self.doctype, "parent": self.name, "parentfield": "employees"},
		)
		self.set("employees", [])

	def _clear_run_scope(self):
		"""Blank the run's own inputs once it is done.

		The tool is a Single, so whatever the last run left in it is what the
		next person sees when they open the form — and a company and a window
		belonging to somebody else's run are never the right defaults for the
		next one. The Holiday List is kept: it is the one field that says what
		the tool is for, and it is validated on every run anyway.
		"""
		fields = {"company": None, "from_date": None, "to_date": None}
		frappe.db.set_single_value(self.doctype, fields)
		for fieldname in fields:
			self.set(fieldname, None)

	# ──────────────────────────────────────────────────────────────────────
	# Progress and reporting
	# ──────────────────────────────────────────────────────────────────────

	def _describe(self, row, reason=None) -> dict:
		entry = {"employee": row.employee, "employee_name": row.employee_name or row.employee}
		if reason:
			entry["reason"] = reason
		return entry

	def _publish_progress(self, count, total):
		if not total:
			return
		frappe.publish_progress(count * 100 / total, title=_("Creating Holiday List Assignments..."))

	def _report(self, success, failure, skipped):
		"""One summary for the user, then one realtime payload for the desk.
		``clear_messages`` first: the per-record validation chatter from hundreds
		of inserts is noise, and the lines below are the whole answer."""
		frappe.clear_messages()

		if success:
			created = sum(cint(entry.get("count")) for entry in success)
			message = _("Created {0} Holiday List Assignment(s) for {1} employee(s).").format(
				created, len(success)
			)
			replaced = sum(cint(entry.get("replaced")) for entry in success)
			if replaced:
				message += " " + _("Replaced {0} earlier one(s).").format(replaced)
			frappe.msgprint(message, alert=True, indicator="green")

		if skipped:
			frappe.msgprint(
				_(
					"Skipped {0} employee(s) with no previous Holiday List Assignment — there is nothing to restore them to after {2}, and guessing a list would move them somewhere they have never been. Give them a Holiday List Assignment first, then run this tool again for them: {1}"
				).format(
					len(skipped),
					_names(skipped),
					frappe.bold(str(self.to_date)) if self.to_date else "",
				),
				title=_("Skipped Employees"),
				indicator="orange",
			)

		if failure:
			frappe.msgprint(
				_("{0} employee(s) failed:<br>{1}").format(len(failure), _reasons(failure)),
				title=_("Failures"),
				indicator="red",
			)

		# Broadcast with the docname in the payload rather than to the doc room,
		# which would only reach clients subscribed at that instant; the desk
		# listener filters on docname. On a Single that is the doctype name, so
		# every session with the tool open hears it.
		frappe.publish_realtime(
			ASSIGN_EVENT,
			message={
				"docname": self.name,
				"success": success,
				"failure": failure,
				"skipped": skipped,
			},
			doctype="Holiday Assignment Tool",
			after_commit=True,
		)


# ──────────────────────────────────────────────────────────────────────────
# Background entry point
# ──────────────────────────────────────────────────────────────────────────


def create_assignments_in_background(docname: str):
	"""Queued by :meth:`HolidayAssignmentTool.assign_holidays` above
	:data:`BATCH_THRESHOLD`. Takes only a name so nothing but a string crosses
	into the worker; the document is re-read there, after the save committed."""
	frappe.get_doc("Holiday Assignment Tool", docname).create_assignments()


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _first_line(exception) -> str:
	"""A frappe.throw message as one short plain-text line — frappe.bold() and
	get_link_to_form() put HTML in there, which a summary list must not carry
	through verbatim."""
	text = strip_html(str(exception) or "").strip()
	text = " ".join(text.split())
	return text[:200] if text else _("rejected by Holiday List Assignment")


def _names(entries: list) -> str:
	shown = [
		escape_html(entry.get("employee_name") or entry["employee"])
		for entry in entries[:MAX_NAMES_IN_MESSAGE]
	]
	text = ", ".join(shown)
	remaining = len(entries) - len(shown)
	if remaining > 0:
		text += _(" and {0} more").format(remaining)
	return text


def _reasons(entries: list) -> str:
	lines = [
		"{0}: {1}".format(
			escape_html(entry.get("employee_name") or entry["employee"]),
			escape_html(entry.get("reason") or ""),
		)
		for entry in entries[:MAX_NAMES_IN_MESSAGE]
	]
	remaining = len(entries) - len(lines)
	if remaining > 0:
		lines.append(_("... and {0} more — see the Error Log.").format(remaining))
	return "<br>".join(lines)
