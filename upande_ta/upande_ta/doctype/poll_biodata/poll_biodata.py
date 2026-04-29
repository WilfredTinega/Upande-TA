# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from upande_ta.upande_ta.doctype.biometric_users.biometric_users import _post_to_nodered


class PollBioData(Document):
	pass


@frappe.whitelist()
def request_biodata(doc_name, pin=None):
	doc = frappe.get_doc("Poll BioData", doc_name)

	if not doc.device_sn:
		frappe.throw("Please select a device first")

	cmd_id = frappe.generate_hash(length=10)
	field_desc = "Pin,No,Index,Valid,Type,MajorVer,MinorVer,Format,Tmp"

	command = (
		f"C:{cmd_id}:DATA QUERY BIODATA"
		f"\tFieldDesc={field_desc}"
	)

	cache_key = f"poll_biodata_filter:{doc.device_sn}"
	if pin:
		frappe.cache().set_value(cache_key, str(pin).strip(), expires_in_sec=300)
	else:
		frappe.cache().delete_value(cache_key)

	_post_to_nodered({
		"command_id":    cmd_id,
		"command_type":  "Poll BioData",
		"device_sn":     doc.device_sn,
		"user_id":       pin or "",
		"employee_name": "",
		"command":       command
	})

	return {
		"status":     "queued",
		"command_id": cmd_id,
		"device_sn":  doc.device_sn,
		"command":    command
	}


@frappe.whitelist(allow_guest=True)
def store_biodata():
	data        = frappe.request.get_json() or {}
	user_id     = str(data.get("user_id")   or "").strip()
	device_sn   = str(data.get("device_sn") or "").strip()
	bio_type    = str(data.get("bio_type")  or "Fingerprint").strip()
	bio_no      = int(data.get("bio_no")    or 0)
	bio_index   = int(data.get("bio_index") or 0)
	valid       = int(data.get("valid")     or 1)
	major_ver   = int(data.get("major_ver") or 0)
	minor_ver   = int(data.get("minor_ver") or 0)
	size        = int(data.get("size")      or 0)
	template    = str(data.get("template")  or "").strip()

	if not user_id or not template:
		frappe.response["http_status_code"] = 400
		frappe.response["message"] = {
			"status": "error",
			"error":  "Missing user_id or template"
		}
		return

	if device_sn:
		wanted_pin = frappe.cache().get_value(f"poll_biodata_filter:{device_sn}")
		if wanted_pin and str(wanted_pin).strip() != user_id:
			frappe.response["message"] = {
				"status": "skipped",
				"reason": f"PIN {user_id} filtered out (requested {wanted_pin})"
			}
			return

	employee_name = frappe.db.get_value(
		"Employee",
		{"attendance_device_id": user_id},
		"name"
	)

	if not employee_name:
		frappe.response["message"] = {
			"status": "skipped",
			"reason": f"No employee found for PIN {user_id}"
		}
		return

	doc_name = f"BIO-{employee_name}-{bio_type}-{bio_no}"

	if frappe.db.exists("BioData Template", doc_name):
		doc = frappe.get_doc("BioData Template", doc_name)
		doc.bio_index     = bio_index
		doc.valid         = valid
		doc.major_ver     = major_ver
		doc.minor_ver     = minor_ver
		doc.size          = size
		doc.template      = template
		doc.source_device = device_sn
		doc.captured_at   = frappe.utils.now_datetime()
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({
			"doctype":       "BioData Template",
			"employee":      employee_name,
			"bio_type":      bio_type,
			"bio_no":        bio_no,
			"bio_index":     bio_index,
			"valid":         valid,
			"major_ver":     major_ver,
			"minor_ver":     minor_ver,
			"size":          size,
			"template":      template,
			"source_device": device_sn,
			"captured_at":   frappe.utils.now_datetime()
		})
		doc.insert(ignore_permissions=True)

	frappe.db.commit()

	frappe.response["message"] = {
		"status":   "stored",
		"employee": employee_name,
		"user_id":  user_id,
		"bio_type": bio_type,
		"bio_no":   bio_no,
		"doc_name": doc.name
	}
