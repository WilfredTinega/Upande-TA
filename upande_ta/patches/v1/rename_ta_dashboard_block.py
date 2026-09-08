# Copyright (c) 2026, Upande LTD and contributors
"""Rename the shipped dashboard block from "T&A Dashboard" to "Upande TA Dashboard".

The last piece of the old "T&A" naming. Kept out of
``rename_ta_to_upande_ta`` deliberately: that patch may already be recorded in
a site's Patch Log, and an entry that has run never runs again, so extending it
would silently skip every site that had already migrated the workspace.

A Custom HTML Block is referenced by *name*, and a rename does not carry the
references with it. A workspace points at the block twice -- once in the
``content`` blob the desk renders from, and once in the ``custom_blocks`` child
table -- so both are rewritten here for every workspace on the site, not just
the one this app ships. Missing either leaves the block rendering as
"undefined", which is the exact failure ``install.ensure_ta_dashboard_block``
exists to prevent.
"""

import frappe

OLD = "T&A Dashboard"
NEW = "Upande TA Dashboard"


def execute():
	if not frappe.db.table_exists("Custom HTML Block"):
		return

	if frappe.db.exists("Custom HTML Block", OLD):
		if frappe.db.exists("Custom HTML Block", NEW):
			# Both present: the new record is already the live one, so the old
			# name is just clutter. install.ensure_ta_dashboard_block() keeps
			# the survivor's html/script in step with the shipped file.
			frappe.delete_doc(
				"Custom HTML Block", OLD, ignore_permissions=True, force=True, ignore_missing=True
			)
		else:
			frappe.rename_doc("Custom HTML Block", OLD, NEW, force=True)

	_repoint_workspaces()

	frappe.db.commit()


def _repoint_workspaces():
	"""Move every workspace reference off the old block name."""
	if not frappe.db.table_exists("Workspace"):
		return

	# The child table carries the name and the label shown in the workspace
	# editor; both used to read "T&A Dashboard".
	if frappe.db.table_exists("Workspace Custom Block"):
		for field in ("custom_block_name", "label"):
			frappe.db.sql(
				f"""UPDATE `tabWorkspace Custom Block` SET `{field}` = %(new)s
				    WHERE `{field}` = %(old)s""",  # nosemgrep - field is from a literal tuple
				{"old": OLD, "new": NEW},
			)

	# `content` is a JSON blob, so the reference is edited as text. Only rows
	# that actually mention the block are touched, and each is saved through
	# db.set_value to avoid running workspace validation on unrelated records.
	rows = frappe.db.sql(
		"""SELECT name, content FROM `tabWorkspace` WHERE content LIKE %(like)s""",
		{"like": f"%{OLD}%"},
		as_dict=True,
	)
	for row in rows:
		frappe.db.set_value(
			"Workspace",
			row.name,
			"content",
			(row.content or "").replace(OLD, NEW),
			update_modified=False,
		)

	frappe.clear_cache()
