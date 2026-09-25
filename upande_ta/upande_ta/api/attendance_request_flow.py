# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Mark Attendance on the Attendance Insights page, through approval.

The page no longer writes Attendance. It raises one HRMS **Attendance
Request** per employee and run of consecutive dates. When Biometric Setting
names an approver for the employee's Company and Unit/Division, the request is
sent straight to Pending Approval and addressed to them. That approver alone
can approve it: the request carries their user in ``custom_approver``, is
shared with them to read, edit and submit, and the site's workflow gates
Approve on ``doc.custom_approver == frappe.session.user``. When nobody is
named, the request needs no approval and is submitted at once. Either way,
submitting is what makes HRMS mark the employee Present for its dates.

Requests raised in the Desk get their approver the same way, on insert.

The workflow itself is site configuration, not shipped here.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_link_to_form, getdate, today

SETTING_DOCTYPE = "Biometric Setting"
SUBMIT_ACTION = "Submit for Approval"
APPROVE_ACTION = "Approve"
PENDING_STATE = "Pending Approval"
#: HRMS' reason that marks Present (the other, Work From Home, marks WFH).
REQUEST_REASON = "On Duty"
VALID_MARKING_REASONS = ("Away Assignment", "Pending Off", "Pending Holiday")
MAX_EMPLOYEES = 500
MAX_DATES = 62


def approvers() -> dict:
	"""``{(company, Unit/Division): {user, employee, name}}`` from Biometric
	Setting; a blank Unit/Division is the company-wide row."""
	if not frappe.db.exists("DocType", "Attendance Request Approver"):
		return {}
	rows = frappe.get_all(
		"Attendance Request Approver",
		filters={"parent": SETTING_DOCTYPE, "parenttype": SETTING_DOCTYPE},
		fields=["company", "farm", "approver", "approver_name", "approver_user"],
	)
	return {
		(r.company, (r.farm or "").strip()): frappe._dict(
			user=r.approver_user, employee=r.approver, name=r.approver_name
		)
		for r in rows
		if r.company and r.approver_user
	}


def approver_for(company, farm, table=None):
	"""The approver for this Company and Unit/Division, else the company-wide
	one, else ``None`` — no approval needed."""
	table = approvers() if table is None else table
	return table.get((company, (farm or "").strip())) or table.get((company, ""))


def set_approver(doc, method=None):
	"""Attendance Request before_insert: name the approver, whoever raised
	it, so a Desk request is routed like one from Attendance Insights."""
	if doc.get("custom_approver") or not frappe.db.has_column("Attendance Request", "custom_approver"):
		return
	farm = (
		frappe.db.get_value("Employee", doc.employee, "custom_farm")
		if frappe.db.has_column("Employee", "custom_farm")
		else None
	)
	approver = approver_for(doc.company, farm)
	if approver:
		doc.custom_approver = approver.user
		doc.custom_approver_name = approver.name


@frappe.whitelist(methods=["POST"])
def raise_requests():
	"""Raise Attendance Requests for the ticked employees and dates.

	Form fields, as the page sends them: ``emp_ids`` (comma separated),
	``att_date``, ``att_dates`` (comma separated, optional — wins over
	``att_date``), ``reason`` (a Marking Reason, optional), ``allow_off`` ("1"
	to include rest days).
	"""
	from upande_ta.upande_ta.api.attendance_insights import get_allowed_companies

	form = frappe.form_dict
	emp_ids = [e.strip() for e in (form.get("emp_ids") or "").split(",") if e.strip()][:MAX_EMPLOYEES]
	dates = sorted(
		{getdate(d.strip()) for d in (form.get("att_dates") or "").split(",") if d.strip()}
		or {getdate(form.get("att_date") or today())}
	)[:MAX_DATES]
	reason = (form.get("reason") or "").strip()
	if reason not in VALID_MARKING_REASONS:
		reason = ""
	allow_off = cint(form.get("allow_off"))

	if not emp_ids:
		return {"error": _("No employees selected.")}
	future = [d for d in dates if d > getdate(today())]
	if future:
		return {"error": _("Attendance cannot be requested for a future date: {0}").format(future[0])}

	allowed = get_allowed_companies()
	employees = {
		e.name: e
		for e in frappe.get_all(
			"Employee",
			filters={"name": ["in", emp_ids]},
			fields=["name", "employee_name", "company", "custom_farm"]
			if frappe.db.has_column("Employee", "custom_farm")
			else ["name", "employee_name", "company"],
		)
	}
	table = approvers()
	workflow = _active_workflow()

	results = []
	for emp_id in emp_ids:
		employee = employees.get(emp_id)
		if not employee:
			results.append({"employee": emp_id, "ok": False, "error": _("Employee not found")})
			continue
		if allowed is not None and employee.company not in allowed:
			results.append(
				{"employee": emp_id, "ok": False, "error": _("You are not permitted to mark this employee")}
			)
			continue
		approver = approver_for(employee.company, employee.get("custom_farm"), table)

		for start, end in _runs(dates):
			results.append(_raise_one(employee, start, end, reason, allow_off, approver, workflow))

	ok = [r for r in results if r.get("ok")]
	return {
		"results": results,
		"ok_count": len(ok),
		"err_count": len(results) - len(ok),
		"workflow": workflow,
	}


