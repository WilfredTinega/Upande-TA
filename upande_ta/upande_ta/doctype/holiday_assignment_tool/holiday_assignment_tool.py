# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Holiday Assignment Tool — controller.

Phase 1 is ``validate``. Phase 2 is the employee fetch
(``upande_ta.upande_ta.api.holiday_assignment_employees``) and the pure segment
planner (``upande_ta.upande_ta.holiday_segments``). Phase 3, below, is the
write side: turning each planned boundary into a submitted **Holiday List
Assignment**, and unwinding them again.

This doctype is a **Single**, like HRMS's Shift Assignment Tool: one shared
form that does a job, not a filing cabinet of past runs. It is therefore never
submitted and never cancelled, so the two halves of the write side are
whitelisted document methods — :meth:`HolidayAssignmentTool.assign_holidays`
and :meth:`HolidayAssignmentTool.undo_assignment` — which the desk form calls
from its primary action, picked by the ``action`` field. That field is what
keeps reversal reachable now that ``on_cancel`` is gone.

A Single still keeps its child tables: ``exceptions`` and ``employees`` rows
live in their own tables with ``parent = "Holiday Assignment Tool"``. That
matters, because ``assignments_json`` — the per-employee backlink
:meth:`undo_assignment` unwinds from — is written onto those rows with
``db_set``, which needs them to exist in the database. Hence the ``self.save()``
at the top of :meth:`assign_holidays`: the desk posts the in-memory document
(``frm.call({doc: frm.doc})``), and saving it both runs ``validate`` and gives
every child row a real name to write back to.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, escape_html, get_link_to_form, getdate, strip_html

from upande_ta.upande_ta.holiday_segments import plan_segments

#: At or below this many employees the work runs inline, inside the request the
#: primary action fired, so the user sees the outcome immediately. Above it, it
#: is handed to a background worker. Same threshold, for the same reason, as
#: HRMS's Shift Assignment Tool (hrms/hr/doctype/shift_assignment_tool).
BATCH_THRESHOLD = 30

#: Realtime events carrying the outcome of a run. The desk form subscribes to
#: both in its ``refresh`` (see ``listen_for_completion`` in
#: holiday_assignment_tool.js) — for a batched run they are the only thing that
#: reaches the user, because the msgprints below are raised in a worker process
#: whose request nobody is waiting on. The strings are unchanged from when this
#: doctype was submittable, so a desk session left open across the upgrade still
#: hears them.
ASSIGN_EVENT = "completed_holiday_assignment_tool"
UNDO_EVENT = "completed_holiday_assignment_tool_cancellation"

#: Savepoint name used to isolate one employee from the next. Without it a
#: single bad employee poisons the whole transaction and every later insert
#: fails too, so one unassignable employee would lose the other 499.
SAVEPOINT = "before_holiday_assignment"

#: Employees named individually in a summary message before it collapses to a
#: count. A 500-row document must not render a 500-name msgprint.
MAX_NAMES_IN_MESSAGE = 20


