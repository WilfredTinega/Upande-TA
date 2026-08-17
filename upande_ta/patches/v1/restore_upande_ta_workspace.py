# Copyright (c) 2026, Upande LTD and contributors
"""Clear the leftover "T&A" Workspace row once "Upande TA" is back.

The workspace itself is re-created by model sync from
upande_ta/upande_ta/workspace/upande_ta/upande_ta.json -- this patch must NOT
create it. A DB row with no matching file on disk is exactly what migrate's
orphan sweep deletes, which is the bug being fixed.

Registered under [post_model_sync] so the file-synced workspace already exists
by the time this runs.
"""

import frappe

NEW = "Upande TA"


def execute():
	if not frappe.db.table_exists("Workspace"):
		return
	if not (frappe.db.exists("Workspace", "T&A") and frappe.db.exists("Workspace", NEW)):
		return

	prev_in_migrate = frappe.flags.in_migrate
	# overrides/workspace.py protects the nav records outside migrate.
	frappe.flags.in_migrate = True
	try:
		frappe.delete_doc(
			"Workspace", "T&A", ignore_permissions=True, force=True, ignore_missing=True
		)
	finally:
		frappe.flags.in_migrate = prev_in_migrate

	frappe.db.commit()
