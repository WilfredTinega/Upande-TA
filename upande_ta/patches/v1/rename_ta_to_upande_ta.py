# Copyright (c) 2026, Upande LTD and contributors
"""One-time rename of the user-facing nav from "T&A" to "Upande TA".

Covers the three records that make up the launcher chain:
Desktop Icon -> Workspace Sidebar -> Workspace.

The Workspace itself is shipped as a file again (see
upande_ta/upande_ta/workspace/upande_ta/upande_ta.json) because migrate deletes
public workspaces that have no matching JSON in any installed app. This patch
only clears the old names off sites that still carry them; the file is what
re-creates the record during model sync.

Note: frappe.rename_doc() takes no ``ignore_permissions`` kwarg -- the
top-level wrapper in frappe/__init__.py declares a narrower, keyword-only
signature than frappe.model.rename_doc.rename_doc. Passing it raises
TypeError. Administrator is the session user during migrate, so dropping it
costs nothing.
"""

import frappe

OLD = "Upande T&A"
NEW = "Upande TA"


def execute():
	prev_in_migrate = frappe.flags.in_migrate
	# The protective on_trash in overrides/workspace.py allows deletion while
	# in_migrate is set; force it on so this also works under `bench run-patch`.
	frappe.flags.in_migrate = True
	try:
		# Workspace: "T&A" -> "Upande TA"
		if frappe.db.table_exists("Workspace") and frappe.db.exists("Workspace", "T&A"):
			if frappe.db.exists("Workspace", NEW):
				frappe.delete_doc(
					"Workspace", "T&A", ignore_permissions=True, force=True, ignore_missing=True
				)
			else:
				frappe.rename_doc("Workspace", "T&A", NEW, force=True)

		# Workspace Sidebar: "Upande T&A" -> "Upande TA"
		if frappe.db.exists("DocType", "Workspace Sidebar") and frappe.db.exists(
			"Workspace Sidebar", OLD
		):
			if frappe.db.exists("Workspace Sidebar", NEW):
				frappe.delete_doc(
					"Workspace Sidebar", OLD, ignore_permissions=True, force=True, ignore_missing=True
				)
			else:
				frappe.rename_doc("Workspace Sidebar", OLD, NEW, force=True)

		# Sidebar items pointing at the old workspace name.
		if frappe.db.exists("DocType", "Workspace Sidebar") and frappe.db.exists(
			"Workspace Sidebar", NEW
		):
			sidebar = frappe.get_doc("Workspace Sidebar", NEW)
			dirty = False
			if sidebar.title != NEW:
				sidebar.title = NEW
				dirty = True
			for item in sidebar.items:
				if item.link_type == "Workspace" and item.link_to in (OLD, "T&A"):
					item.link_to = NEW
					dirty = True
			if dirty:
				sidebar.save(ignore_permissions=True)

		# Desktop Icon: drop the old launcher, point the new one at the sidebar.
		if frappe.db.exists("DocType", "Desktop Icon"):
			for stale in ("T&A", OLD):
				if frappe.db.exists("Desktop Icon", stale):
					frappe.delete_doc(
						"Desktop Icon", stale, ignore_permissions=True, force=True, ignore_missing=True
					)
			if frappe.db.exists("Desktop Icon", NEW):
				frappe.db.set_value("Desktop Icon", NEW, {"link_to": NEW, "sidebar": NEW})
	finally:
		frappe.flags.in_migrate = prev_in_migrate

	frappe.db.commit()
