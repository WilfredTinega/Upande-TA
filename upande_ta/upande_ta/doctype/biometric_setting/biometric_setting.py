# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from upande_ta.upande_ta.doctype.biometric_user.biometric_user import _post_to_nodered


_FREQUENCY_SCRIPTS = {
	"checkin_event_frequency":  "Biometric: Poll Attendance",
	"users_event_frequency":    "Biometric: Sync Users",
	"biodata_event_frequency":  "Biometric: Sync BioData",
}


class BiometricSetting(Document):
	def on_update(self):
		for fieldname, script_name in _FREQUENCY_SCRIPTS.items():
			frequency = self.get(fieldname)
			if not frequency:
				continue
			if not frappe.db.exists("Server Script", script_name):
				continue
			current = frappe.db.get_value("Server Script", script_name, "event_frequency")
			if current != frequency:
				frappe.db.set_value(
					"Server Script", script_name, "event_frequency", frequency
				)


@frappe.whitelist()
def poll_devices():
	doc = frappe.get_single("Biometric Setting")

	if not doc.start_date or not doc.end_date:
		frappe.throw("Start Date and End Date are required")
	if doc.start_date > doc.end_date:
		frappe.throw("Start Date cannot be after End Date")
	if not doc.poll_devices:
		frappe.throw("Add at least one device to poll")

	queued = []
	failed = []

	for row in doc.poll_devices:
		try:
			if not row.device:
				failed.append({"row": row.idx, "reason": "Missing device"})
				continue

			device_sn = row.device
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


_BIODATA_QUERIES = [
	("FINGERTMP", "Fingerprint"),
	("FACE",      "Face"),
	("BIOPHOTO",  "BioPhoto"),
	("USERINFO",  "Password"),
	("BIODATA",   "Palm"),
]


@frappe.whitelist()
def request_biodata(device_sn, pin=None):
	if not device_sn:
		frappe.throw("Please select a device first")

	pin = (str(pin).strip() if pin else "")

	cache_key = f"poll_biodata_filter:{device_sn}"
	if pin:
		frappe.cache().set_value(cache_key, pin, expires_in_sec=300)
	else:
		frappe.cache().delete_value(cache_key)

	queued = []
	for table, label in _BIODATA_QUERIES:
		cmd_id = frappe.generate_hash(length=10)

		parts = [f"C:{cmd_id}:DATA QUERY {table}"]
		if pin:
			parts.append(f"PIN={pin}")
		if table == "BIODATA":
			parts.append("Type=8")
		command = "\t".join(parts)

		_post_to_nodered({
			"command_id":    cmd_id,
			"command_type":  f"Poll {label}",
			"device_sn":     device_sn,
			"user_id":       pin,
			"employee_name": "",
			"command":       command,
		})
		queued.append({"table": table, "command_id": cmd_id, "command": command})

	return {
		"status":    "queued",
		"device_sn": device_sn,
		"pin":       pin or None,
		"queued":    queued,
	}


_BIOTEMPLATE_TRACKED_FIELDS = (
	"bio_type", "type_code", "bio_no", "bio_index", "valid",
	"major_ver", "minor_ver", "size", "template", "source_device",
	"privilege", "password", "card", "vice_card", "user_group",
	"timezone_group", "verify_mode", "start_datetime", "end_datetime",
	"raw_log",
)


def _str(v):
	return "" if v is None else str(v).strip()


def _int(v, default=0):
	try:
		return int(v) if v not in (None, "") else default
	except (TypeError, ValueError):
		return default


