# Copyright (c) 2026, Upande LTD and contributors

import json
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now_datetime, nowdate

from upande_ta.upande_ta.doctype.biometric_user.biometric_user import (
	_device_capabilities,
	_device_supports,
	_post_to_nodered,
)

SCHEDULER_TASKS = [
 ("checkin", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_checkin",      "Biometric: Poll Attendance"),
 ("biodata", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_biodata_sync", "Biometric: Sync BioData"),
 ("flip",    "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_flip_last_in", "Biometric: Normalize Checkin Directions"),
 ("absent",  "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_absent_marking", "Biometric: Mark Absentees"),
]

SCHEDULER_EVENT_AGAINST = "Biometric Setting"


def _ensure_scheduler_event(method):
	"""Return the name of a Scheduler Event linking `method` to Biometric Setting,
	creating it if missing. Returns None when the Scheduler Event doctype is absent
	(older Frappe), in which case the job is simply left unstamped."""
	if not frappe.db.exists("DocType", "Scheduler Event"):
		return None
	name = frappe.db.get_value(
		"Scheduler Event",
		{"scheduled_against": SCHEDULER_EVENT_AGAINST, "method": method},
		"name",
	)
	if name:
		return name
	doc = frappe.get_doc({
		"doctype": "Scheduler Event",
		"scheduled_against": SCHEDULER_EVENT_AGAINST,
		"method": method,
	})
	doc.insert(ignore_permissions=True)
	return doc.name

_TASK_BY_PREFIX = {prefix: (method, label) for prefix, method, label in SCHEDULER_TASKS}

_PREFIX_ENABLE = {
 "checkin": "enable_checkin",
 "biodata": "enable_bio_templates",
 "flip":    "enable_flip",
 "absent":  "enable_absent",
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

# Serial-number prefix -> the credentials that family of terminal has actually
# delivered on this fleet. The prefix is not a published model code, but it is a
# reliable grouping: the WJA/CO8D readers have never returned a single
# fingerprint across ~1,000 Bio Template rows, while NYU/PYA/TDBD return both
# 1,400-char fingerprints and face templates. Palm and card have never appeared
# anywhere. Longest prefix wins, so a more specific entry can be added later.
_SERIAL_CAPABILITY_PROFILES = {
	"NYU":  ("Fingerprint + face reader", ("supports_fingerprint", "supports_face", "supports_password")),
	"PYA":  ("Fingerprint + face reader", ("supports_fingerprint", "supports_face", "supports_password")),
	"TDBD": ("Fingerprint + face reader", ("supports_fingerprint", "supports_face", "supports_password")),
	"WJA":  ("Face terminal",             ("supports_face", "supports_password")),
	"CO8D": ("Face terminal",             ("supports_face", "supports_password")),
}


@frappe.whitelist()
def capability_profile_for_serial(device_sn):
	"""Default capability flags for a serial number, by prefix.

	Used when a device is first added, so the operator does not have to know
	which credentials the terminal carries. An unrecognised prefix returns no
	profile and the field defaults (everything on) stand — being permissive on
	an unknown device is safer than silently refusing to push to it.
	"""
	sn = (device_sn or "").strip().upper()
	if not sn:
		return {}

	match = None
	for prefix in sorted(_SERIAL_CAPABILITY_PROFILES, key=len, reverse=True):
		if sn.startswith(prefix):
			match = prefix
			break
	if not match:
		return {}

	model, supported = _SERIAL_CAPABILITY_PROFILES[match]
	return {
		"prefix":       match,
		"model":        model,
		# _CAPABILITY_EVIDENCE is this module's single list of the five flags.
		"capabilities": {f: (1 if f in supported else 0) for f in _CAPABILITY_EVIDENCE},
	}


class BiometricSetting(Document):
	def validate(self):
		self._block_removing_devices_with_links()

	def _block_removing_devices_with_links(self):
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
		blocked = _device_link_summary(list(removed))
		if not blocked:
			return
		frappe.throw(
			_format_blocked_device_message(blocked),
			title="Cannot remove device",
		)

	def on_update(self):
		self._sync_scheduled_jobs()
		self._ensure_biometric_user_parents()
		self._normalize_poll_device_values()

	def _normalize_poll_device_values(self):
		sn_to_loc = {
			d.device_sn: (d.device_location or d.device_sn)
			for d in (self.devices or []) if d.device_sn
		}
		loc_to_sn = {}
		for d in (self.devices or []):
			if d.device_sn and d.device_location:
				loc_to_sn[d.device_location] = d.device_sn
		dirty = False
		for row in (self.poll_devices or []):
			if not row.device:
				continue
			if row.device in sn_to_loc:
				loc = sn_to_loc[row.device]
				if loc != row.device:
					frappe.db.set_value(
						"Biometric Checkin", row.name, "device", loc,
						update_modified=False,
					)
				if not row.device_sn or row.device_sn != row.device:
					frappe.db.set_value(
						"Biometric Checkin", row.name, "device_sn", row.device,
						update_modified=False,
					)
				dirty = True
			elif row.device in loc_to_sn:
				sn = loc_to_sn[row.device]
				if not row.device_sn or row.device_sn != sn:
					frappe.db.set_value(
						"Biometric Checkin", row.name, "device_sn", sn,
						update_modified=False,
					)
					dirty = True
		for picker_field, companion in (
			("users_device_picker",   "users_device_sn"),
			("biodata_device_picker", "biodata_device_sn"),
		):
			value = self.get(picker_field)
			if not value:
				continue
			if value in sn_to_loc:
				loc = sn_to_loc[value]
				if loc != value:
					frappe.db.set_value(
						"Biometric Setting", self.name, picker_field, loc,
						update_modified=False,
					)
				if self.get(companion) != value:
					frappe.db.set_value(
						"Biometric Setting", self.name, companion, value,
						update_modified=False,
					)
				dirty = True
			elif value in loc_to_sn:
				sn = loc_to_sn[value]
				if self.get(companion) != sn:
					frappe.db.set_value(
						"Biometric Setting", self.name, companion, sn,
						update_modified=False,
					)

	def _ensure_biometric_user_parents(self):
		has_user = frappe.db.exists("DocType", "Biometric User")
		has_template = frappe.db.exists("DocType", "Biometric Template")
		if not has_user and not has_template:
			return

		ensure_user = ensure_template = None
		if has_user:
			from upande_ta.upande_ta.doctype.biometric_user.biometric_user import (
				_ensure_biometric_user_parent,
			)
			ensure_user = _ensure_biometric_user_parent
		if has_template:
			from upande_ta.upande_ta.doctype.biometric_template.biometric_template import (
				_ensure_biometric_template_parent,
			)
			ensure_template = _ensure_biometric_template_parent

		for d in (self.devices or []):
			if not d.device_sn:
				continue
			if ensure_user:
				try:
					ensure_user(d.device_sn)
				except Exception:
					frappe.log_error(
						title="Biometric User parent creation failed",
						message=frappe.get_traceback(),
					)
			if ensure_template:
				try:
					ensure_template(d.device_sn)
				except Exception:
					frappe.log_error(
						title="Biometric Template parent creation failed",
						message=frappe.get_traceback(),
					)

	def _sync_scheduled_jobs(self, force=False):
		for prefix, method, _label in SCHEDULER_TASKS:
			fields = (
			 f"{prefix}_event_frequency",
			 f"{prefix}_cron_format",
			 _PREFIX_ENABLE[prefix],
			)
			if not force and not any(self.has_value_changed(f) for f in fields):
				continue
			if force:
				try:
					self._upsert_scheduled_job(prefix, method)
				except Exception:
					frappe.log_error(
						title=f"Biometric Setting: failed to sync '{prefix}' scheduled job",
						message=frappe.get_traceback(),
					)
			else:
				self._upsert_scheduled_job(prefix, method)

	def _upsert_scheduled_job(self, prefix, method):
		frequency = (self.get(f"{prefix}_event_frequency") or "").strip()
		cron_format = (self.get(f"{prefix}_cron_format") or "").strip()
		enabled = bool(self.get(_PREFIX_ENABLE[prefix]))

		stopped = 1 if (not enabled or not frequency) else 0
		if frequency == "Cron" and not cron_format:
			stopped = 1

		effective_frequency = "Daily" if (frequency == "Cron" and not cron_format) else frequency

		new_frequency = effective_frequency or "Daily"
		new_cron = cron_format if new_frequency == "Cron" else ""

		job_name = frappe.db.get_value("Scheduled Job Type", {"method": method})

		if not job_name:
			job = frappe.new_doc("Scheduled Job Type")
			job.method = method
			event_name = _ensure_scheduler_event(method)
			if event_name:
				job.scheduler_event = event_name
			job.create_log = new_frequency not in ("All", "Cron")
			job.frequency = new_frequency
			job.cron_format = new_cron
			job.stopped = stopped
			job.insert(ignore_permissions=True)
			return

		current = frappe.db.get_value(
		 "Scheduled Job Type",
		 job_name,
		 ["frequency", "cron_format", "stopped", "scheduler_event"],
		 as_dict=True,
		)
		updates = {}
		if current.frequency != new_frequency:
			updates["frequency"] = new_frequency
		if (current.cron_format or "") != new_cron:
			updates["cron_format"] = new_cron
		if int(current.stopped or 0) != stopped:
			updates["stopped"] = stopped
		event_name = _ensure_scheduler_event(method)
		if event_name and (current.scheduler_event or "") != event_name:
			updates["scheduler_event"] = event_name

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

def _format_blocked_device_message(blocked):
	from urllib.parse import quote

	from frappe.utils import escape_html

	def link(doctype, name):
		safe_name = escape_html(name)
		href = f"/app/{doctype.lower().replace(' ', '-')}/{quote(name)}"
		return f'<a href="{href}" target="_blank">{safe_name}</a>'

	rows = []
	for sn, info in blocked.items():
		bullets = []
		for tpl_name in (info.get("templates") or []):
			bullets.append(f"<li>Biometric Template: {link('Biometric Template', tpl_name)}</li>")
		for usr_name in (info.get("users") or []):
			bullets.append(f"<li>Biometric User: {link('Biometric User', usr_name)}</li>")
		rows.append(
			f"<li><b>{escape_html(sn)}</b><ul>{''.join(bullets)}</ul></li>"
		)

	return (
		"The following device(s) still have Biometric User or Biometric Template records. "
		"Open each link, delete the linked rows, then remove the device again:"
		f"<ul>{''.join(rows)}</ul>"
	)


def _device_link_summary(device_sns):
	out = {}
	if not device_sns:
		return out

	if frappe.db.exists("DocType", "Biometric Template"):
		tpl_parents = frappe.get_all(
		 "Biometric Template",
		 filters={"device_sn": ("in", list(device_sns))},
		 fields=["name", "device_sn"],
		)
		tpl_by_parent = {p.name: p.device_sn for p in tpl_parents}
		if tpl_by_parent:
			tpl_rows = frappe.get_all(
			 "Bio Template",
			 filters={
				 "parenttype": "Biometric Template",
				 "parentfield": "bio_templates",
				 "parent": ("in", list(tpl_by_parent.keys())),
			 },
			 fields=["parent"],
			)
			for r in tpl_rows:
				sn = tpl_by_parent.get(r.parent)
				if not sn:
					continue
				out.setdefault(sn, {"templates": [], "users": []})
				if r.parent not in out[sn]["templates"]:
					out[sn]["templates"].append(r.parent)

	if frappe.db.exists("DocType", "Biometric User"):
		usr_parents = frappe.get_all(
		 "Biometric User",
		 filters={"device_sn": ("in", list(device_sns))},
		 fields=["name", "device_sn"],
		)
		usr_by_parent = {p.name: p.device_sn for p in usr_parents}
		if usr_by_parent:
			usr_rows = frappe.get_all(
			 "Bio User",
			 filters={
				 "parenttype": "Biometric User",
				 "parentfield": "users",
				 "parent": ("in", list(usr_by_parent.keys())),
			 },
			 fields=["parent"],
			)
			for r in usr_rows:
				sn = usr_by_parent.get(r.parent)
				if not sn:
					continue
				out.setdefault(sn, {"templates": [], "users": []})
				if r.parent not in out[sn]["users"]:
					out[sn]["users"].append(r.parent)

	return out

@frappe.whitelist()
def devices_with_templates(device_sns):
	import json
	if not device_sns:
		return {}
	if isinstance(device_sns, str):
		device_sns = json.loads(device_sns)
	return _device_link_summary(device_sns)

@frappe.whitelist()
def resync_scheduled_jobs():
	"""after_migrate hook: re-stamp/recreate the per-site biometric Scheduled Job
	Type rows. With each row linked to a Scheduler Event, sync_jobs no
	longer deletes them — this remains the path that backfills the marker on
	pre-existing rows and keeps frequency/cron in sync with Biometric Setting."""
	if not frappe.db.exists("DocType", "Biometric Setting"):
		return {"jobs": [], "skipped": True, "reason": "DocType not yet installed"}

	doc = frappe.get_single("Biometric Setting")
	doc._sync_scheduled_jobs(force=True)
	frappe.db.commit()
	return {
	 "jobs": frappe.get_all(
	  "Scheduled Job Type",
	  filters={"method": ["like", "%biometric_setting%"]},
	  fields=["method", "frequency", "cron_format", "stopped", "scheduler_event"],
	  order_by="method",
	 )
	}

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

def _resolve_device_sn(value, devices):
	if not value:
		return None
	value = (value or "").strip()
	for d in (devices or []):
		if d.device_sn == value:
			return d.device_sn
	for d in (devices or []):
		if (d.device_location or "") == value:
			return d.device_sn
	return None


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

			device_sn = _resolve_device_sn(row.device, doc.devices)
			if not device_sn:
				failed.append({"row": row.idx, "reason": f"Unknown device: {row.device}"})
				continue

			cmd_id = frappe.generate_hash(length=10)

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
	devices = []
	for row in (settings.poll_devices or []):
		if not row.device:
			continue
		sn = _resolve_device_sn(row.device, settings.devices)
		if sn:
			devices.append(sn)
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

	# Only ask a terminal for what it can actually hold. Polling a palm table
	# from a face reader just burns a command slot and a round trip, and the
	# reply is either empty or unstorable.
	caps = _device_capabilities(device_sn)

	queued = []
	skipped = []
	for table, label, cap_field in _BIODATA_QUERIES:
		if cap_field and not _device_supports(caps, cap_field):
			skipped.append(label)
			continue

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

	if skipped:
		frappe.logger().info(
			f"Biodata poll {device_sn}: skipped {', '.join(skipped)} "
			f"(not supported by this device)"
		)

	return {
		"device_sn": device_sn,
		"pin":       pin or None,
		"queued":    queued,
		"skipped":   skipped,
	}

# (table, label, capability flag that gates it). A None flag means always ask.
#
# USERINFO is never gated — it carries the user record itself (name, privilege,
# card, password, verify mode), which is needed whatever sensors the terminal
# has. BIODATA carries Type=8, so it is the palm query specifically.
#
# BIOPHOTO is gated on supports_face: the enrolment photo is only interesting
# on a face terminal, and it is the one artefact that CAN cross algorithm
# versions — a 5.6 reader can re-extract its own template from a photo taken by
# a 40.1 reader, which a template copy can never do. It lands as a File
# attached to the Bio Template row (bio_type "photo").
_BIODATA_QUERIES = [
 ("FINGERTMP", "Fingerprint", "supports_fingerprint"),
 ("FACE",      "Face",        "supports_face"),
 ("USERINFO",  "Password",    None),
 ("BIODATA",   "Palm",        "supports_palm"),
 ("BIOPHOTO",  "BioPhoto",    "supports_face"),
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

@frappe.whitelist()
def request_biodata_per_device(assignments):
	if isinstance(assignments, str):
		assignments = json.loads(assignments)
	if not assignments:
		frappe.throw("No device assignments provided")

	total_queued = 0
	results = []
	for entry in assignments:
		sn = (entry.get("device_sn") or "").strip()
		pins = entry.get("pins") or []
		if not sn or not pins:
			continue
		try:
			r = request_biodata(sn, pins=json.dumps(pins))
			n = len(r.get("queued") or [])
			total_queued += n
			results.append({"device_sn": sn, "queued": n, "pins": r.get("pins") or []})
		except Exception as e:
			results.append({"device_sn": sn, "error": str(e)})
	return {
		"status":    "queued",
		"queued":    total_queued,
		"by_device": results,
	}


@frappe.whitelist()
def request_biodata_multi(device_sns, pins=None):
	if isinstance(device_sns, str):
		device_sns = json.loads(device_sns)
	device_sns = [str(sn).strip() for sn in (device_sns or []) if str(sn).strip()]
	if not device_sns:
		frappe.throw("Select at least one device")

	results = []
	total_queued = 0
	for sn in device_sns:
		try:
			r = request_biodata(sn, pins=pins)
			n = len(r.get("queued") or [])
			total_queued += n
			results.append({"device_sn": sn, "queued": n, "pins": r.get("pins") or []})
		except Exception as e:
			results.append({"device_sn": sn, "error": str(e)})
	return {
		"status":    "queued",
		"device_sns": device_sns,
		"queued":    total_queued,
		"by_device": results,
	}


# Which Bio Template column proves a credential exists, per capability flag.
# All five live on the same Bio Template row, so one pass over a device's rows
# answers for every credential at once.
_CAPABILITY_EVIDENCE = {
	"supports_fingerprint": "fingerprint_template",
	"supports_face":        "face_template",
	"supports_palm":        "palm_template",
	"supports_card":        "card",
	"supports_password":    "password",
}

# A handful of rows is noise (a test enrolment, a default card of "0"), not
# proof that a terminal has the sensor. Require a real share of its roster.
_CAPABILITY_MIN_ROWS = 5
_CAPABILITY_MIN_SHARE = 0.02


def _capability_evidence(device_sn):
	"""Count, per credential, how many of this device's Bio Template rows carry
	one. Returns ``(total_rows, {flag: count})`` — ``(0, {})`` when the device
	has no rows at all."""
	parent_name = frappe.db.get_value("Biometric Template", {"device_sn": device_sn}, "name")
	if not parent_name:
		return 0, {}

	cols = []
	for flag, column in _CAPABILITY_EVIDENCE.items():
		# card/password are text fields where "0" is the device's own "unset".
		if column.endswith("_template"):
			cond = f"`{column}` IS NOT NULL AND `{column}` <> ''"
		else:
			cond = f"COALESCE(`{column}`, '') NOT IN ('', '0')"
		cols.append(f"SUM({cond}) AS `{flag}`")

	row = frappe.db.sql(
		f"""
		SELECT COUNT(*) AS total, {', '.join(cols)}
		  FROM `tabBio Template`
		 WHERE parent = %s AND parentfield = 'bio_templates'
		""",
		(parent_name,),
		as_dict=True,
	)
	if not row:
		return 0, {}
	total = int(row[0].get("total") or 0)
	counts = {flag: int(row[0].get(flag) or 0) for flag in _CAPABILITY_EVIDENCE}
	return total, counts


@frappe.whitelist()
def detect_device_capabilities(device_sn=None, apply=0):
	"""Map each device's capability flags from the templates it has delivered.

	All five credentials share the Bio Template row, so what a terminal has
	actually uploaded is the best evidence of what it can store. Two rules keep
	this from disabling a working device:

	* a flag is only turned **on** when the credential appears on at least
	  ``_CAPABILITY_MIN_ROWS`` rows **and** 2% of the device's roster — one
	  stray card number is noise, not a card reader;
	* nothing is turned **off** for a device that has delivered **no templates
	  at all**. A device with an empty Biometric Template cannot be told apart
	  from one whose uploads are failing upstream, and switching its flags off
	  would quietly stop every future push.

	Dry run by default: pass ``apply=1`` to write. Returns one entry per device
	with the evidence counts and the flags that would change.
	"""
	frappe.only_for(("System Manager", "HR Manager"))
	apply = int(apply or 0)

	settings = frappe.get_single("Biometric Setting")
	rows = [
		d for d in (settings.devices or [])
		if d.device_sn and (not device_sn or d.device_sn == device_sn)
	]
	if not rows:
		frappe.throw("No matching device in Biometric Setting")

	report = []
	changed_total = 0
	for d in rows:
		total, counts = _capability_evidence(d.device_sn)
		entry = {
			"device_sn":       d.device_sn,
			"device_location": d.device_location or d.device_sn,
			"template_rows":   total,
			"evidence":        counts,
			"changes":         {},
			"skipped":         "",
		}

		has_any_template = any(
			counts.get(flag, 0)
			for flag, column in _CAPABILITY_EVIDENCE.items()
			if column.endswith("_template")
		)
		if not has_any_template:
			entry["skipped"] = (
				"no biometric template has ever arrived from this device — "
				"cannot tell a missing sensor from a broken upload, flags left as they are"
			)
			report.append(entry)
			continue

		threshold = max(_CAPABILITY_MIN_ROWS, int(total * _CAPABILITY_MIN_SHARE))
		for flag in _CAPABILITY_EVIDENCE:
			wanted = 1 if counts.get(flag, 0) >= threshold else 0
			if int(d.get(flag) or 0) != wanted:
				entry["changes"][flag] = wanted

		if entry["changes"] and apply:
			for flag, wanted in entry["changes"].items():
				frappe.db.set_value(
					"Biometric Device", d.name, flag, wanted, update_modified=False
				)
				d.set(flag, wanted)
		changed_total += len(entry["changes"])
		report.append(entry)

	if apply and changed_total:
		frappe.db.commit()
		frappe.clear_cache(doctype="Biometric Setting")

	return {
		"applied":  bool(apply),
		"devices":  len(report),
		"changes":  changed_total,
		"report":   report,
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

	server_now = frappe.utils.now_datetime()
	if last_seen:
		try:
			last_seen = frappe.utils.get_datetime(last_seen)
		except Exception:
			last_seen = server_now
	else:
		last_seen = server_now
	if last_seen > server_now:
		last_seen = server_now
	last_seen = last_seen.strftime("%Y-%m-%d %H:%M:%S")

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


@frappe.whitelist()
def get_device_statuses():
	stale = mark_stale_devices_offline()
	if stale:
		frappe.db.commit()
		_publish_device_status_update({"updated": [], "offline": stale})

	rows = frappe.get_all(
		"Biometric Device",
		filters={"parenttype": "Biometric Setting", "parentfield": "devices"},
		fields=["name", "device_sn", "status", "last_seen"],
	)
	return [
		{
			"row_name":  r.name,
			"device_sn": r.device_sn,
			"status":    r.status or "Offline",
			"last_seen": str(r.last_seen) if r.last_seen else None,
		}
		for r in rows
	]


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


def run_absent_marking():
	"""Mark Absent for shift windows that have closed with no check-in logs.

	Runs off the wall clock and the grace period configured on this doctype, not
	off `Shift Type.last_sync_of_checkin` — HRMS's own pass waits a full day
	past the shift and stalls entirely when the biometric poll stalls. See
	upande_ta/upande_ta/api/absent_marking.py.
	"""
	from upande_ta.upande_ta.api.absent_marking import run_absent_marking as _run

	return _run()


@frappe.whitelist()
def mark_absentees_now(
	from_date: str | None = None,
	to_date: str | None = None,
	dry_run: str | int | bool = 0,
):
	"""Attendance tab button: run the pass now, or preview it (dry_run=1)."""
	from upande_ta.upande_ta.api.absent_marking import mark_absentees_now as _mark

	return _mark(from_date=from_date, to_date=to_date, dry_run=dry_run)


def run_flip_last_in():
	settings = frappe.get_single("Biometric Setting")
	if not settings.enable_flip:
		return {"skipped": True, "reason": "enable_flip is off"}
	from upande_ta.upande_ta.overrides.employee_checkin import normalize_checkin_directions

	# How far back this run re-scans, from Biometric Setting. The function
	# itself falls back to one day when both are unset, so an existing site
	# that has never filled these in does not suddenly scan nothing.
	return normalize_checkin_directions(
		days=int(settings.flip_lookback_days or 0),
		hours=int(settings.flip_lookback_hours or 0),
	)


@frappe.whitelist()
def flip_checkins_for_date(date=None):
	"""Manually normalize check-in directions for a single date (the Checkin
	tab's Update button), scoped to each employee's assigned shift window.

	A day whose last scan is an IN gets that scan flipped to OUT; a day with no
	IN at all — every scan stamped OUT by the reader — gets its earliest scan
	flipped to IN. Middle scans are left intact. Defaults to today."""
	frappe.only_for(("System Manager", "HR Manager"))
	from upande_ta.upande_ta.overrides.employee_checkin import normalize_checkin_directions

	day = getdate(date) if date else getdate(nowdate())
	return normalize_checkin_directions(target_date=day, days=1)
	
