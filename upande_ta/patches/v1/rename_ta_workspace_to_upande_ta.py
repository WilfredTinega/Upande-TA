# Copyright (c) 2026, Upande LTD and contributors
"""One-time rename of this app's nav from "T&A" to "Upande TA".

The app ships exactly one Workspace. It used to be called "T&A" while the
launcher tile and the Desk sidebar around it read "Upande TA", so the desk
showed a redundant "T&A" entry nested inside an "Upande TA" folder. The shipped
JSON is now "Upande TA" throughout; this clears the old name off sites that
still carry it.

Deliberately NOT named `rename_ta_to_upande_ta`: a patch of that exact dotted
path already shipped on a sibling branch and is recorded in the Patch Log of
every site that ever ran it. Frappe never runs a logged name twice, so reusing
it would have made this file dead code on those sites while looking installed.

Renaming rather than deleting keeps whatever the site added to the workspace:
the card layout, any custom blocks and the shortcuts are all children of the
record, and a delete would take them with it.

The Desktop Icon is deleted rather than renamed. It carries no site-specific
content worth preserving, and `install.ensure_desktop_icon()` writes the
replacement on the same migrate.

Note: `frappe.rename_doc()` takes no `ignore_permissions` kwarg. The top-level
wrapper in frappe/__init__.py declares a narrower, keyword-only signature than
frappe.model.rename_doc.rename_doc, so passing it raises TypeError.
Administrator is the session user during migrate, so dropping it costs nothing.
"""

import frappe

OLD = "T&A"
NEW = "Upande TA"


def execute():
	# The protective on_trash in overrides/workspace.py allows deletion while
	# in_migrate is set; force it on so this also works under `bench run-patch`.
	prev_in_migrate = frappe.flags.in_migrate
	frappe.flags.in_migrate = True
	try:
		if frappe.db.table_exists("Workspace") and frappe.db.exists("Workspace", OLD):
			if frappe.db.exists("Workspace", NEW):
				# Both names present: the new one is already the live record, so
				# the leftover is just clutter.
				frappe.delete_doc(
					"Workspace", OLD, ignore_permissions=True, force=True, ignore_missing=True
				)
			else:
				frappe.rename_doc("Workspace", OLD, NEW, force=True)

			# `Workspace` is autonamed `field:label`, so the rename moves `name`
			# and leaves `label`/`title` reading "T&A" -- which is the text the
			# desk actually renders. Set them here rather than relying on the
			# shipped JSON to land: model sync skips a file whose timestamp is
			# not newer than the record it is importing over.
			if frappe.db.exists("Workspace", NEW):
				frappe.db.set_value(
					"Workspace", NEW, {"label": NEW, "title": NEW}, update_modified=False
				)

		# Older releases shipped the launcher under the old name, and one release
		# shipped it twice. Drop every stale tile; ensure_desktop_icon() recreates
		# the single current one.
		if frappe.db.exists("DocType", "Desktop Icon"):
			for stale in (OLD, "Upande T&A"):
				if frappe.db.exists("Desktop Icon", stale):
					frappe.delete_doc(
						"Desktop Icon", stale, ignore_permissions=True, force=True, ignore_missing=True
					)
	finally:
		frappe.flags.in_migrate = prev_in_migrate

	frappe.db.commit()