@frappe.whitelist(allow_guest=True)
def store_biotemplate():
	data       = frappe.request.get_json() or {}
	bio_type   = _str(data.get("bio_type"))
	device_sn  = _str(data.get("device_sn") or data.get("source_device"))
	user_id    = _str(data.get("user_id") or data.get("employee_id") or data.get("employee"))

	if not user_id:
		frappe.response["http_status_code"] = 400
		frappe.response["message"] = {"status": "error", "error": "Missing user_id"}
		return

	is_user_record = bio_type.lower() == "user"
	if not is_user_record and not _str(data.get("template")):
		frappe.response["http_status_code"] = 400
		frappe.response["message"] = {"status": "error", "error": "Missing template"}
		return

	if device_sn and not is_user_record:
		wanted_pin = frappe.cache().get_value(f"poll_biodata_filter:{device_sn}")
		if wanted_pin and _str(wanted_pin) != user_id:
			frappe.response["message"] = {
				"status": "skipped",
				"reason": f"PIN {user_id} filtered out (requested {wanted_pin})",
			}
			return

	employee_name = frappe.db.get_value(
		"Employee", {"attendance_device_id": user_id}, "name"
	)
	if not employee_name:
		frappe.response["message"] = {
			"status": "skipped",
			"reason": f"No employee found for PIN {user_id}",
		}
		return

	now = frappe.utils.now_datetime()

	if is_user_record:
		new_values = {
			"privilege":      _str(data.get("privilege")),
			"password":       _str(data.get("password")),
			"card":           _str(data.get("card")),
			"vice_card":      _str(data.get("vice_card")),
			"user_group":     _str(data.get("group")),
			"timezone_group": _str(data.get("timezone_group")),
			"verify_mode":    _str(data.get("verify_mode")),
			"start_datetime": _str(data.get("start_datetime")),
			"end_datetime":   _str(data.get("end_datetime")),
			"source_device":  device_sn,
			"raw_log":        _str(data.get("raw_log")),
		}
	else:
		new_values = {
			"bio_type":      bio_type or "Fingerprint",
			"type_code":     _str(data.get("type_code")),
			"bio_no":        _int(data.get("bio_no")),
			"bio_index":     _int(data.get("bio_index")),
			"valid":         _int(data.get("valid"), 1),
			"major_ver":     _int(data.get("major_ver")),
			"minor_ver":     _int(data.get("minor_ver")),
			"size":          _int(data.get("size")),
			"template":      _str(data.get("template")),
			"source_device": device_sn,
			"raw_log":       _str(data.get("raw_log")),
		}

	existing = frappe.db.get_value(
		"Biometric Template",
		{
			"parent":      "Biometric Setting",
			"parentfield": "bio_templates",
			"employee":    employee_name,
		},
		("name",) + _BIOTEMPLATE_TRACKED_FIELDS,
		as_dict=True,
	)

	if existing:
		changed = {
			k: v for k, v in new_values.items()
			if existing.get(k) != v
		}
		if changed:
			changed["captured_at"] = now
			frappe.db.set_value(
				"Biometric Template", existing["name"], changed,
				update_modified=False,
			)
			frappe.db.commit()
			status = "updated"
		else:
			status = "unchanged"
		row_name = existing["name"]
	else:
		row_name = frappe.generate_hash(length=10)
		idx = (frappe.db.sql(
			"""
			SELECT COALESCE(MAX(idx), 0) + 1
			FROM `tabBiometric Template`
			WHERE parent = %s AND parentfield = %s
			""",
			("Biometric Setting", "bio_templates"),
		)[0][0]) or 1

		row = frappe.get_doc({
			"doctype":       "Biometric Template",
			"name":          row_name,
			"parent":        "Biometric Setting",
			"parenttype":    "Biometric Setting",
			"parentfield":   "bio_templates",
			"idx":           idx,
			"employee":      employee_name,
			"employee_name": _str(data.get("employee_name")) or frappe.db.get_value(
				"Employee", employee_name, "employee_name"
			),
			"user_id":       user_id,
			"captured_at":   now,
			**new_values,
		})
		row.db_insert()
		frappe.db.commit()
		status = "inserted"

	frappe.response["message"] = {
		"status":   status,
		"employee": employee_name,
		"user_id":  user_id,
		"bio_type": new_values.get("bio_type") or bio_type,
		"row_name": row_name,
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

	settings = frappe.get_single("Biometric Setting")
	now = frappe.utils.now_datetime()

	existing = next(
		(
			r for r in (settings.bio_templates or [])
			if r.employee == employee_name
			and r.bio_type == bio_type
			and (r.bio_no or 0) == bio_no
			and (r.source_device or "") == device_sn
		),
		None
	)

	if existing:
		existing.bio_index   = bio_index
		existing.valid       = valid
		existing.major_ver   = major_ver
		existing.minor_ver   = minor_ver
		existing.size        = size
		existing.template    = template
		existing.captured_at = now
		row = existing
	else:
		row = settings.append("bio_templates", {
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
			"captured_at":   now
		})

	settings.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.response["message"] = {
		"status":   "stored",
		"employee": employee_name,
		"user_id":  user_id,
		"bio_type": bio_type,
		"bio_no":   bio_no,
		"row_name": row.name
	}
