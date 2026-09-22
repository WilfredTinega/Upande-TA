# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""The approval chain for Overtime Request and Bulk Overtime.

Everything about the workflow — how many approval stages there are, who acts
at each one, what colour it wears in the desk and who may still edit there —
comes from :data:`DRAFT`, :data:`APPROVALS` and the closing states below.
**Change those and nothing else.** The states, the transitions, the action
buttons, the roles and the role profiles are all built from them.

To add a stage, put another :class:`Stage` in :data:`APPROVALS`; the chain is
rewired in order, each stage handing on to the next and the last one to
Approved. To change who approves, change the ``role``. Then re-apply it::

    bench --site <site> execute upande_ta.upande_ta.overtime_workflow.rebuild

``rebuild`` overwrites the states and transitions of both workflows in place.
Plain ``setup()`` — what the patch runs on migrate — creates a workflow only if
it is missing, so edits made in the Desk UI survive.

Two rules hold the design together, and both are about docstatus:

* **Rejected is docstatus 0, not 1.** Bulk Overtime pays from
  ``where docstatus = 1``, so a rejected Overtime Request that submitted itself
  would be paid exactly like an approved one, and a rejected batch would fire
  ``on_submit`` and cut the Overtime Slips it was just refused. A draft cannot
  go straight to 2 either, so 0 is what a rejection is: back with the
  requester, with the reason on record.

* **Approved is the only docstatus 1 state, and comes first among them.**
  ``set_workflow_state_on_action`` maps a plain ``doc.submit()`` onto the first
  state whose ``doc_status`` is 1, which is how the desk's Submit button and
  existing code keep a coherent state.
