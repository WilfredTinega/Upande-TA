# Copyright (c) 2026, Upande LTD and contributors
"""Restrict amending of a *rejected* Leave Application to the exact user who
rejected it.

Flow enabled by this module together with the workflow "Cancel" transition on
the "Rejected" state:

    Rejected --(Cancel, only visible to the rejecter)--> cancelled --> Amend

The workflow transition condition (``doc.custom_rejected_by == frappe.session.user``)
hides the Cancel button from everyone except the rejecter. The server-side
hooks below are defence-in-depth so the rule holds even if the doc is cancelled
or amended through the API, the desk, or by a highly-privileged user
(System Manager / HR Manager). Only ``Administrator`` can bypass, as a
break-glass.
"""

import frappe
from frappe import _

REJECTED_STATE = "Rejected"
REJECTER_FIELD = "custom_rejected_by"
REJECTION_REASON_FIELD = "custom_rejection_reason"
# Only the literal Administrator account bypasses the rule. We deliberately do
# NOT bypass the "System Manager" role, because the managers who reject leaves
# also hold that role -- bypassing it would defeat the restriction.
BYPASS_USERS = {"Administrator"}


def ensure_rejected_by_field():
	"""Idempotently create the custom field that records the rejecter."""
	if not frappe.db.table_exists("Leave Application"):
		return

	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Leave Application": [
				{
					"fieldname": REJECTER_FIELD,
					"label": "Rejected By",
					"fieldtype": "Link",
					"options": "User",
					"insert_after": "workflow_state",
					"read_only": 1,
					"no_copy": 1,  # amended draft must start without a rejecter
					"allow_on_submit": 1,
					"print_hide": 1,
					"module": "Upande TA",
				}
			]
		},
		ignore_validate=True,
	)


def _is_bypass(user: str) -> bool:
	return user in BYPASS_USERS


@frappe.whitelist()
def set_rejection_reason(leave_application: str, reason: str):
	"""Persist the mandatory rejection reason just before the Reject workflow
	action runs.

	apply_workflow() reloads the document from the DB, so the reason has to be
	written to the DB first (setting it on the client form is not enough). We
	write it directly, which also means an approver who legitimately rejects but
	only holds a workflow (submit) permission -- not a generic write permission
	-- can still record the reason. A guard makes sure the caller actually has a
	Reject transition available on this document.
	"""
	from frappe.model.workflow import get_transitions

	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Rejection Reason is required."))

	doc = frappe.get_doc("Leave Application", leave_application)
	transitions = get_transitions(doc)
	if not any((t.action or "").lower() == "reject" for t in transitions):
		frappe.throw(
			_("You are not allowed to reject this leave application."),
			frappe.PermissionError,
		)

	frappe.db.set_value("Leave Application", leave_application, REJECTION_REASON_FIELD, reason)
	return True


# --------------------------------------------------------------------------- #
# Attendance that blocks approve / reject
# --------------------------------------------------------------------------- #
# HRMS' Leave Application.validate_attendance() throws AttendanceAlreadyMarkedError
# when submitted Present / Work From Home attendance exists inside the leave date
# range. That fires on every save/submit, so it blocks both Approve and Reject.
# These helpers let the UI detect and (on confirmation) cancel that attendance so
# the workflow action can continue.
ATTENDANCE_BLOCKING_STATUSES = ("Present", "Work From Home")


def _assert_can_action_leave(leave_application: str):
	"""Ensure the current user actually has a workflow transition available on
	this leave application before we let them mutate related records."""
	from frappe.model.workflow import get_transitions

	doc = frappe.get_doc("Leave Application", leave_application)
	if not get_transitions(doc):
		frappe.throw(
			_("You are not allowed to act on this leave application."),
			frappe.PermissionError,
		)
	return doc


