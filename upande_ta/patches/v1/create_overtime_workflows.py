# Copyright (c) 2026, Upande LTD and contributors
"""Create the "Overtime Request Approval" and "Bulk Overtime Approval" workflows.

Both halves of the overtime chain now carry an approval step, and each Workflow
Action is an auditable, timestamped record of who approved what:

    Overtime Request (approved)  ->  Bulk Overtime  ->  Overtime Slip

**Rejected is docstatus 0, not 1.** This is the one thing to keep in mind when
editing these states. Bulk Overtime pays from ``where req.docstatus = 1``, so a
rejected Overtime Request sitting at docstatus 1 would be paid exactly like an
approved one; and a rejected Bulk Overtime at docstatus 1 would fire
``on_submit`` and cut the Overtime Slips and Additional Salary it was just
refused. A draft cannot go straight to 2 either ("Illegal Document Status"), so
0 is what a rejection is: back with the requester, with the reason on record.

For the same reason **Approved is the only docstatus 1 state, and comes first**
among them: ``set_workflow_state_on_action`` maps a plain ``doc.submit()`` onto
the first state whose ``doc_status`` is 1, which is how the desk's own Submit
button and existing code keep a coherent state.

Existence-guarded per workflow: once a Workflow doc exists, later edits made in
the Desk UI survive `bench migrate` untouched.
"""

import frappe

#: HR User prepares, HR Manager approves — the split the doctype permissions
#: already make (HR User may write but not submit), now with a pending state
#: and a record of who cleared it. Both ship with HRMS, so nothing is invented.
PREPARER = "HR User"
APPROVER = "HR Manager"

# (state, docstatus, style, allow_edit)
STATES = (
	("Draft", "0", "Danger", PREPARER),
	("Pending Approval", "0", "Warning", APPROVER),
	# the only docstatus 1 state, and the first of them: see the module docstring
	("Approved", "1", "Success", APPROVER),
	# docstatus 0 deliberately — a rejection must never look like an approval
	("Rejected", "0", "Danger", PREPARER),
	# so the Cancel button leaves a truthful label rather than "Approved"
	("Cancelled", "2", "Danger", APPROVER),
)

ACTIONS = ("Submit for Approval", "Approve", "Reject", "Reopen")

# (from_state, action, to_state, allowed_role, allow_self_approval)
#
# allow_self_approval is 1 throughout: the approver already holds submit
# permission on both doctypes, so demanding a second person would not add a
# privilege — it would simply deadlock any site where one HR Manager both
# raises and clears overtime, which is the common case on a single farm. Who
# approved is still recorded on the Workflow Action either way.
TRANSITIONS = (
	("Draft", "Submit for Approval", "Pending Approval", PREPARER, 1),
	("Pending Approval", "Approve", "Approved", APPROVER, 1),
	("Pending Approval", "Reject", "Rejected", APPROVER, 1),
	("Rejected", "Reopen", "Draft", PREPARER, 1),
)

WORKFLOWS = (
	("Overtime Request Approval", "Overtime Request"),
	("Bulk Overtime Approval", "Bulk Overtime"),
)


def execute():
	if not any(frappe.db.exists("DocType", doctype) for _name, doctype in WORKFLOWS):
		return

	ensure_states()
	ensure_actions()

	for workflow_name, doctype in WORKFLOWS:
		if frappe.db.exists("Workflow", workflow_name):
			continue
		if not frappe.db.exists("DocType", doctype):
			continue
		create_workflow(workflow_name, doctype)

	frappe.db.commit()


def ensure_states():
	for state, _docstatus, style, _allow_edit in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)


def ensure_actions():
	for action in ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def create_workflow(workflow_name: str, doctype: str):
	workflow = frappe.new_doc("Workflow")
	workflow.workflow_name = workflow_name
	workflow.document_type = doctype
	workflow.workflow_state_field = "workflow_state"
	workflow.is_active = 1
	workflow.send_email_alert = 1
	workflow.override_status = 0

	for state, docstatus, _style, allow_edit in STATES:
		workflow.append("states", {"state": state, "doc_status": docstatus, "allow_edit": allow_edit})

	for from_state, action, to_state, allowed, self_approval in TRANSITIONS:
		# No `condition` on any transition: conditions are evaluated while
		# Workflow Action records are generated in the background, where
		# frappe.session.user is the submitter rather than the approver — a
		# session-user condition there silently suppresses approval emails.
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
