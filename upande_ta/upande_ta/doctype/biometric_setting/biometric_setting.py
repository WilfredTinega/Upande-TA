# Copyright (c) 2026, Upande LTD and contributors

from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from upande_ta.upande_ta.doctype.biometric_user.biometric_user import (
 _build_userinfo_command,
 _delete_user_row,
 _get_template_row,
 _post_to_nodered,
 _queue_biodata_for_user,
 _upsert_user_row,
)

SCHEDULER_TASKS = [
 ("checkin", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_checkin",              "Biometric: Poll Attendance"),
 ("users",   "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_users_sync",           "Biometric: Sync Users"),
 ("biodata", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_biodata_sync",         "Biometric: Sync BioData"),
 ("cleanup", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_deactivation_cleanup", "Biometric: Deactivation Cleanup"),
]

_TASK_BY_PREFIX = {prefix: (method, label) for prefix, method, label in SCHEDULER_TASKS}

_PREFIX_ENABLE = {
 "checkin": "enable_checkin",
 "users":   "enable_users",
 "biodata": "enable_bio_templates",
 "cleanup": "enable_cleanup",
}

_FREQUENCY_WINDOWS = {
 "All":          timedelta(hours=1),
 "Hourly":       timedelta(hours=1),
 "Hourly Long":  timedelta(hours=1),
 "Daily":        timedelta(days=1),
 "Daily Long":   timedelta(days=1),
 "Weekly":       timedelta(days=7),
 "Weekly Long":  timedelta(days=7),
 "Monthly":      timedelta(days=30),
 "Monthly Long": timedelta(days=30),
 "Yearly":       timedelta(days=365),
 "Cron":         timedelta(hours=1),
}

class BiometricSetting(Document):
	def validate(self):
		self._block_removing_devices_with_templates()

	def _block_removing_devices_with_templates(self):
		if not frappe.db.exists("DocType", "Biometric Template"):
			return
		current_sns = {d.device_sn for d in (self.devices or []) if d.device_sn}
		previous_sns = set(
		 frappe.get_all(
		  "Biometric Device",
		  filters={"parent": self.name, "parentfield": "devices"},
		  pluck="device_sn",
		 )
		)
		removed = previous_sns - current_sns
		if not removed:
			return
		blocked = frappe.get_all(
		 "Biometric Template",
		 filters={"device_sn": ("in", list(removed))},
		 fields=["name", "device_sn"],
		)
		if not blocked:
			return
		lines = "\n".join(f"  - {b.device_sn} (template: {b.name})" for b in blocked)
		frappe.throw(
		 f"Cannot remove the following device(s) — they have Biometric Template records:\n{lines}\n\nDelete the Biometric Template doc(s) first."
		)

	def on_update(self):
		self._sync_scheduled_jobs()

	def _sync_scheduled_jobs(self, force=False):
		for prefix, method, _label in SCHEDULER_TASKS:
			fields = (
			 f"{prefix}_event_frequency",
			 f"{prefix}_cron_format",
			 _PREFIX_ENABLE[prefix],
			)
			if not force and not any(self.has_value_changed(f) for f in fields):
				continue
			self._upsert_scheduled_job(prefix, method)

	def _upsert_scheduled_job(self, prefix, method):
		frequency = (self.get(f"{prefix}_event_frequency") or "").strip()
		cron_format = (self.get(f"{prefix}_cron_format") or "").strip()
		enabled = bool(self.get(_PREFIX_ENABLE[prefix]))

		stopped = 1 if (not enabled or not frequency) else 0
		if frequency == "Cron" and not cron_format:
			stopped = 1

		effective_frequency = "Daily" if (frequency == "Cron" and not cron_format) else frequency

		job_name = frappe.db.get_value("Scheduled Job Type", {"method": method})

		if not job_name:
			if stopped:
				return
			job = frappe.new_doc("Scheduled Job Type")
			job.method = method
			job.create_log = effective_frequency not in ("All", "Cron")
			job.frequency = effective_frequency
			job.cron_format = cron_format if effective_frequency == "Cron" else ""
			job.stopped = 0
			job.insert(ignore_permissions=True)
			return

		new_frequency = effective_frequency or "Daily"
		new_cron = cron_format if effective_frequency == "Cron" else ""

		current = frappe.db.get_value(
		 "Scheduled Job Type",
		 job_name,
		 ["frequency", "cron_format", "stopped"],
		 as_dict=True,
		)
		updates = {}
		if current.frequency != new_frequency:
			updates["frequency"] = new_frequency
		if (current.cron_format or "") != new_cron:
			updates["cron_format"] = new_cron
		if int(current.stopped or 0) != stopped:
			updates["stopped"] = stopped

		if updates:
			frappe.db.set_value("Scheduled Job Type", job_name, updates)

@frappe.whitelist()
def get_scheduled_job_links():
	out = {}
	for prefix, method, label in SCHEDULER_TASKS:
		row = frappe.db.get_value(
		 "Scheduled Job Type",
		 {"method": method},
		 ["name", "stopped", "frequency"],
		 as_dict=True,
		)
		if row:
			out[prefix] = {
			 "name":      row.name,
			 "stopped":   int(row.stopped or 0),
			 "frequency": row.frequency or "",
			 "label":     label,
			}
		else:
			out[prefix] = None
	return out

@frappe.whitelist()
def get_templated_pins_per_device():
	settings = frappe.get_doc("Biometric Setting", "Biometric Setting")
	devices = [
	 {"device_sn": d.device_sn, "device_location": d.device_location or d.device_sn}
	 for d in (settings.devices or []) if d.device_sn
	]
	pins_by_device = {d["device_sn"]: [] for d in devices}

	if not devices or not frappe.db.exists("DocType", "Biometric Template"):
		return {"devices": devices, "pins_by_device": pins_by_device}

	parents = frappe.get_all(
	 "Biometric Template",
	 filters={"device_sn": ("in", [d["device_sn"] for d in devices])},
	 fields=["name", "device_sn"],
	)
	parent_to_sn = {p.name: p.device_sn for p in parents}
	if not parent_to_sn:
		return {"devices": devices, "pins_by_device": pins_by_device}

	rows = frappe.get_all(
	 "Bio Template",
	 filters={
	  "parent": ("in", list(parent_to_sn)),
	  "parentfield": "bio_templates",
	  "deleted": 0,
	 },
	 fields=[
	  "parent", "user_id",
	  "fp_valid", "face_valid", "palm_valid",
	  "fingerprint_template", "face_template", "palm_template",
	 ],
	)
	for r in rows:
		if not r.user_id:
			continue
		has_bio = (
		 (r.fp_valid and (r.fingerprint_template or "").strip()) or
		 (r.face_valid and (r.face_template or "").strip()) or
		 (r.palm_valid and (r.palm_template or "").strip())
		)
		if not has_bio:
			continue
		sn = parent_to_sn.get(r.parent)
		if sn and r.user_id not in pins_by_device[sn]:
			pins_by_device[sn].append(r.user_id)

	return {"devices": devices, "pins_by_device": pins_by_device}

@frappe.whitelist()
def get_device_templates(device_sn):
	if not device_sn:
		return []
	if not frappe.db.exists("DocType", "Biometric Template"):
		return []
	parent_name = frappe.db.get_value("Biometric Template", {"device_sn": device_sn}, "name")
	if not parent_name:
		return []
	rows = frappe.get_all(
	 "Bio Template",
	 filters={
	  "parent":      parent_name,
	  "parentfield": "bio_templates",
	  "deleted":     0,
	 },
	 fields=[
	  "name", "employee", "employee_name", "user_id", "captured_at",
	  "fp_valid", "face_valid", "palm_valid",
	  "fingerprint_template", "face_template", "palm_template",
	  "password", "card",
	 ],
	 order_by="employee_name asc",
	)
	out = []
	for r in rows:
		out.append({
		 "row_name":      r.name,
		 "parent_name":   parent_name,
		 "employee":      r.employee,
		 "employee_name": r.employee_name or "",
		 "user_id":       r.user_id or "",
		 "captured_at":   frappe.utils.format_datetime(r.captured_at) if r.captured_at else "",
		 "has_fp":       bool(r.fp_valid and (r.fingerprint_template or "").strip()),
		 "has_face":     bool(r.face_valid and (r.face_template or "").strip()),
		 "has_palm":     bool(r.palm_valid and (r.palm_template or "").strip()),
		 "has_password": bool((r.password or "").strip()),
		 "has_card":     bool((r.card or "").strip()),
		})
	return out

@frappe.whitelist()
def delete_device_template_row(row_name):
	if not row_name:
		frappe.throw("row_name is required")
	if not frappe.db.exists("Bio Template", row_name):
		return {"status": "not_found"}
	frappe.db.delete("Bio Template", {"name": row_name})
	frappe.db.commit()
	return {"status": "deleted"}

@frappe.whitelist()
def devices_with_templates(device_sns):
	import json
	if not device_sns:
		return {}
	if isinstance(device_sns, str):
		device_sns = json.loads(device_sns)
	if not device_sns:
		return {}
	if not frappe.db.exists("DocType", "Biometric Template"):
		return {}
	rows = frappe.get_all(
	 "Biometric Template",
	 filters={"device_sn": ("in", list(device_sns))},
	 fields=["name", "device_sn"],
	)
	out = {}
	for r in rows:
		out.setdefault(r.device_sn, []).append(r.name)
	return out

@frappe.whitelist()
def resync_scheduled_jobs():
	try:
		doc = frappe.get_single("Biometric Setting")
		doc._sync_scheduled_jobs(force=True)
		frappe.db.commit()
		return {
		 "jobs": frappe.get_all(
		  "Scheduled Job Type",
		  filters={"method": ["like", "%biometric_setting%"]},
		  fields=["method", "frequency", "cron_format", "stopped"],
		  order_by="method",
		 )
		}
	except Exception:

		frappe.log_error(
		 title="Biometric Setting: resync_scheduled_jobs skipped",
		 message=frappe.get_traceback(),
		)
		return {"jobs": [], "skipped": True}

def _window_for(prefix):
	doc = frappe.get_single("Biometric Setting")
	freq = (doc.get(f"{prefix}_event_frequency") or "").strip()
	delta = _FREQUENCY_WINDOWS.get(freq, timedelta(hours=1))
	end = now_datetime()
	start = end - delta
	return start, end

def _poll_attendance(start_dt, end_dt, devices):
	queued = []
	failed = []
	for device_sn in devices:
		try:
			cmd_id = frappe.generate_hash(length=10)
			command = (
			 f"C:{cmd_id}:DATA QUERY ATTLOG"
			 f"\tStartTime={start_dt:%Y-%m-%d %H:%M:%S}"
			 f"\tEndTime={end_dt:%Y-%m-%d %H:%M:%S}"
			)
			_post_to_nodered({
			 "command_id":   cmd_id,
			 "command_type": "Poll Attendance",
			 "device_sn":    device_sn,
			 "start_date":   start_dt.strftime("%Y-%m-%d"),
			 "end_date":     end_dt.strftime("%Y-%m-%d"),
			 "command":      command,
			})
			queued.append({"device": device_sn, "command_id": cmd_id})
		except Exception as e:
			failed.append({"device": device_sn, "reason": str(e)})
	return queued, failed

@frappe.whitelist()
def poll_devices():
	doc = frappe.get_single("Biometric Setting")

	if not doc.enable_checkin:
		frappe.throw("Enable Checkin")

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

def run_checkin():
	settings = frappe.get_single("Biometric Setting")
	if not settings.enable_checkin:
		return {"skipped": True, "reason": "enable_checkin is off"}
	devices = [row.device for row in (settings.poll_devices or []) if row.device]
	if not devices:
		return {"skipped": True, "reason": "No devices in poll_devices table"}
	start, end = _window_for("checkin")
	queued, failed = _poll_attendance(start, end, devices)
	return {
	 "status": "done",
	 "window": {"start": str(start), "end": str(end)},
	 "queued": len(queued),
	 "failed": len(failed),
	}

def run_users_sync():
	settings = frappe.get_single("Biometric Setting")
	if not settings.enable_users:
		return {"skipped": True, "reason": "enable_users is off"}
	devices = [row.device_sn for row in (settings.devices or []) if row.device_sn]
	if not devices:
		return {"skipped": True, "reason": "No devices in devices table"}

	queued = []
	failed = []
	for device_sn in devices:
		try:
			cmd_id = frappe.generate_hash(length=10)
			command = f"C:{cmd_id}:DATA QUERY USERINFO"
			_post_to_nodered({
			 "command_id":    cmd_id,
			 "command_type":  "Poll Users",
			 "device_sn":     device_sn,
			 "user_id":       "",
			 "employee_name": "",
			 "command":       command,
			})
			queued.append({"device": device_sn, "command_id": cmd_id})
		except Exception as e:
			failed.append({"device": device_sn, "reason": str(e)})
	return {"status": "done", "queued": len(queued), "failed": len(failed)}

def run_deactivation_cleanup():
	settings = frappe.get_single("Biometric Setting")
	if not settings.enable_cleanup:
		return {"skipped": True, "reason": "enable_cleanup is off"}

	deleted_queued = []
	deleted_failed = []
	added_queued = []
	added_failed = []

	stale_rows = []
	if frappe.db.exists("DocType", "Biometric Template"):
		stale_rows = frappe.db.sql(
		 """
			SELECT DISTINCT bp.device_sn, bt.user_id, bt.employee, bt.employee_name
			FROM `tabBio Template` bt
			INNER JOIN `tabBiometric Template` bp
			        ON bp.name = bt.parent
			       AND bt.parenttype = 'Biometric Template'
			       AND bt.parentfield = 'bio_templates'
			INNER JOIN `tabEmployee` e ON e.name = bt.employee
			WHERE bp.device_sn IS NOT NULL AND bp.device_sn != ''
			  AND bt.user_id   IS NOT NULL AND bt.user_id   != ''
			  AND e.status != 'Active'
			""",
		 as_dict=True,
		)
	for r in stale_rows:
		try:
			cmd_id = frappe.generate_hash(length=10)
			command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={r.user_id}"
			_post_to_nodered({
			 "command_id":    cmd_id,
			 "command_type":  "Delete User",
			 "device_sn":     r.device_sn,
			 "user_id":       r.user_id,
			 "employee_name": r.employee_name or "",
			 "command":       command,
			})
			_delete_user_row(r.device_sn, r.user_id)
			deleted_queued.append({
			 "device": r.device_sn, "user_id": r.user_id,
			 "employee": r.employee, "command_id": cmd_id,
			})
		except Exception as e:
			deleted_failed.append({
			 "device": r.device_sn, "user_id": r.user_id, "reason": str(e),
			})

	device_sns = [d.device_sn for d in (settings.devices or []) if d.device_sn]
	if device_sns:
		active_employees = frappe.get_all(
		 "Employee",
		 filters={
		  "status": "Active",
		  "attendance_device_id": ["!=", ""],
		 },
		 fields=["name", "employee_name", "attendance_device_id"],
		)
		user_ids = {
		 (emp.attendance_device_id or "").strip()
		 for emp in active_employees
		 if (emp.attendance_device_id or "").strip()
		}

		existing_pairs = set()
		if user_ids:
			existing_pairs = {
			 (r.device_sn, r.user_id)
			 for r in frappe.get_all(
			  "Biometric User",
			  filters={
			   "device_sn": ["in", device_sns],
			   "user_id":   ["in", list(user_ids)],
			  },
			  fields=["device_sn", "user_id"],
			 )
			}

		template_pairs = set()
		if user_ids and frappe.db.exists("DocType", "Biometric Template"):
			template_pairs = {
			 (row.device_sn, row.user_id)
			 for row in frappe.db.sql(
			  """
					SELECT bp.device_sn, bt.user_id
					FROM `tabBio Template` bt
					INNER JOIN `tabBiometric Template` bp
					        ON bp.name = bt.parent
					       AND bt.parenttype = 'Biometric Template'
					       AND bt.parentfield = 'bio_templates'
					WHERE bp.device_sn IN %(devices)s
					  AND bt.user_id   IN %(pins)s
					""",
			  {"devices": tuple(device_sns), "pins": tuple(user_ids)},
			  as_dict=True,
			 )
			}

		for emp in active_employees:
			user_id = (emp.attendance_device_id or "").strip()
			if not user_id:
				continue
			emp_name = (emp.employee_name or "")[:24]
			tpl = None
			for device_sn in device_sns:
				if (device_sn, user_id) in existing_pairs:
					continue
				if (device_sn, user_id) in template_pairs:
					continue
				try:
					if tpl is None:
						tpl = _get_template_row(emp.name)
					cmd_id = frappe.generate_hash(length=10)
					command = _build_userinfo_command(cmd_id, user_id, emp_name, "0", tpl)
					_upsert_user_row(device_sn, user_id, {
					 "employee":       emp.name,
					 "employee_name":  emp_name,
					 "privilege":      "0",
					 "status":         "Active",
					 "command_status": "Pending",
					 "add_user":       command,
					})
					_post_to_nodered({
					 "command_id":    cmd_id,
					 "command_type":  "Add User",
					 "device_sn":     device_sn,
					 "user_id":       user_id,
					 "employee_name": emp_name,
					 "command":       command,
					})
					_queue_biodata_for_user(device_sn, user_id, emp.name, tpl)
					added_queued.append({
					 "device": device_sn, "user_id": user_id,
					 "employee": emp.name, "command_id": cmd_id,
					})
				except Exception as e:
					added_failed.append({
					 "device": device_sn, "user_id": user_id, "reason": str(e),
					})

	frappe.db.commit()
	return {
	 "status":  "done",
	 "deleted": {"queued": len(deleted_queued), "failed": len(deleted_failed)},
	 "added":   {"queued": len(added_queued),   "failed": len(added_failed)},
	}

def run_biodata_sync():
	settings = frappe.get_single("Biometric Setting")
	if not settings.enable_bio_templates:
		return {"skipped": True, "reason": "enable_bio_templates is off"}
	devices = [row.device_sn for row in (settings.devices or []) if row.device_sn]
	if not devices:
		return {"skipped": True, "reason": "No devices in devices table"}

	results = []
	for device_sn in devices:
		try:
			results.append(request_biodata_internal(device_sn))
		except Exception as e:
			results.append({"device_sn": device_sn, "error": str(e)})
	return {"status": "done", "devices": len(devices), "results": results}

def request_biodata_internal(device_sn, pin=None):
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
		queued.append({"table": table, "command_id": cmd_id})
	return {"device_sn": device_sn, "pin": pin or None, "queued": queued}

_BIODATA_QUERIES = [
 ("FINGERTMP", "Fingerprint"),
 ("FACE",      "Face"),
 ("BIOPHOTO",  "BioPhoto"),
 ("USERINFO",  "Password"),
 ("BIODATA",   "Palm"),
]

@frappe.whitelist()
def request_biodata(device_sn, pin=None, pins=None):
	if not frappe.db.get_single_value("Biometric Setting", "enable_bio_templates"):
		frappe.throw("Enable Bio Templates")
	if not device_sn:
		frappe.throw("Please select a device first")

	import json
	pin_list = []
	if pins:
		if isinstance(pins, str):
			pins = json.loads(pins)
		pin_list = [str(p).strip() for p in (pins or []) if str(p).strip()]
	elif pin:
		pin_list = [str(pin).strip()]

	if not pin_list:
		out = request_biodata_internal(device_sn, None)
		out["status"] = "queued"
		out["pins"]   = []
		return out

	all_queued = []
	for p in pin_list:
		result = request_biodata_internal(device_sn, p)
		all_queued.extend(result.get("queued") or [])
	return {
	 "status":    "queued",
	 "device_sn": device_sn,
	 "pins":      pin_list,
	 "queued":    all_queued,
	}

@frappe.whitelist(allow_guest=True)
def store_biotemplate():
	from upande_ta.upande_ta.doctype.biometric_template.biometric_template import (
	 store_biotemplate as _new_store,
	)
	return _new_store()
