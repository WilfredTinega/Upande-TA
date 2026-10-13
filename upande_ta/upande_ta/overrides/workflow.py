# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""The desk's workflow actions, less those for a Bulk Overtime that needs no
approval: its company is not ticked for approval in Biometric Setting, so the
form submits it directly instead (see bulk_overtime.needs_approval)."""

import frappe
from frappe.model import workflow
from frappe.model.document import Document


@frappe.whitelist()
def get_transitions(doc, workflow_name=None, raise_exception=False):
	from upande_ta.upande_ta.doctype.bulk_overtime.bulk_overtime import needs_approval

	data = doc if isinstance(doc, Document) else frappe._dict(frappe.parse_json(doc))
	if data.get("doctype") == "Bulk Overtime" and data.get("name"):
		saved = frappe.db.get_value("Bulk Overtime", data.name, ["company", "docstatus"], as_dict=True)
		if saved and not saved.docstatus and not needs_approval(saved.company):
			return []
	return workflow.get_transitions(doc, workflow_name, raise_exception)
