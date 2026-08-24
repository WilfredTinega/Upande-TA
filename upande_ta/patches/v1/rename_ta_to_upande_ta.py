# Copyright (c) 2026, Upande LTD and contributors
"""One-time rename of the user-facing nav from "T&A" to "Upande TA".

Covers the three records that make up the launcher chain:
Desktop Icon -> Workspace Sidebar -> Workspace.

The app no longer ships or re-creates any of these on install/migrate -- they
are plain site records now, owned by the database. This patch only cleans up
the old names on sites that still carry them; it never re-creates anything.

Nothing here is required for the app to work, so a fresh site (or any site
without the legacy records) is a no-op, and an unexpected state is reported
rather than allowed to abort the whole migration.
"""

import frappe

# frappe.rename_doc is the whitelisted wrapper and does not expose
# ignore_permissions; the model-level helper does.
from frappe.model.rename_doc import rename_doc

NEW = "Upande TA"
# Both spellings have shipped over time, on all three doctypes.
STALE = ("Upande T&A", "T&A")
# All three are autonamed `field:<x>`, so a rename has to carry the naming
# field with it or the next save renames the record straight back.
NAME_FIELD = {
	"Workspace": "label",
	"Workspace Sidebar": "title",
	"Desktop Icon": "label",
}


def _retire(doctype, old):
	"""Rename `old` to NEW, or drop it if NEW already exists."""
	if not frappe.db.exists(doctype, old):
		return
	if frappe.db.exists(doctype, NEW):
		frappe.delete_doc(doctype, old, ignore_permissions=True, force=True)
		return

	rename_doc(doctype, old, NEW, force=True, ignore_permissions=True)
	frappe.db.set_value(doctype, NEW, NAME_FIELD[doctype], NEW, update_modified=False)


def _detach_workspace():
	"""Keep the Workspace out of migrate's orphan sweep.

	frappe.model.sync.remove_orphan_entities deletes any workspace matching
	`public=1 and module is set and app is set` that no installed app ships as
	a file -- which is precisely this one, since the app deliberately stopped
	shipping it. Clearing `module` is the only lever: Workspace.validate
	re-derives `app` from `module` on every save, so this goes through the db
	directly. Runs in pre_model_sync, i.e. before the sweep in the same
	migration.
	"""
	if not frappe.db.exists("Workspace", NEW):
		return
	current = frappe.db.get_value("Workspace", NEW, ["module", "app"], as_dict=True)
	if current.module or current.app:
		frappe.db.set_value("Workspace", NEW, {"module": "", "app": ""}, update_modified=False)


def execute():
	prev_in_migrate = frappe.flags.in_migrate
	# The protective on_trash in overrides/workspace.py allows deletion while
	# in_migrate is set; force it on so this also works under `bench run-patch`.
	frappe.flags.in_migrate = True
	try:
		for doctype in ("Workspace", "Workspace Sidebar", "Desktop Icon"):
			if not frappe.db.exists("DocType", doctype):
				continue
			for old in STALE:
				try:
					_retire(doctype, old)
				except Exception:
					# Cosmetic nav cleanup must never break a migration.
					frappe.log_error(title=f"rename_ta_to_upande_ta: {doctype} {old}")
					print(f"  skipped {doctype} '{old}' -- see Error Log")

		# Sidebar items pointing at an old workspace name.
		if frappe.db.exists("DocType", "Workspace Sidebar") and frappe.db.exists(
			"Workspace Sidebar", NEW
		):
			sidebar = frappe.get_doc("Workspace Sidebar", NEW)
			dirty = False
			if sidebar.title != NEW:
				sidebar.title = NEW
				dirty = True
			for item in sidebar.items:
				if item.link_type == "Workspace" and item.link_to in STALE:
					item.link_to = NEW
					dirty = True
			if dirty:
				sidebar.save(ignore_permissions=True)

		# Point the surviving launcher at the renamed sidebar.
		if frappe.db.exists("DocType", "Desktop Icon") and frappe.db.exists("Desktop Icon", NEW):
			frappe.db.set_value("Desktop Icon", NEW, {"link_to": NEW, "sidebar": NEW})

		if frappe.db.table_exists("Workspace"):
			_detach_workspace()
	finally:
		frappe.flags.in_migrate = prev_in_migrate

	frappe.db.commit()
