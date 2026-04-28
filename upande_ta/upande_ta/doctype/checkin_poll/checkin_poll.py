# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from upande_ta.upande_ta.doctype.biometric_users.biometric_users import _post_to_nodered


class CheckinPoll(Document):
	pass


@frappe.whitelist()
def poll_devices(doc_name):
	doc = frappe.get_doc("Checkin Poll", doc_name)

	if not doc.start_date or not doc.end_date:
		frappe.throw("Start Date and End Date are required")
	if doc.start_date > doc.end_date:
		frappe.throw("Start Date cannot be after End Date")
	if not doc.devices:
		frappe.throw("Add at least one device to poll")

	queued = []
	failed = []

	for row in doc.devices:
		try:
			if not row.device:
				failed.append({"row": row.idx, "reason": "Missing device"})
				continue

			device_sn = frappe.db.get_value("Biometric Device", row.device, "device_sn") or row.device
			cmd_id    = frappe.generate_hash(length=10)

			command = (
				f"C:{cmd_id}:DATA QUERY ATTLOG"
				f"\tStartTime={doc.start_date} 00:00:00"
				f"\tEndTime={doc.end_date} 23:59:59"
			)

			_post_to_nodered({
				"command_id":   cmd_id,
				"command_type": "Poll Attendance",
				"device_sn":    device_sn,
				"start_date":   str(doc.start_date),
				"end_date":     str(doc.end_date),
				"command":      command
			})

			row.command_id = cmd_id
			row.status     = "Queued"
			queued.append({"device": row.device, "command_id": cmd_id})

		except Exception as e:
			row.status = "Failed"
			failed.append({"device": row.device, "reason": str(e)})

	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"status": "done",
		"queued": len(queued),
		"failed": len(failed),
		"details": queued,
		"errors":  failed
	}
