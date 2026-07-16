# Copyright (c) 2026, Upande LTD and contributors

import frappe


MODULE = "Upande TA"


def execute():
	if not frappe.db.table_exists("Workspace"):
		return

	# Delete every Upande-TA-owned workspace. The protective on_trash in
	# overrides/workspace.py allows deletion when in_migrate is set; force it on
	# so this also works under `bench run-patch` (outside a full migrate).
	prev_in_migrate = frappe.flags.in_migrate
	frappe.flags.in_migrate = True
	try:
		for name in frappe.get_all("Workspace", filters={"module": MODULE}, pluck="name"):
			frappe.delete_doc("Workspace", name, ignore_permissions=True, force=True)
	finally:
		frappe.flags.in_migrate = prev_in_migrate

	# Re-create the workspace afresh from the current card definition on disk.
	frappe.reload_doc("upande_ta", "workspace", "t&a", force=True)

	frappe.db.commit()
