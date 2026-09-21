# Copyright (c) 2026, Upande LTD and contributors
"""Carry the manual-change reason from the Bulk Overtime rows up to the batch.

The reason used to sit on every row that HR had changed by hand, which meant
typing the same sentence once per row, and it is not visible in the grid — so a
correction typed straight into the table hit a mandatory field the user could
not see. It is one field on the batch now.

Rows are read before the child field is dropped, so nothing written under the
old shape is lost: each batch takes the first reason its rows carried.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Bulk Overtime"):
		return
	if "override_reason" not in frappe.db.get_table_columns("Bulk Overtime Entry"):
		return
	if "override_reason" not in frappe.db.get_table_columns("Bulk Overtime"):
		return

	rows = frappe.db.sql(
		"""
		select entry.parent, entry.override_reason
		from `tabBulk Overtime Entry` entry
		join `tabBulk Overtime` bo on bo.name = entry.parent
		where ifnull(entry.override_reason, '') != ''
			and ifnull(bo.override_reason, '') = ''
		order by entry.parent, entry.idx
		""",
		as_dict=True,
	)

	moved = {}
	for row in rows:
		moved.setdefault(row.parent, row.override_reason)

	for name, reason in moved.items():
		# update_modified=False: this is the same statement in a new place, not
		# an edit anybody made
		frappe.db.set_value("Bulk Overtime", name, "override_reason", reason, update_modified=False)

	if moved:
		frappe.db.commit()
