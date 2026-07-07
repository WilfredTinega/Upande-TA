# Copyright (c) 2026, Upande LTD and contributors

import json
import os

import frappe

MODULE = "Upande TA"

_SWEEP = [
	("Report", {"is_standard": "Yes"}),
	("Page", {"standard": "Yes"}),
	("Print Format", {"standard": "Yes"}),
	("Notification", {"is_standard": 1}),
	("Dashboard", {"is_standard": 1}),
	("Dashboard Chart", {"is_standard": 1}),
	("Number Card", {"is_standard": 1}),
	("Custom HTML Block", {}),
	("Workspace", {"for_user": ""}),
]


def _shipped_names(module_path, doctype):
	folder = os.path.join(module_path, frappe.scrub(doctype))
	names = set()
	if not os.path.isdir(folder):
		return names
	for entry in os.listdir(folder):
		sub = os.path.join(folder, entry)
		if not os.path.isdir(sub):
			continue
		candidates = [f for f in os.listdir(sub) if f.endswith(".json")]
		jf = os.path.join(sub, entry + ".json")
		if not os.path.exists(jf):
			if len(candidates) != 1:
				continue
			jf = os.path.join(sub, candidates[0])
		try:
			with open(jf, encoding="utf-8") as f:
				data = json.load(f)
		except Exception:
			continue
		if data.get("name"):
			names.add(data["name"])
	return names


def remove_orphans():
	try:
		module_path = frappe.get_module_path(MODULE)
	except Exception:
		return {"removed": [], "orphan_doctypes": []}

	removed = []
	for doctype, std_filter in _SWEEP:
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		if not meta.has_field("module"):
			continue

		filters = {"module": MODULE}
		for key, val in std_filter.items():
			if meta.has_field(key):
				filters[key] = val

		shipped = _shipped_names(module_path, doctype)
		try:
			db_names = frappe.get_all(doctype, filters=filters, pluck="name")
		except Exception:
			continue

		for name in db_names:
			if name in shipped:
				continue
			try:
				frappe.delete_doc(
					doctype, name, ignore_permissions=True, force=True, ignore_missing=True
				)
				removed.append(f"{doctype}: {name}")
			except Exception:
				frappe.log_error(
					frappe.get_traceback(), f"upande_ta orphan cleanup: {doctype} {name}"
				)

	orphan_doctypes = []
	dt_folder = os.path.join(module_path, "doctype")
	shipped_dt = (
		{d for d in os.listdir(dt_folder) if os.path.isdir(os.path.join(dt_folder, d))}
		if os.path.isdir(dt_folder)
		else set()
	)
	for name in frappe.get_all("DocType", filters={"module": MODULE, "custom": 0}, pluck="name"):
		if frappe.scrub(name) not in shipped_dt:
			orphan_doctypes.append(name)
	if orphan_doctypes:
		frappe.log_error(
			"Orphan DocTypes in DB for module Upande TA with no source folder. "
			"Review and delete manually if intended (auto-delete is skipped because "
			"it drops the table):\n" + "\n".join(orphan_doctypes),
			"upande_ta orphan DocTypes",
		)

	if removed:
		frappe.db.commit()
		frappe.logger().info("upande_ta orphan cleanup removed: " + "; ".join(removed))
	return {"removed": removed, "orphan_doctypes": orphan_doctypes}