def _raise_one(employee, start, end, reason, allow_off, approver, workflow) -> dict:
	from frappe.model.workflow import apply_workflow

	entry = {
		"employee": employee.name,
		"employee_name": employee.employee_name,
		"from_date": str(start),
		"to_date": str(end),
		"approver": (approver.name or approver.user) if approver else None,
	}
	try:
		frappe.db.savepoint("attendance_request_raise")
		doc = frappe.get_doc(
			{
				"doctype": "Attendance Request",
				"employee": employee.name,
				"company": employee.company,
				"from_date": start,
				"to_date": end,
				"reason": REQUEST_REASON,
				"include_holidays": allow_off,
				"explanation": reason or _("Marked from Attendance Insights"),
				"custom_marking_reason": reason or None,
				"custom_approver": approver.user if approver else None,
				"custom_approver_name": approver.name if approver else None,
			}
		)
		doc.insert()
		if approver:
			frappe.share.add_docshare(
				"Attendance Request",
				doc.name,
				approver.user,
				read=1,
				write=1,
				submit=1,
				flags={"ignore_share_permission": True},
				notify=0,
			)
			if workflow:
				doc = apply_workflow(doc, SUBMIT_ACTION)
			_notify(approver.user, doc)
		elif workflow:
			# nobody to approve it, so it is approved as it is raised
			doc = apply_workflow(doc, APPROVE_ACTION)
		else:
			doc.submit()
		frappe.clear_messages()
		entry.update(
			{
				"ok": True,
				"name": doc.name,
				"state": doc.get("workflow_state") or (_("Submitted") if doc.docstatus == 1 else _("Draft")),
				"submitted": doc.docstatus == 1,
			}
		)
	except Exception as e:
		frappe.db.rollback(save_point="attendance_request_raise")
		frappe.clear_messages()
		entry.update({"ok": False, "error": frappe.utils.strip_html(str(e))[:300]})
	return entry


def _runs(dates) -> list:
	"""Consecutive dates as ``(first, last)`` pairs: an Attendance Request is
	one range, so a picked 19th, 20th and 23rd is two requests."""
	runs = []
	for date in dates:
		if runs and runs[-1][1] == add_days(date, -1):
			runs[-1][1] = date
		else:
			runs.append([date, date])
	return [(a, b) for a, b in runs]


def _active_workflow():
	"""The active Attendance Request workflow's name, else ``None`` — a
	request is then submitted as it is raised."""
	return frappe.db.get_value("Workflow", {"document_type": "Attendance Request", "is_active": 1}, "name")


def _notify(user, doc):
	"""An in-app notification for the approver."""
	try:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": "Attendance Request",
				"document_name": doc.name,
				"subject": _("Attendance Request {0} for {1} ({2} to {3}) is waiting for your approval").format(
					doc.name, doc.employee_name or doc.employee, doc.from_date, doc.to_date
				),
				"from_user": frappe.session.user,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Attendance Request: approver notification failed")