@frappe.whitelist()
def get_blocking_attendance(leave_application: str):
	"""Return submitted Present/WFH attendance inside the leave range that would
	block approving or rejecting the application."""
	la = frappe.db.get_value(
		"Leave Application",
		leave_application,
		["employee", "from_date", "to_date"],
		as_dict=True,
	)
	if not la:
		return []

	return frappe.get_all(
		"Attendance",
		filters={
			"employee": la.employee,
			"attendance_date": ("between", [la.from_date, la.to_date]),
			"status": ("in", ATTENDANCE_BLOCKING_STATUSES),
			"docstatus": 1,
			"half_day_status": ("!=", "Absent"),
		},
		fields=["name", "attendance_date", "status"],
		order_by="attendance_date",
	)


@frappe.whitelist()
def cancel_blocking_attendance(leave_application: str):
	"""Cancel the Present/WFH attendance blocking this leave application, after the
	user has confirmed in the UI. Guarded so only an approver with a pending
	transition can trigger it."""
	_assert_can_action_leave(leave_application)

	cancelled = []
	for row in get_blocking_attendance(leave_application):
		att = frappe.get_doc("Attendance", row["name"])
		att.flags.ignore_permissions = True
		att.cancel()
		cancelled.append(row["name"])
	return cancelled


# --------------------------------------------------------------------------- #
# Capture the rejecter
# --------------------------------------------------------------------------- #
def capture_rejecter(doc, method=None):
	"""Stamp custom_rejected_by when the document moves into the Rejected state.

	The Reject transition takes the doc from an "Awaiting ..." draft straight to
	the submitted "Rejected" state, so this fires on before_submit. It also runs
	on on_update_after_submit as a safety net.
	"""
	if getattr(doc, "workflow_state", None) != REJECTED_STATE:
		return

	current = frappe.session.user
	if doc.get(REJECTER_FIELD) == current:
		return

	if method == "on_update_after_submit":
		# field is set/changed on an already submitted doc
		doc.db_set(REJECTER_FIELD, current, update_modified=False)
	else:
		doc.set(REJECTER_FIELD, current)


# --------------------------------------------------------------------------- #
# Enforce: only the rejecter may cancel a rejected leave
# --------------------------------------------------------------------------- #
def enforce_cancel_by_rejecter(doc, method=None):
	"""Block cancelling a *rejected* leave by anyone other than the rejecter.

	At before_cancel time the workflow has already flipped the in-memory state to
	"cancelled", so we read the persisted state from the DB to know whether this
	cancellation originates from the Rejected state. Cancelling an *Approved*
	leave (the pre-existing HR flow) is untouched.
	"""
	user = frappe.session.user
	if _is_bypass(user):
		return

	prev_state = frappe.db.get_value(doc.doctype, doc.name, "workflow_state")
	if prev_state != REJECTED_STATE:
		return

	rejecter = doc.get(REJECTER_FIELD) or frappe.db.get_value(doc.doctype, doc.name, REJECTER_FIELD)
	if rejecter and user != rejecter:
		frappe.throw(
			_("Only {0}, who rejected this leave application, can cancel it.").format(
				frappe.bold(rejecter)
			),
			frappe.PermissionError,
		)


# --------------------------------------------------------------------------- #
# Enforce: only the rejecter may amend a rejected leave
# --------------------------------------------------------------------------- #
def enforce_amend_by_rejecter(doc, method=None):
	"""On a new amended doc, block the amend unless the current user rejected the
	source document. Amendments of non-rejected (e.g. Approved) leaves are
	unaffected, because their source has no custom_rejected_by value."""
	if not doc.get("amended_from"):
		return

	user = frappe.session.user
	if _is_bypass(user):
		return

	source_rejecter = frappe.db.get_value(doc.doctype, doc.amended_from, REJECTER_FIELD)
	if source_rejecter and user != source_rejecter:
		frappe.throw(
			_("Only {0}, who rejected the original leave application, can amend it.").format(
				frappe.bold(source_rejecter)
			),
			frappe.PermissionError,
		)