"""

from dataclasses import dataclass, field

import frappe

#: Colours the desk will render a state pill in.
COLOURS = ("Primary", "Info", "Success", "Warning", "Danger", "Inverse")


@dataclass(frozen=True)
class Stage:
	"""One step in the chain, and the person who acts on it."""

	#: The Workflow State the document waits in.
	state: str
	#: The Role that may act here. Created if the site does not have it.
	role: str
	#: The button that moves it on from here.
	action: str = "Approve"
	#: The pill colour in the desk — one of :data:`COLOURS`.
	colour: str = "Warning"
	#: Role Profiles this role should belong to. Each is created if missing and
	#: the role added to it, so a new approver is granted by profile alone.
	role_profiles: tuple = field(default_factory=tuple)
	#: Who may still edit the document here. Defaults to the acting role.
	allow_edit: str = ""
	#: 0 draft, 1 submitted, 2 cancelled.
	doc_status: str = "0"

	@property
	def editor(self) -> str:
		return self.allow_edit or self.role


# ──────────────────────────────────────────────────────────────────────────
# The chain. This is the part to edit.
# ──────────────────────────────────────────────────────────────────────────

#: Who prepares the document, and the button that sends it for approval.
DRAFT = Stage(
	state="Draft",
	role="Farm Manager",
	action="Submit for Approval",
	colour="Danger",
	role_profiles=(),
)

#: The approval stages, in order. One entry per signature the document needs:
#: each hands on to the next, and the last one to Approved.
APPROVALS = (
	Stage(
		state="Pending Approval",
		role="HR Manager Kaitet",
		action="Approve",
		colour="Warning",
		role_profiles=(),
	),
)

#: Approved is the approval: docstatus 1 is what Bulk Overtime pays from.
APPROVED = Stage(state="Approved", role="HR Manager Kaitet", colour="Success", doc_status="1")

#: A rejection stays a draft — see the module docstring.
REJECTED = Stage(state="Rejected", role="Farm Manager", colour="Danger", doc_status="0")

#: So the Cancel button leaves a truthful label rather than "Approved".
CANCELLED = Stage(state="Cancelled", role="HR Manager Kaitet", colour="Inverse", doc_status="2")

REJECT_ACTION = "Reject"
REOPEN_ACTION = "Reopen"

#: allow_self_approval throughout: the approver already holds submit permission
#: on both doctypes, so demanding a second person would not add a privilege —
#: it would deadlock any site where one HR Manager both raises and clears
#: overtime, which is the common case on a single farm. Who approved is still
#: recorded on the Workflow Action either way.
ALLOW_SELF_APPROVAL = 1

WORKFLOWS = (
	("Overtime Request Approval", "Overtime Request"),
	("Bulk Overtime Approval", "Bulk Overtime"),
)


# ──────────────────────────────────────────────────────────────────────────
# Built from the chain above
# ──────────────────────────────────────────────────────────────────────────


def states() -> tuple:
	"""Every state, Approved first among the submitted ones."""
	return (DRAFT, *APPROVALS, APPROVED, REJECTED, CANCELLED)


def transitions() -> list:
	"""(from_state, action, to_state, allowed_role) along the whole chain."""
	chain = []
	first = APPROVALS[0].state if APPROVALS else APPROVED.state
	chain.append((DRAFT.state, DRAFT.action, first, DRAFT.role))

	for index, stage in enumerate(APPROVALS):
		onward = APPROVALS[index + 1].state if index + 1 < len(APPROVALS) else APPROVED.state
		chain.append((stage.state, stage.action, onward, stage.role))
		chain.append((stage.state, REJECT_ACTION, REJECTED.state, stage.role))

	chain.append((REJECTED.state, REOPEN_ACTION, DRAFT.state, DRAFT.role))
	return chain


def actions() -> list:
	return sorted({action for _from, action, _to, _role in transitions()})


# ──────────────────────────────────────────────────────────────────────────
# Applying it
# ──────────────────────────────────────────────────────────────────────────


def setup(force: bool = False):
	"""Create the workflows. With ``force``, overwrite the ones already there.

	Without it an existing Workflow is left alone, so whatever was tuned in the
	Desk UI survives ``bench migrate``.
	"""
	if not any(frappe.db.exists("DocType", doctype) for _name, doctype in WORKFLOWS):
		return

	ensure_roles()
	ensure_states()
	ensure_actions()

	for workflow_name, doctype in WORKFLOWS:
		if not frappe.db.exists("DocType", doctype):
			continue
		if frappe.db.exists("Workflow", workflow_name):
			if not force:
				continue
			write_workflow(frappe.get_doc("Workflow", workflow_name), doctype)
		else:
			write_workflow(frappe.new_doc("Workflow"), doctype, workflow_name)

	frappe.db.commit()


def rebuild():
	"""Re-apply the chain over the workflows already on the site.

	Run after changing the roles or the stages above::

	    bench --site <site> execute upande_ta.upande_ta.overtime_workflow.rebuild
	"""
	setup(force=True)


def ensure_roles():
	"""Every role the chain names, and the Role Profiles it should sit in."""
	for stage in states():
		for role in {stage.role, stage.editor}:
			if role and not frappe.db.exists("Role", role):
				frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
					ignore_permissions=True
				)
		for profile_name in stage.role_profiles:
			add_role_to_profile(stage.role, profile_name)


def add_role_to_profile(role: str, profile_name: str):
	"""So granting the profile is enough to make someone an approver."""
	if not (role and profile_name):
		return
	if frappe.db.exists("Role Profile", profile_name):
		profile = frappe.get_doc("Role Profile", profile_name)
	else:
		profile = frappe.get_doc({"doctype": "Role Profile", "role_profile": profile_name})

	if any(row.role == role for row in profile.get("roles", [])):
		return
	profile.append("roles", {"role": role})
	profile.save(ignore_permissions=True)


def ensure_states():
	"""The Workflow State records, with their colour.

	Workflow State is global — "Draft" and "Approved" are shared with every
	other workflow on the site — so a colour is written only where there is
	none, never over one somebody already chose.
	"""
	for stage in states():
		if not frappe.db.exists("Workflow State", stage.state):
			frappe.get_doc(
				{
					"doctype": "Workflow State",
					"workflow_state_name": stage.state,
					"style": stage.colour,
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.get_value("Workflow State", stage.state, "style"):
			frappe.db.set_value("Workflow State", stage.state, "style", stage.colour)


def ensure_actions():
	for action in actions():
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def write_workflow(workflow, doctype: str, workflow_name: str | None = None):
	workflow.workflow_name = workflow_name or workflow.workflow_name
	workflow.document_type = doctype
	workflow.workflow_state_field = "workflow_state"
	workflow.is_active = 1
	workflow.send_email_alert = 1
	workflow.override_status = 0

	workflow.set("states", [])
	for stage in states():
		workflow.append(
			"states",
			{"state": stage.state, "doc_status": stage.doc_status, "allow_edit": stage.editor},
		)

	workflow.set("transitions", [])
	for from_state, action, to_state, allowed in transitions():
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
				"allow_self_approval": ALLOW_SELF_APPROVAL,
			},
		)

	workflow.save(ignore_permissions=True) if not workflow.is_new() else workflow.insert(
		ignore_permissions=True
	)
	return workflow
