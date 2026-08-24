# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import (
	add_days,
	get_datetime,
	get_fullname,
	getdate,
	now_datetime,
	time_diff_in_hours,
	today,
)
from frappe.utils.user import get_users_with_role

# Roles that may action any stage on anyone's behalf, and that bypass the
# per-document approver check in validate_actor().
APPROVAL_ROLES = {"HR Manager", "Group HR Manager", "System Manager"}

# Roles that see every Gate Pass in the list view, and that may backdate.
HR_ROLES = {"System Manager", "HR Manager", "Group HR Manager", "HR User"}

# Roles that see every Gate Pass but are not HR (the gate needs the full list).
GATE_ROLES = {"Gate Security"}

# The workflow terminates on both of these, and both carry docstatus 1 --
# "Rejected" is a submitted-but-refused pass, not a cancelled one.
FINAL_STATES = {"Approved", "Rejected"}

# Parentfield of the Department custom field added by
# patches/v1/create_gate_pass_department_field.py.
DEPARTMENT_APPROVER_FIELD = "custom_gate_pass_approvers"

# How far back a non-HR user may date a pass.
MAX_BACKDATE_DAYS = 7


class GatePass(Document):
	def validate(self):
		self.set_approvers()
		self.validate_employee_active()
		self.validate_times()
		self.validate_self_approval()
		self.validate_not_on_leave()
		self.validate_overlap()
		self.calculate_hours()
		self.validate_actor()

	def before_submit(self):
		# The workflow drives submission: both "Approved" and "Rejected" are
		# docstatus 1, so a rejected pass submits too. Anything else means
		# somebody bypassed the approval chain.
		if self.workflow_state not in FINAL_STATES:
			frappe.throw(
				_("Gate Pass can only be submitted once the approval workflow is complete."),
				title=_("Not Approved"),
			)

		if self.workflow_state == "Approved":
			self.hr_approver = self.hr_approver or frappe.session.user
			self.hr_action_on = self.hr_action_on or now_datetime()

	def on_submit(self):
		if self.workflow_state == "Approved":
			self.notify_gate()

	def before_update_after_submit(self):
		# The gate officer fills actual_time_out / actual_time_in after the
		# fact; recompute so returned + actual_hours_out stay in step. This has
		# to be `before_` -- `on_update_after_submit` runs after the row is
		# written, so anything set there is silently discarded. `validate` does
		# not run on an update-after-submit either.
		self.calculate_hours()
		if (self.actual_time_out or self.actual_time_in) and not self.gate_officer:
			self.gate_officer = frappe.session.user

	# ------------------------------------------------------------------
	# approver routing
	# ------------------------------------------------------------------

	def set_approvers(self):
		if not self.supervisor:
			reports_to = frappe.db.get_value("Employee", self.employee, "reports_to")
			if reports_to:
				self.supervisor = frappe.db.get_value("Employee", reports_to, "user_id")

		if not self.hod and self.department:
			approvers = frappe.get_all(
				"Department Approver",
				filters={
					"parent": self.department,
					"parenttype": "Department",
					"parentfield": DEPARTMENT_APPROVER_FIELD,
				},
				pluck="approver",
				order_by="idx asc",
			)
			if approvers:
				self.hod = approvers[0]

		# Say exactly what is missing -- these are setup problems, and a bare
		# "Supervisor is required" sends the user hunting.
		if not self.supervisor:
			employee = frappe.utils.get_link_to_form("Employee", self.employee)
			msg = _('No Immediate Supervisor found. Set "Reports To" on Employee {0}, or pick one manually.')
			frappe.throw(msg.format(employee), title=_("Supervisor Not Set"))

		if not self.hod:
			department = (
				frappe.utils.get_link_to_form("Department", self.department)
				if self.department
				else _("(not set)")
			)
			msg = _("No Head of Department found. Add a Gate Pass Approver on Department {0}.")
			frappe.throw(msg.format(department), title=_("HOD Not Set"))

	# ------------------------------------------------------------------
	# validations
	# ------------------------------------------------------------------

	def validate_employee_active(self):
		status = frappe.db.get_value("Employee", self.employee, "status")
		if status and status != "Active":
			frappe.throw(
				_("Employee {0} is {1}. A Gate Pass can only be raised for an Active employee.").format(
					frappe.bold(self.employee_name or self.employee), frappe.bold(status)
				)
			)

	def validate_times(self):
		if not self.time_out:
			frappe.throw(_("Time Out is required."))

		if self.returning_same_day:
			if self.expected_time_in and get_datetime(f"{self.date} {self.expected_time_in}") <= get_datetime(
				f"{self.date} {self.time_out}"
			):
				frappe.throw(_("Time Back In must be later than Time Out."))
		else:
			self.expected_time_in = None

		self.validate_backdating()
		self.warn_if_holiday()

	def validate_backdating(self):
		if not self.date:
			return

		cutoff = add_days(getdate(today()), -MAX_BACKDATE_DAYS)
		if getdate(self.date) < cutoff and not (set(frappe.get_roles()) & HR_ROLES):
			msg = _("A Gate Pass cannot be dated more than {0} days in the past. Ask HR to backdate it.")
			frappe.throw(msg.format(MAX_BACKDATE_DAYS), title=_("Date Too Far Back"))

	def warn_if_holiday(self):
		"""Flag a pass raised on a non-working day, but do not block it."""
		if not (self.date and self.employee):
			return

		try:
			from hrms.utils.holiday_list import get_holiday_list_for_employee
		except ImportError:
			return

		holiday_list = get_holiday_list_for_employee(self.employee, raise_exception=False)
		if not holiday_list:
			return

		if frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": self.date}):
			frappe.msgprint(
				_("{0} is a holiday for {1}. Raising a Gate Pass anyway.").format(
					frappe.bold(frappe.utils.formatdate(self.date)),
					frappe.bold(self.employee_name or self.employee),
				),
				indicator="orange",
				alert=True,
			)

	def validate_self_approval(self):
		"""An employee may not sign off their own pass at either stage."""
		user_id = frappe.db.get_value("Employee", self.employee, "user_id")
		if not user_id:
			return

		if user_id == self.supervisor:
			frappe.throw(
				_("{0} cannot be their own Supervisor. Route the pass to the next level up.").format(
					frappe.bold(self.employee_name or self.employee)
				),
				title=_("Self Approval"),
			)

		if user_id == self.hod:
			frappe.throw(
				_("{0} cannot be their own Head of Department. Route the pass to the next level up.").format(
					frappe.bold(self.employee_name or self.employee)
				),
				title=_("Self Approval"),
			)

	def validate_not_on_leave(self):
		if not (self.employee and self.date):
			return

		leave = frappe.db.get_value(
			"Leave Application",
			{
				"employee": self.employee,
				"docstatus": 1,
				"status": "Approved",
				"from_date": ["<=", self.date],
				"to_date": [">=", self.date],
			},
			["name", "leave_type"],
			as_dict=True,
		)
		if leave:
			frappe.throw(
				_("{0} is already on approved leave ({1}) on {2} — see {3}.").format(
					frappe.bold(self.employee_name or self.employee),
					leave.leave_type,
					frappe.utils.formatdate(self.date),
					frappe.utils.get_link_to_form("Leave Application", leave.name),
				),
				title=_("Employee On Leave"),
			)

	def validate_overlap(self):
		if not (self.employee and self.date and self.time_out):
			return

		others = frappe.get_all(
			"Gate Pass",
			filters={
				"name": ["!=", self.name],
				"employee": self.employee,
				"date": self.date,
				"docstatus": ["<", 2],
				"workflow_state": ["!=", "Rejected"],
			},
			fields=["name", "time_out", "expected_time_in"],
		)

		# A blank expected_time_in means "out for the rest of the day".
		end_of_day = "23:59:59"
		this_start = get_datetime(f"{self.date} {self.time_out}")
		this_end = get_datetime(f"{self.date} {self.expected_time_in or end_of_day}")

		for other in others:
			other_start = get_datetime(f"{self.date} {other.time_out}")
			other_end = get_datetime(f"{self.date} {other.expected_time_in or end_of_day}")

			if this_start < other_end and other_start < this_end:
				frappe.throw(
					_("This overlaps an existing Gate Pass for {0} on {1}: {2}.").format(
						frappe.bold(self.employee_name or self.employee),
						frappe.utils.formatdate(self.date),
						frappe.utils.get_link_to_form("Gate Pass", other.name),
					),
					title=_("Overlapping Gate Pass"),
				)

	def calculate_hours(self):
		if self.time_out and self.expected_time_in:
			self.expected_hours_out = time_diff_in_hours(
				get_datetime(f"{self.date} {self.expected_time_in}"),
				get_datetime(f"{self.date} {self.time_out}"),
			)
		else:
			self.expected_hours_out = 0

		if self.actual_time_out and self.actual_time_in:
			self.actual_hours_out = time_diff_in_hours(
				get_datetime(f"{self.date} {self.actual_time_in}"),
				get_datetime(f"{self.date} {self.actual_time_out}"),
			)
			self.returned = 1
		else:
			self.actual_hours_out = 0
			self.returned = 0

	def validate_actor(self):
		"""Enforce the *named* approver on a workflow state change.

		Workflow transitions are role-gated so that Workflow Action records
		(and their emails) generate for everyone holding the role. This narrows
		that to the specific supervisor / HOD on this document, and stamps the
		approval timestamp that stands in for a wet signature.
		"""
		before = self.get_doc_before_save()
		if not before or before.workflow_state == self.workflow_state:
			return

		if frappe.session.user == "Administrator" or (set(frappe.get_roles()) & APPROVAL_ROLES):
			self.stamp_action(before.workflow_state)
			return

		expected = None
		if before.workflow_state == "Pending Supervisor":
			expected = self.supervisor
		elif before.workflow_state == "Pending HOD":
			expected = self.hod

		if expected and frappe.session.user != expected:
			frappe.throw(
				_("Only {0} can action this Gate Pass at the {1} stage.").format(
					get_fullname(expected), before.workflow_state
				),
				title=_("Not Permitted"),
			)

		self.stamp_action(before.workflow_state)

	def stamp_action(self, previous_state):
		if previous_state == "Pending Supervisor":
			self.supervisor_action_on = now_datetime()
		elif previous_state == "Pending HOD":
			self.hod_action_on = now_datetime()
		elif previous_state == "Pending HR":
			self.hr_approver = self.hr_approver or frappe.session.user
			self.hr_action_on = now_datetime()

	# ------------------------------------------------------------------
	# notification
	# ------------------------------------------------------------------

	def notify_gate(self):
		"""Tell the gate an approved pass exists, via Notification Log.

		Deliberately driven off the Gate Security role rather than a hardcoded
		address list, so staffing changes need no code change.
		"""
		recipients = get_users_with_role("Gate Security")
		if not recipients:
			return

		subject = _("Gate Pass {0} approved for {1}").format(self.name, self.employee_name or self.employee)
		message = _("{0} ({1}) is authorised to leave at {2} on {3}. Reason: {4}").format(
			frappe.bold(self.employee_name or self.employee),
			self.payroll_no or self.employee,
			frappe.utils.format_time(self.time_out) if self.time_out else "",
			frappe.utils.formatdate(self.date),
			self.reason or "",
		)

		for user in recipients:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": message,
					"type": "Alert",
					"document_type": self.doctype,
					"document_name": self.name,
					"for_user": user,
					"from_user": frappe.session.user,
				}
			).insert(ignore_permissions=True)