@frappe.whitelist()
def pending_requests(company=None, farm=None, mine=0, limit=500):
	"""Attendance Requests still waiting — raised from this page or not — with
	who is to approve each, for the Attendance Requests tab. ``can_approve``
	marks the ones the signed-in user may approve; ``mine=1`` lists only those,
	whatever company the page is filtered to."""
	from upande_ta.upande_ta.api.attendance_insights import get_allowed_companies

	allowed = get_allowed_companies()
	has_approver = frappe.db.has_column("Attendance Request", "custom_approver_name")
	conditions = ["ar.docstatus = 0"]
	values = {"limit": min(cint(limit) or 500, 1000), "me": frappe.session.user}
	if cint(mine):
		if not has_approver:
			return []
		conditions.append("ar.custom_approver = %(me)s")
	elif company:
		conditions.append("ar.company = %(company)s")
		values["company"] = company
	elif allowed is not None:
		if not allowed:
			return []
		conditions.append("ar.company in %(allowed)s")
		values["allowed"] = tuple(allowed)
	has_farm = frappe.db.has_column("Employee", "custom_farm")
	if farm and has_farm and not cint(mine):
		conditions.append("TRIM(e.custom_farm) = TRIM(%(farm)s)")
		values["farm"] = farm
	has_state = frappe.db.has_column("Attendance Request", "workflow_state")

	rows = frappe.db.sql(
		f"""
		select ar.name, ar.employee, ar.employee_name, ar.from_date, ar.to_date,
			ar.explanation, ar.owner, ar.creation,
			{"ar.workflow_state" if has_state else "''"} as state,
			{"ar.custom_approver_name" if has_approver else "''"} as approver_name,
			{"ar.custom_approver" if has_approver else "''"} as approver,
			{"e.custom_farm" if has_farm else "''"} as farm
		from `tabAttendance Request` ar
		left join `tabEmployee` e on e.name = ar.employee
		where {" and ".join(conditions)}
		order by ar.creation desc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)
	requesters = dict(
		frappe.get_all(
			"User", filters={"name": ["in", list({r.owner for r in rows}) or [""]]}, fields=["name", "full_name"], as_list=True
		)
	)
	for row in rows:
		row.days = frappe.utils.date_diff(row.to_date, row.from_date) + 1
		row.from_date, row.to_date = str(row.from_date), str(row.to_date)
		row.requested_by = requesters.get(row.owner) or row.owner
		row.state = row.state or _("Draft")
		row.can_approve = bool(row.approver) and row.approver == frappe.session.user and row.state == PENDING_STATE
	return rows


@frappe.whitelist(methods=["POST"])
def act_on_requests(names, action):
	"""Approve or reject the ticked requests from the Attendance Requests
	tab. Each goes through the site workflow as the signed-in user, so only
	a request's own approver gets anywhere; every request is reported."""
	from frappe.model.workflow import apply_workflow, get_transitions

	if action not in ("Approve", "Reject"):
		frappe.throw(_("Unknown action {0}.").format(action))
	names = frappe.parse_json(names) if isinstance(names, str) and names.strip().startswith("[") else names
	if isinstance(names, str):
		names = [n.strip() for n in names.split(",") if n.strip()]

	results = []
	for name in (names or [])[:MAX_EMPLOYEES]:
		entry = {"name": name}
		try:
			frappe.db.savepoint("attendance_request_act")
			doc = frappe.get_doc("Attendance Request", name)
			entry["employee_name"] = doc.employee_name or doc.employee
			if action not in [t.action for t in get_transitions(doc)]:
				entry.update({"ok": False, "error": _("You cannot {0} this request").format(_(action).lower())})
				results.append(entry)
				continue
			doc = apply_workflow(doc, action)
			frappe.clear_messages()
			entry.update({"ok": True, "state": doc.get("workflow_state")})
		except Exception as e:
			frappe.db.rollback(save_point="attendance_request_act")
			frappe.clear_messages()
			entry.update({"ok": False, "error": frappe.utils.strip_html(str(e))[:300]})
		results.append(entry)

	ok = [r for r in results if r.get("ok")]
	return {"results": results, "ok_count": len(ok), "err_count": len(results) - len(ok)}


def copy_marking_reason(doc, method=None):
	"""On submit, the Marking Reason goes onto the Attendance HRMS just
	marked, so the register still tells a hand-marked day from a scanned
	one."""
	reason = doc.get("custom_marking_reason")
	if not reason or not frappe.db.has_column("Attendance", "custom_marking_reason"):
		return
	frappe.db.sql(
		"""update `tabAttendance` set custom_marking_reason = %s
		where attendance_request = %s and docstatus = 1""",
		(reason, doc.name),
	)
