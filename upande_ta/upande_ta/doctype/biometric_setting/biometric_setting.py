# Copyright (c) 2026, Upande LTD and contributors

import json
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from upande_ta.upande_ta.doctype.biometric_user.biometric_user import _post_to_nodered

SCHEDULER_TASKS = [
 ("checkin", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_checkin",      "Biometric: Poll Attendance"),
 ("biodata", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_biodata_sync", "Biometric: Sync BioData"),
 ("flip",    "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_flip_last_in", "Biometric: Flip Last IN → OUT"),
]

_TASK_BY_PREFIX = {prefix: (method, label) for prefix, method, label in SCHEDULER_TASKS}

_PREFIX_ENABLE = {
 "checkin": "enable_checkin",
 "biodata": "enable_bio_templates",
 "flip":    "enable_flip",
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
	  "name", "employee", "employee_name", "user_id",
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

def request_biodata_internal(device_sn, pin=None, manage_cache=True):
	pin = (str(pin).strip() if pin else "")
	if manage_cache:
		cache_key = f"poll_biodata_filter:{device_sn}"
		if pin:
			frappe.cache().set_value(cache_key, json.dumps([pin]), expires_in_sec=300)
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

	pin_list = []
	if pins:
		if isinstance(pins, str):
			pins = json.loads(pins)
		pin_list = [str(p).strip() for p in (pins or []) if str(p).strip()]
	elif pin:
		pin_list = [str(pin).strip()]

	cache_key = f"poll_biodata_filter:{device_sn}"
	if pin_list:
		frappe.cache().set_value(cache_key, json.dumps(pin_list), expires_in_sec=300)
	else:
		frappe.cache().delete_value(cache_key)

	if not pin_list:
		out = request_biodata_internal(device_sn, None, manage_cache=False)
		out["status"] = "queued"
		out["pins"]   = []
		return out

	all_queued = []
	for p in pin_list:
		result = request_biodata_internal(device_sn, p, manage_cache=False)
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


@frappe.whitelist(allow_guest=True)
def store_device_status():
	data      = frappe.request.get_json() or {}
	device_sn = (data.get("device_sn") or "").strip()
	last_seen = (data.get("last_seen") or "").strip()

	if not device_sn:
		frappe.response["http_status_code"] = 400
		frappe.response["message"] = {"status": "error", "error": "Missing device_sn"}
		return

	if not last_seen:
		last_seen = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")

	parent_name = frappe.db.get_value(
		"Biometric Device",
		{"parenttype": "Biometric Setting", "parentfield": "devices", "device_sn": device_sn},
		"name",
	)

	if not parent_name:
		frappe.response["http_status_code"] = 404
		frappe.response["message"] = {
			"status": "error",
			"error":  f"Device {device_sn} not configured in Biometric Setting",
		}
		return

	frappe.db.set_value(
		"Biometric Device",
		parent_name,
		{"status": "Online", "last_seen": last_seen},
		update_modified=False,
	)

	stale = mark_stale_devices_offline()
	frappe.db.commit()

	_publish_device_status_update({
		"updated": [{
			"row_name":  parent_name,
			"device_sn": device_sn,
			"status":    "Online",
			"last_seen": last_seen,
		}],
		"offline": stale,
	})

	return {"status": "ok", "device_sn": device_sn, "last_seen": last_seen}


OFFLINE_THRESHOLD_MINUTES = 1


def mark_stale_devices_offline():
	cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-OFFLINE_THRESHOLD_MINUTES)
	stale = frappe.db.sql(
		"""
			SELECT name, device_sn, last_seen
			FROM `tabBiometric Device`
			WHERE parenttype = 'Biometric Setting'
			  AND parentfield = 'devices'
			  AND status = 'Online'
			  AND (last_seen IS NULL OR last_seen < %s)
		""",
		(cutoff,),
		as_dict=True,
	)
	if not stale:
		return []
	frappe.db.sql(
		"""
			UPDATE `tabBiometric Device`
			SET status = 'Offline'
			WHERE parenttype = 'Biometric Setting'
			  AND parentfield = 'devices'
			  AND status = 'Online'
			  AND (last_seen IS NULL OR last_seen < %s)
		""",
		(cutoff,),
	)
	return [
		{
			"row_name":  r.name,
			"device_sn": r.device_sn,
			"status":    "Offline",
			"last_seen": str(r.last_seen) if r.last_seen else None,
		}
		for r in stale
	]


def mark_stale_devices_offline_scheduled():
	stale = mark_stale_devices_offline()
	if stale:
		frappe.db.commit()
		_publish_device_status_update({"updated": [], "offline": stale})
	return {"flipped": len(stale)}


def _publish_device_status_update(payload):
	try:
		frappe.publish_realtime(
			event="biometric_device_status",
			message=payload,
			doctype="Biometric Setting",
			docname="Biometric Setting",
			after_commit=True,
		)
	except Exception:
		frappe.log_error(
			title="Biometric: publish_realtime failed",
			message=frappe.get_traceback(),
		)


def run_flip_last_in():
	settings = frappe.get_single("Biometric Setting")
	if not settings.enable_flip:
		return {"skipped": True, "reason": "enable_flip is off"}
	from upande_ta.upande_ta.overrides.employee_checkin import auto_close_open_ins
	return auto_close_open_ins()
	