class HolidayAssignmentTool(Document):
	"""Move a filtered set of employees onto a Holiday List for a date range.

	The rule that decides *which list, from which date* lives in
	``upande_ta.upande_ta.holiday_segments.plan_segments`` (pure, unit-tested).
	This class only validates the inputs and performs the writes that rule
	implies.

	It is a Single: there is one of these per site, re-used for every run.
	``validate`` still runs, because :meth:`assign_holidays` saves the document
	before it writes anything.
	"""

	# ──────────────────────────────────────────────────────────────────────
	# Validation (phase 1)
	# ──────────────────────────────────────────────────────────────────────

	def validate(self):
		# A Single that predates the `action` field has NULL stored for it, and
		# the mandatory check would then refuse every save — defaults are only
		# applied to new documents. Fall back the way new_doc would.
		if not self.action:
			self.action = "Assign Holidays"

		self.validate_mandatory()
		self.validate_date_range()
		self.validate_from_date()
		self.validate_exceptions()
		self.check_duplicate_employees()

	def validate_mandatory(self):
		for fieldname in ("company", "holiday_list", "from_date"):
			if not self.get(fieldname):
				frappe.throw(_("{0} is required.").format(frappe.bold(_(self.meta.get_label(fieldname)))))
		if not self.employees:
			frappe.throw(_("Please add employees before assigning holidays."))

	def validate_date_range(self):
		"""``to_date`` is optional — blank means open-ended, i.e. the assignment
		runs until something else changes it, which is Bulk Week Off's
		behaviour. When it is set it may not precede ``from_date``."""
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
		the Holiday List's own period, so catch it here (same check as
		Bulk Week Off.validate_from_date) rather than at assignment time."""
		if not self.from_date or not self.holiday_list:
			return
		self.assert_within_holiday_list_period(self.holiday_list, self.from_date, _("Assignment start date"))

	def validate_exceptions(self):
		"""Every exception must name a distinct date inside the period, and its
		own Holiday List must cover that date (same HRMS constraint as above)."""
		if not self.exceptions:
			return

		from_date = getdate(self.from_date)
		to_date = getdate(self.to_date) if self.to_date else None
		seen = {}

		for row in self.exceptions:
			if not row.exception_date:
				frappe.throw(_("Row {0}: Exception Date is required.").format(row.idx))
			if not row.holiday_list:
				frappe.throw(_("Row {0}: Holiday List is required.").format(row.idx))

			exception_date = getdate(row.exception_date)

			if exception_date < from_date or (to_date and exception_date > to_date):
				frappe.throw(
					_("Row {0}: Exception date {1} is outside the assignment period ({2} to {3}).").format(
						row.idx,
						frappe.bold(str(row.exception_date)),
						frappe.bold(str(self.from_date)),
						frappe.bold(str(self.to_date) if self.to_date else _("open-ended")),
					)
				)

			if exception_date in seen:
				frappe.throw(
					_("Row {0}: Exception date {1} is already used in row {2}.").format(
						row.idx, frappe.bold(str(row.exception_date)), seen[exception_date]
					)
				)
			seen[exception_date] = row.idx

			self.assert_within_holiday_list_period(
				row.holiday_list,
				row.exception_date,
				_("Row {0}: exception date").format(row.idx),
			)

	def assert_within_holiday_list_period(self, holiday_list, date, context):
		"""Throw unless ``date`` falls inside ``holiday_list``'s own period.
		``context`` prefixes the error so the user knows which date it is."""
		period = frappe.db.get_value("Holiday List", holiday_list, ["from_date", "to_date"])
		if not period:
			return
		list_start, list_end = period
		if not list_start or not list_end:
			return

		date = getdate(date)
		if date < getdate(list_start) or date > getdate(list_end):
			frappe.throw(
				_("{0} {1} is outside the Holiday List {2} period ({3} to {4}).").format(
					context,
					frappe.bold(str(date)),
					frappe.bold(holiday_list),
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
	# Run / undo (phase 3)
	# ──────────────────────────────────────────────────────────────────────

	@frappe.whitelist()
	def assign_holidays(self):
		"""Create the Holiday List Assignments, inline or in the background.

		This was ``on_submit``. A Single is never submitted, so the desk form
		calls this straight from its primary action, which the ``action`` field
		selects (see ``set_primary_action`` in holiday_assignment_tool.js).

		The document is saved first, in this request, and three things ride on
		that: ``validate`` runs, so every check that used to guard the submit
		still guards the run; the ``employees`` rows the user just ticked into
		the grid are given real names, without which the ``db_set`` of
		``assignments_json`` below would update no row at all and
		:meth:`undo_assignment` would have nothing to unwind; and the background
		worker gets a document worth re-reading.

		**``enqueue_after_commit=True`` survives the conversion — but not for
		its original reason.** It used to be about ``docstatus``: a submittable
		document's ``docstatus = 1`` was written by ``save()`` and only durable
		at commit, so a worker that started early could read this document as a
		draft. A Single has no docstatus, so that reason is gone. What replaces
		it is stronger: the job carries nothing but a name and re-reads the
		document in the worker, and what it must read is the ``employees`` rows
		that the ``self.save()`` above has written **but not yet committed**.
		Enqueue eagerly and the job races the transaction it depends on, seeing
		the previous run's employee list, or none at all. Deferring it to
		``frappe.db.after_commit`` also keeps the old safety property: if this
		request dies after the enqueue, the save rolls back and the job is never
		queued, rather than assigning holidays from a document state that no
		longer exists.
		"""
		self.save()

		if len(self.employees) <= BATCH_THRESHOLD:
			self.create_assignments()
			return

		frappe.enqueue(
			create_assignments_in_background,
			# A 3000s job does not belong on the queue that serves interactive
			# work; the shift tool leaves this at "default", which is a bug we
			# are not copying.
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

	@frappe.whitelist()
	def undo_assignment(self):
		"""Cancel the Holiday List Assignments this document created.

		This was ``on_cancel``. With no cancel to hang it on, reversal is its
		own action: the user switches ``action`` to "Undo Assignment" and presses
		the primary button.

		``reload()`` first, deliberately. The desk posts its in-memory copy of
		the Single, which may predate the run — a batched run writes
		``assignments_json`` from a worker, long after the form last read the
		document. The stored rows are the only record of what is owed a
		cancellation, so they are re-read rather than trusted from the client.
		That also means an undo never writes the client's edits back: undoing is
		about what was done, not about what the form currently shows.

		``enqueue_after_commit=True`` is kept for the reason given in
		:meth:`assign_holidays`, minus the save: nothing here depends on this
		transaction, but a request that dies after the enqueue should not leave
		a worker unwinding a document on its own. Until the job runs,
		``assignments_json`` still names every record that is owed a
		cancellation, so a worker that never ran leaves a recoverable trail
		rather than an untraceable one.
		"""
		self.reload()

		if not self.employees:
			frappe.throw(
				_("There is nothing to undo — this tool has no employees recorded."),
				title=_("Nothing to Undo"),
			)

		if len(self.employees) <= BATCH_THRESHOLD:
			self.cancel_assignments()
			return

		frappe.enqueue(
			cancel_assignments_in_background,
			queue="long",
			timeout=3000,
			enqueue_after_commit=True,
			docname=self.name,
		)
		frappe.msgprint(
			_(
				"Cancellation of the Holiday List Assignments for {0} employees has been queued. It may take a few minutes."
			).format(len(self.employees)),
			alert=True,
			indicator="blue",
		)

	# ──────────────────────────────────────────────────────────────────────
	# Creation
	# ──────────────────────────────────────────────────────────────────────

	def create_assignments(self):
		"""Per employee: plan the boundaries, create one submitted Holiday List
		Assignment for each, record their names on the row.

		Each employee is wrapped in its own savepoint and is therefore
		all-or-nothing: an employee whose third boundary is rejected keeps none
		of the first two, because a half-applied override (moved onto the
		holiday list, never restored) is worse than not being moved at all.
		"""
		from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import (
			DuplicateAssignment,
		)

		success, failure, skipped = [], [], []
		total = len(self.employees)
		count = 0

		for row in self.employees:
			count += 1

			if self._nothing_to_restore_to(row):
				# No prior Holiday List Assignment means there is no list to put
				# this employee back on at to_date + 1. Falling back to the
				# company list would silently move them somewhere they have
				# never been, so the whole row is skipped and reported instead.
				skipped.append(self._describe(row))
				self._publish_progress(count, total, _("Creating Holiday List Assignments..."))
				continue

			try:
				frappe.db.savepoint(SAVEPOINT)
				names = self._create_assignments_for(row)
				row.db_set("assignments_json", json.dumps(names), update_modified=False)

			except DuplicateAssignment as e:
				# The fetch excludes employees who already hold an assignment on
				# from_date, but an exception boundary (or a restore at
				# to_date + 1) can still land on a date an unrelated assignment
				# already owns. That is the user's to resolve, not ours to
				# overwrite.
				frappe.db.rollback(save_point=SAVEPOINT)
				failure.append(self._describe(row, _("already assigned: {0}").format(_first_line(e))))
				frappe.log_error(
					f"Holiday Assignment Tool {self.name}: duplicate Holiday List Assignment for employee {row.employee}.",
					reference_doctype="Holiday List Assignment",
				)

			except frappe.ValidationError as e:
				# Most often HRMS's own "Assignment start date cannot be outside
				# holiday list dates": validate() can only check the boundaries
				# it knows up front, and the restore boundary depends on the
				# employee's own prior list, which it cannot see.
				frappe.db.rollback(save_point=SAVEPOINT)
				failure.append(self._describe(row, _first_line(e)))
				frappe.log_error(
					f"Holiday Assignment Tool {self.name}: Holiday List Assignment rejected for employee {row.employee}.",
					reference_doctype="Holiday List Assignment",
				)

			except Exception:
				frappe.db.rollback(save_point=SAVEPOINT)
				failure.append(self._describe(row, _("failed — see the Error Log")))
				frappe.log_error(
					f"Holiday Assignment Tool {self.name}: Holiday List Assignment creation failed for employee {row.employee}.",
					reference_doctype="Holiday List Assignment",
				)

			else:
				entry = self._describe(row)
				entry["count"] = len(names)
				if names:
					entry["doc"] = get_link_to_form("Holiday List Assignment", names[0])
				success.append(entry)

			self._publish_progress(count, total, _("Creating Holiday List Assignments..."))

		self._report(success, failure, skipped, ASSIGN_EVENT, cancelling=False)

	def _create_assignments_for(self, row) -> list:
		"""Create and submit one Holiday List Assignment per planned boundary,
		returning their names in boundary (ascending date) order.

		``holiday_list_start`` / ``holiday_list_end`` are deliberately never set:
		they are ``is_virtual`` fields backed by Python properties on
		HolidayListAssignment, so assigning them writes nothing and only makes
		the code look like it did something.

		No Employee Transfer is created here, and that is deliberate. Bulk Week
		Off creates one to move ``Employee.holiday_list``, but HRMS does not
		consult that field when it resolves an employee's holidays — every
		lookup is redirected into the Holiday List Assignment resolver (see
		hrms/hooks.py). This tool's overrides are temporary and date-bounded, so
		moving the permanent pointer would buy nothing functionally and would
		leave ``Employee.holiday_list`` lying about the employee's steady state
		once the period ends.
		"""
		boundaries = plan_segments(
			self.holiday_list,
			self.from_date,
			self.to_date,
			self.exceptions,
			row.prior_holiday_list,
		)

		names = []
		for boundary_date, holiday_list in boundaries:
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

		return names

	def _nothing_to_restore_to(self, row) -> bool:
		"""True when this row must be skipped: the period ends, but the employee
		has no prior Holiday List Assignment to be returned to.

		An open-ended document (no ``to_date``) restores nothing by design, so
		the missing prior list is not a problem there.
		"""
		return bool(self.to_date) and not row.prior_holiday_list

	# ──────────────────────────────────────────────────────────────────────
	# Undo
	# ──────────────────────────────────────────────────────────────────────

	def cancel_assignments(self):
		"""Cancel every Holiday List Assignment recorded on the rows."""
		success, failure, skipped = [], [], []
		total = len(self.employees)
		count = 0

		for row in self.employees:
			count += 1
			names = _stored_assignment_names(row)

			if not names:
				skipped.append(self._describe(row))
				self._publish_progress(count, total, _("Cancelling Holiday List Assignments..."))
				continue

			try:
				frappe.db.savepoint(SAVEPOINT)
				cancelled = _cancel_assignments(names)
				row.db_set("assignments_json", None, update_modified=False)

			except Exception:
				frappe.db.rollback(save_point=SAVEPOINT)
				failure.append(self._describe(row, _("could not be cancelled — see the Error Log")))
				frappe.log_error(
					f"Holiday Assignment Tool {self.name}: Holiday List Assignment cancellation failed for employee {row.employee}.",
					reference_doctype="Holiday List Assignment",
				)

			else:
				entry = self._describe(row)
				entry["count"] = cancelled
				success.append(entry)

			self._publish_progress(count, total, _("Cancelling Holiday List Assignments..."))

		self._report(success, failure, skipped, UNDO_EVENT, cancelling=True)

	# ──────────────────────────────────────────────────────────────────────
	# Progress and reporting
	# ──────────────────────────────────────────────────────────────────────

	def _describe(self, row, reason=None) -> dict:
		entry = {"employee": row.employee, "employee_name": row.employee_name or row.employee}
		if reason:
			entry["reason"] = reason
		return entry

	def _publish_progress(self, count, total, title):
		if not total:
			return
		frappe.publish_progress(count * 100 / total, title=title)

	def _report(self, success, failure, skipped, event, cancelling: bool):
		"""One summary for the user, then one realtime payload for the desk.

		``clear_messages`` first, exactly as the shift tool does it: the
		per-record validation chatter accumulated across hundreds of inserts is
		noise, and the three lines below are the whole answer. In the inline
		path these msgprints are what the user actually sees; in the background
		path only the realtime payload reaches anyone.
		"""
		frappe.clear_messages()

		if success:
			created = sum(cint(entry.get("count")) for entry in success)
			if cancelling:
				message = _("Cancelled {0} Holiday List Assignment(s) for {1} employee(s).")
			else:
				message = _("Created {0} Holiday List Assignment(s) for {1} employee(s).")
			frappe.msgprint(message.format(created, len(success)), alert=True, indicator="green")

		if skipped:
			if cancelling:
				text = _("{0} employee(s) had no recorded Holiday List Assignment to cancel: {1}")
			else:
				text = _(
					"Skipped {0} employee(s) with no previous Holiday List Assignment — there is nothing to restore them to after {2}, and guessing a list would move them somewhere they have never been. Give them a Holiday List Assignment first, then run this tool again for them: {1}"
				)
			frappe.msgprint(
				text.format(
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

		# ``docname`` rides in the payload rather than being passed to
		# publish_realtime(): passing it would move the message into the doc room
		# and only reach clients that happen to be subscribed to this document at
		# that instant. Broadcasting and letting the listener filter is the
		# behaviour the desk form is written against — see notify_completion() in
		# holiday_assignment_tool.js, which drops anything that is not its own
		# document, since a site-room event reaches every open desk session.
		#
		# On a Single ``self.name`` is the doctype name, so every desk session
		# looking at this form matches and every one of them is told. That is
		# the right answer rather than a degenerate one: there is only one
		# Holiday Assignment Tool, so anyone who has it open is looking at the
		# run that just finished. The filter still earns its keep by dropping
		# stray payloads from an unrelated doctype's event of the same name.
		frappe.publish_realtime(
			event,
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
# Background entry points
# ──────────────────────────────────────────────────────────────────────────


def create_assignments_in_background(docname: str):
	"""Queued by :meth:`HolidayAssignmentTool.assign_holidays` for documents
	above :data:`BATCH_THRESHOLD`.

	Module-level and taking only a name, so nothing but a string crosses into
	the worker: the document is re-read there, after the save has committed. For
	a Single that name is the doctype name itself, which is exactly what
	``frappe.get_doc`` wants.
	"""
	frappe.get_doc("Holiday Assignment Tool", docname).create_assignments()


def cancel_assignments_in_background(docname: str):
	"""Queued by :meth:`HolidayAssignmentTool.undo_assignment` for documents
	above :data:`BATCH_THRESHOLD`."""
	frappe.get_doc("Holiday Assignment Tool", docname).cancel_assignments()


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _stored_assignment_names(row) -> list:
	"""The Holiday List Assignment names recorded on a row, tolerating a blank
	field and a hand-edited one (the field is read-only, not tamper-proof)."""
	raw = (row.assignments_json or "").strip()
	if not raw:
		return []
	try:
		names = json.loads(raw)
	except ValueError:
		frappe.log_error(
			f"Holiday Assignment Tool: unreadable assignments_json on row {row.name}.",
			reference_doctype="Holiday List Assignment",
		)
		return []
	if not isinstance(names, list):
		return []
	return [name for name in names if name]


def _cancel_assignments(names: list) -> int:
	"""Cancel the given Holiday List Assignments **latest boundary first**.

	Order matters. The boundaries form a chain in which each record runs until
	the next one starts, so cancelling the opener at ``from_date`` before the
	restore at ``to_date + 1`` would leave the employee sitting on the override
	list with nothing to terminate it — briefly, but observably, and permanently
	if the run then dies. Going backwards, every intermediate state is the
	document half-applied from the *start*, which is always coherent.

	The date is re-read from each record rather than trusted from the stored
	order, so a list written by an older run (or by hand) still unwinds in the
	right direction.
	"""
	entries = []
	for index, name in enumerate(names):
		values = frappe.db.get_value("Holiday List Assignment", name, ["from_date", "docstatus"])
		if not values:
			# Already deleted; nothing to unwind.
			continue
		from_date, docstatus = values
		entries.append((getdate(from_date), index, name, cint(docstatus)))

	# Descending by date; the stored position breaks ties so the order is total.
	entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)

	cancelled = 0
	for _date, _index, name, docstatus in entries:
		if docstatus == 2:
			continue

		assignment = frappe.get_doc("Holiday List Assignment", name)
		# Kept from the submittable version, where this tool was still submitted
		# while its on_cancel ran and the link check refused to let go of a
		# record it still referenced. Nothing links here now — the backlink is a
		# JSON blob in a Small Text field, not a Link — but the bypass costs
		# nothing and still covers a site that adds one. Same flag Bulk Week Off
		# sets.
		assignment.flags.ignore_links = True

		if assignment.docstatus == 1:
			assignment.cancel()
		else:
			assignment.delete(ignore_permissions=True, force=True)
		cancelled += 1

	return cancelled


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
