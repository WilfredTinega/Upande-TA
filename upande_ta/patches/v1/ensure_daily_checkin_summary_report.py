import json
import os

import frappe
from frappe.modules.import_file import import_file_by_path


def execute():
	_sync_report()
	_sync_custom_block()
	_sync_workspace()
	_refresh_apps_screen()


def _refresh_apps_screen():
	try:
		ia = frappe.get_doc("Installed Applications")
		ia.update_versions()
		ia.save(ignore_permissions=True)
		frappe.client_cache.delete_value("doc::Installed Applications::Installed Applications")
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ensure_daily_checkin_summary_report")


def _sync_report():
	path = frappe.get_app_path(
		"upande_ta",
		"upande_ta",
		"report",
		"daily_checkin_summary",
		"daily_checkin_summary.json",
	)
	if not os.path.exists(path):
		return
	try:
		import_file_by_path(path, force=True, ignore_version=True, reset_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ensure_daily_checkin_summary_report")


def _sync_custom_block():
	path = frappe.get_app_path(
		"upande_ta",
		"upande_ta",
		"custom_html_block",
		"ta_dashboard",
		"ta_dashboard.json",
	)
	if not os.path.exists(path):
		return
	try:
		with open(path) as f:
			payload = json.load(f)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ensure_daily_checkin_summary_report")
		return

	name = payload.get("name")
	if not name or payload.get("doctype") != "Custom HTML Block":
		return

	fields = {
		"html": payload.get("html") or "",
		"script": payload.get("script") or "",
		"style": payload.get("style") or "",
		"private": int(payload.get("private") or 0),
	}

	try:
		if frappe.db.exists("Custom HTML Block", name):
			doc = frappe.get_doc("Custom HTML Block", name)
			changed = False
			for k, v in fields.items():
				if doc.get(k) != v:
					doc.set(k, v)
					changed = True
			if changed:
				doc.flags.ignore_permissions = True
				doc.save()
		else:
			doc = frappe.new_doc("Custom HTML Block")
			doc.update({"name": name, **fields})
			doc.flags.ignore_permissions = True
			doc.insert()
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ensure_daily_checkin_summary_report")


def _sync_workspace():
	if not frappe.db.exists("Workspace", "T&A"):
		return
	path = frappe.get_app_path(
		"upande_ta",
		"upande_ta",
		"workspace",
		"t_and_a",
		"t_and_a.json",
	)
	if not os.path.exists(path):
		return
	try:
		import_file_by_path(path, force=True, ignore_version=True, reset_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ensure_daily_checkin_summary_report")
