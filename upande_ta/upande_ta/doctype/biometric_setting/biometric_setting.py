# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from upande_ta.upande_ta.doctype.biometric_user.biometric_user import _post_to_nodered


SCHEDULER_TASKS = [
	("checkin", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_checkin",      "Biometric: Poll Attendance"),
	("users",   "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_users_sync",   "Biometric: Sync Users"),
	("biodata", "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.run_biodata_sync", "Biometric: Sync BioData"),
]

_TASK_BY_PREFIX = {prefix: (method, label) for prefix, method, label in SCHEDULER_TASKS}

_PREFIX_ENABLE = {
	"checkin": "enable_checkin",
	"users":   "enable_users",
	"biodata": "enable_bio_templates",
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
	def on_update(self):
		self._sync_scheduled_jobs()

	def _sync_scheduled_jobs(self, force=False):
		"""Mirror the user's frequency/cron/enabled choices into Scheduled Job Type rows.

		One Scheduled Job Type per task, keyed by `method`. Same pattern Frappe's
		Server Script uses (see core/doctype/server_script/server_script.py).

		Per-task short-circuit: if none of (frequency, cron, enabled) changed for a
		task on this save, skip it entirely.

		Pass force=True to upsert all tasks regardless (used by after_migrate, since
		Frappe's sync_jobs deletes Scheduled Job Type rows whose method isn't
		declared in any app's scheduler_events).
		"""
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

		# Frappe parses cron_format unconditionally even for stopped rows, and a
		# Cron-frequency row with empty cron_format crashes sync_jobs. Downgrade
		# to Daily placeholder when there's no usable cron string.
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

		new_frequency = effective_frequency or "Daily"  # placeholder for stopped rows (required field)
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
def resync_scheduled_jobs():
	"""Re-upsert all Biometric Setting-driven scheduled jobs from current settings.

	Wired into hooks.after_migrate because Frappe's sync_jobs deletes any
	Scheduled Job Type whose method isn't declared in scheduler_events.
	"""
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


def _window_for(prefix):
	"""Compute (start_dt, end_dt) for a scheduled run of `prefix` based on its
	configured frequency. End is now; start is now - frequency_delta.
	"""
	doc = frappe.get_single("Biometric Setting")
	freq = (doc.get(f"{prefix}_event_frequency") or "").strip()
	delta = _FREQUENCY_WINDOWS.get(freq, timedelta(hours=1))
	end = now_datetime()
	start = end - delta
	return start, end


def _poll_attendance(start_dt, end_dt, devices):
	"""Send one ATTLOG poll per device for [start_dt, end_dt]."""
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
	"""Scheduled run: poll attendance for the window implied by checkin_event_frequency."""
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
	"""Scheduled run: refresh enrolled users from each device via DATA QUERY USERINFO."""
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


def run_biodata_sync():
	"""Scheduled run: poll all biodata tables for every device."""
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
	"""Same body as request_biodata, without the enable check / whitelist guard.
	Used by run_biodata_sync (which has already gated on enable_bio_templates).
	"""
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
def request_biodata(device_sn, pin=None):
	if not frappe.db.get_single_value("Biometric Setting", "enable_bio_templates"):
		frappe.throw("Enable Bio Templates")
	if not device_sn:
		frappe.throw("Please select a device first")
	out = request_biodata_internal(device_sn, pin)
	out["status"] = "queued"
	return out


_USER_FIELDS = {
	"card":           "card",
	"vice_card":      "vice_card",
	"password":       "password",
	"privilege":      "privilege",
	"group":          "user_group",
	"timezone_group": "timezone_group",
	"verify_mode":    "verify_mode",
	"start_datetime": "start_datetime",
	"end_datetime":   "end_datetime",
}

_BIO_PREFIX = {
	"fingerprint": "fp",
	"face":        "face",
	"palm":        "palm",
}

_BIO_TEMPLATE_FIELD = {
	"fp":   "fingerprint_template",
	"face": "face_template",
	"palm": "palm_template",
}


def _str(v):
	return "" if v is None else str(v).strip()


def _int(v, default=0):
	try:
		return int(v) if v not in (None, "") else default
	except (TypeError, ValueError):
		return default


@frappe.whitelist(allow_guest=True)
def store_biotemplate():
	data      = frappe.request.get_json() or {}
	bio_type  = _str(data.get("bio_type"))
	device_sn = _str(data.get("device_sn") or data.get("source_device"))
	user_id   = _str(data.get("user_id") or data.get("employee_id") or data.get("employee"))

	if not user_id:
		frappe.response["http_status_code"] = 400
		frappe.response["message"] = {"status": "error", "error": "Missing user_id"}
		return

	kind = bio_type.lower()
	is_user_record = kind == "user"
	prefix = _BIO_PREFIX.get(kind)

	if not is_user_record and not prefix:
		frappe.response["http_status_code"] = 400
		frappe.response["message"] = {
			"status": "error",
			"error":  f"Unsupported bio_type: {bio_type!r}",
		}
		return

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

	new_values = {"source_device": device_sn}

	if is_user_record:
		for src, dst in _USER_FIELDS.items():
			new_values[dst] = _str(data.get(src))
	else:
		new_values.update({
			f"{prefix}_bio_no":    _int(data.get("bio_no")),
			f"{prefix}_bio_index": _int(data.get("bio_index")),
			f"{prefix}_valid":     _int(data.get("valid"), 1),
			f"{prefix}_major_ver": _int(data.get("major_ver")),
			f"{prefix}_minor_ver": _int(data.get("minor_ver")),
			f"{prefix}_size":      _int(data.get("size")),
			f"{prefix}_raw_log":   _str(data.get("raw_log")),
			_BIO_TEMPLATE_FIELD[prefix]: _str(data.get("template")),
		})

	existing = frappe.db.get_value(
		"Biometric Template",
		{
			"parent":      "Biometric Setting",
			"parentfield": "bio_templates",
			"employee":    employee_name,
		},
		("name",) + tuple(new_values),
		as_dict=True,
	)

	if existing:
		changed = {k: v for k, v in new_values.items() if existing.get(k) != v}
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
			"doctype":     "Biometric Template",
			"name":        row_name,
			"parent":      "Biometric Setting",
			"parenttype":  "Biometric Setting",
			"parentfield": "bio_templates",
			"idx":         idx,
			"employee":    employee_name,
			"user_id":     user_id,
			"captured_at": now,
			**new_values,
		})
		row.db_insert()
		frappe.db.commit()
		status = "inserted"

	frappe.response["message"] = {
		"status":   status,
		"employee": employee_name,
		"user_id":  user_id,
		"bio_type": bio_type,
		"row_name": row_name,
	}
