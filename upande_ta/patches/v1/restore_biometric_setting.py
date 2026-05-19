import json
import os

import frappe


_SNAPSHOT_PATH = os.path.join(
	os.path.dirname(__file__), "data", "biometric_setting_snapshot.json"
)

_OPT_IN_ENV_VAR = "UPANDE_TA_RESTORE_BIOMETRIC_SNAPSHOT"


def execute():
	"""Restore the canonical Biometric Setting snapshot on an empty site.

	This snapshot reflects ONE specific deployment's hardware (server IP, device
	serial numbers). Restoring it onto a different site with different hardware
	would seed bogus values. Therefore the patch is OFF by default and runs only
	when the site explicitly opts in by setting the env var:

	    UPANDE_TA_RESTORE_BIOMETRIC_SNAPSHOT=1 bench --site <site> migrate

	Even with the opt-in, the patch refuses to overwrite a site that already has
	server_ip or any device rows configured.
	"""
	if not frappe.db.exists("DocType", "Biometric Setting"):
		return

	if os.environ.get(_OPT_IN_ENV_VAR) not in ("1", "true", "yes", "True"):
		print(
			"[restore_biometric_setting] skipped (opt-in only; set "
			f"{_OPT_IN_ENV_VAR}=1 to enable)"
		)
		return

	doc = frappe.get_single("Biometric Setting")

	already_configured = bool((doc.server_ip or "").strip()) or bool(doc.devices)
	if already_configured:
		print("[restore_biometric_setting] target site already configured — skipping")
		return

	if not os.path.exists(_SNAPSHOT_PATH):
		print(f"[restore_biometric_setting] snapshot missing: {_SNAPSHOT_PATH}")
		return

	with open(_SNAPSHOT_PATH) as f:
		snap = json.load(f)

	for field, value in snap.items():
		if field == "devices":
			continue
		if doc.meta.has_field(field):
			doc.set(field, value)

	doc.set("devices", [])
	for d in (snap.get("devices") or []):
		if d.get("device_sn"):
			doc.append("devices", {
				"device_sn":       d["device_sn"],
				"device_location": d.get("device_location") or "",
			})

	doc.save(ignore_permissions=True)
	frappe.db.commit()
	print(
		f"[restore_biometric_setting] restored snapshot: "
		f"{len(snap.get('devices') or [])} device(s)"
	)
