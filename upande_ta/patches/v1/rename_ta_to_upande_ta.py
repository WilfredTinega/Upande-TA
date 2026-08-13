# Copyright (c) 2026, Upande LTD and contributors
"""One-time rename of the user-facing nav from "T&A" to "Upande TA".

Covers the three records that make up the launcher chain:
Desktop Icon -> Workspace Sidebar -> Workspace.

The app no longer ships or re-creates any of these on install/migrate -- they
are plain site records now, owned by the database. This patch only cleans up
the old names on sites that still carry them; it never re-creates anything.
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
				frappe.delete_doc("Workspace", "T&A", ignore_permissions=True, force=True)
			else:
				frappe.rename_doc("Workspace", "T&A", NEW, force=True, ignore_permissions=True)

		# Workspace Sidebar: "Upande T&A" -> "Upande TA"
		if frappe.db.exists("DocType", "Workspace Sidebar") and frappe.db.exists(
			"Workspace Sidebar", OLD
		):
			if frappe.db.exists("Workspace Sidebar", NEW):
				frappe.delete_doc("Workspace Sidebar", OLD, ignore_permissions=True, force=True)
			else:
				frappe.rename_doc("Workspace Sidebar", OLD, NEW, force=True, ignore_permissions=True)

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
					frappe.delete_doc("Desktop Icon", stale, ignore_permissions=True, force=True)
			if frappe.db.exists("Desktop Icon", NEW):
				frappe.db.set_value("Desktop Icon", NEW, {"link_to": NEW, "sidebar": NEW})
	finally:
		frappe.flags.in_migrate = prev_in_migrate

	frappe.db.commit()
