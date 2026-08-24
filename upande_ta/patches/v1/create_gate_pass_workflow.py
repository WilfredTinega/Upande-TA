# Copyright (c) 2026, Upande LTD and contributors
"""Create the "Gate Pass Approval" workflow.

The workflow *is* the signature chain that replaced the wet signatures on
HR000.00 -- each Workflow Action is an auditable, timestamped approval.

Existence-guarded: once the Workflow doc exists, later edits made in the Desk
UI survive `bench migrate` untouched.
"""

import frappe

WORKFLOW_NAME = "Gate Pass Approval"

# Only created if absent. "HOD" is included deliberately: it is NOT present on
# every target site despite being a familiar name.
ROLES = ("Supervisor", "HOD", "Gate Security")

# docstatus must be monotonically non-decreasing along every path
# (0 -> 0 -> 0 -> 0 -> 1). "Rejected" is docstatus 1, not 2 -- a draft cannot
# be cancelled, and 0 -> 2 raises "Illegal Document Status".
# `allow_edit` is mandatory on Workflow Document State. It is enforced
# client-side only (frappe/model/workflow.js) -- it greys the form out for
# anyone lacking the role, but does not block the server. The gate's "Record
# Return" button writes via frappe.client.set_value and so keeps working for
# HR regardless of the Approved row below.
STATES = (
	# (state, docstatus, style, allow_edit)
	("Draft", "0", "Danger", "Employee"),
	("Pending Supervisor", "0", "Warning", "Supervisor"),
	("Pending HOD", "0", "Warning", "HOD"),
	("Pending HR", "0", "Warning", "Group HR Manager"),
	# Post-approval the only editing left is the gate record.
	("Approved", "1", "Success", "Gate Security"),
	("Rejected", "1", "Danger", "Group HR Manager"),
)

ACTIONS = ("Submit for Approval", "Check", "Authorize", "Approve", "Reject")

TRANSITIONS = (
	# (from_state, action, to_state, allowed_role, allow_self_approval)
	("Draft", "Submit for Approval", "Pending Supervisor", "Employee", 1),
	("Pending Supervisor", "Check", "Pending HOD", "Supervisor", 0),
	("Pending Supervisor", "Reject", "Rejected", "Supervisor", 0),
	("Pending HOD", "Authorize", "Pending HR", "HOD", 0),
	("Pending HOD", "Reject", "Rejected", "HOD", 0),
	("Pending HR", "Approve", "Approved", "Group HR Manager", 0),
	("Pending HR", "Reject", "Rejected", "Group HR Manager", 0),
)


def execute():
	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		return

	ensure_roles()
	ensure_states()
	ensure_actions()
	create_workflow()
	frappe.db.commit()


def ensure_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def ensure_states():
	for state, _docstatus, style, _allow_edit in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)


def ensure_actions():
	for action in ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc(
				{"doctype": "Workflow Action Master", "workflow_action_name": action}
			).insert(ignore_permissions=True)


def create_workflow():
	workflow = frappe.new_doc("Workflow")
	workflow.workflow_name = WORKFLOW_NAME
	workflow.document_type = "Gate Pass"
	workflow.workflow_state_field = "workflow_state"
	workflow.is_active = 1
	workflow.send_email_alert = 1
	workflow.override_status = 0

	for state, docstatus, _style, allow_edit in STATES:
		workflow.append("states", {"state": state, "doc_status": docstatus, "allow_edit": allow_edit})

	for from_state, action, to_state, allowed, self_approval in TRANSITIONS:
		# No `condition` on any transition: conditions are evaluated while
		# Workflow Action records are generated in the background, where
		# frappe.session.user is the submitter rather than the approver -- a
		# session-user condition there silently suppresses approval emails.
		# Per-person enforcement lives in GatePass.validate_actor().
		workflow.append(
			"transitions",
			{
				"state": from_state,
				"action": action,
				"next_state": to_state,
				"allowed": allowed,
				"allow_self_approval": self_approval,
			},
		)

	workflow.insert(ignore_permissions=True)