# ----------------------------------------------------------------------
# module-level helpers
# ----------------------------------------------------------------------


def has_approved_gate_pass(employee, date, at_time=None):
	"""Return the name of an approved Gate Pass covering this employee/date, else None.

	Exported for the missing-clock-out attendance rule: an employee who was out
	on an authorised pass should not be penalised for the missing punch.

	:param at_time: optional "HH:MM:SS" — narrow the match to passes whose
	        window covers that time. A blank expected_time_in counts as
	        "out for the rest of the day".
	"""
	if not (employee and date):
		return None

	filters = {"employee": employee, "date": getdate(date), "docstatus": 1, "workflow_state": "Approved"}

	if not at_time:
		return frappe.db.get_value("Gate Pass", filters, "name")

	passes = frappe.get_all(
		"Gate Pass",
		filters=filters,
		fields=["name", "time_out", "expected_time_in"],
		order_by="time_out asc",
	)
	for gp in passes:
		if gp.time_out and str(gp.time_out) <= str(at_time):
			if not gp.expected_time_in or str(gp.expected_time_in) >= str(at_time):
				return gp.name

	return None


@frappe.whitelist()
def get_approvers(employee):
	"""Resolve supervisor + HOD for an Employee. Used by gate_pass.js."""
	if not employee:
		return {}

	emp = frappe.db.get_value("Employee", employee, ["reports_to", "department"], as_dict=True)
	if not emp:
		return {}

	supervisor = frappe.db.get_value("Employee", emp.reports_to, "user_id") if emp.reports_to else None

	hod = None
	if emp.department:
		approvers = frappe.get_all(
			"Department Approver",
			filters={
				"parent": emp.department,
				"parenttype": "Department",
				"parentfield": DEPARTMENT_APPROVER_FIELD,
			},
			pluck="approver",
			order_by="idx asc",
		)
		if approvers:
			hod = approvers[0]

	return {"supervisor": supervisor, "hod": hod}


def get_permission_query_conditions(user=None):
	"""Employees see their own passes plus the ones they approve."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	roles = set(frappe.get_roles(user))
	if roles & (HR_ROLES | GATE_ROLES):
		return ""

	escaped = frappe.db.escape(user)
	conditions = [
		f"`tabGate Pass`.owner = {escaped}",
		f"`tabGate Pass`.supervisor = {escaped}",
		f"`tabGate Pass`.hod = {escaped}",
	]

	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if employee:
		conditions.append(f"`tabGate Pass`.employee = {frappe.db.escape(employee)}")

	return "({0})".format(" or ".join(conditions))


def has_permission(doc, ptype=None, user=None):
	"""Document-level mirror of get_permission_query_conditions."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	roles = set(frappe.get_roles(user))
	if roles & (HR_ROLES | GATE_ROLES):
		return True

	if user in (doc.owner, doc.supervisor, doc.hod):
		return True

	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	return bool(employee and employee == doc.employee)
