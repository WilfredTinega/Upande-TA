import json

import frappe


TARGET_TABLES = ("tabDocField", "tabCustom Field", "tabCustomize Form Field")


def execute():
	"""Null out invalid `link_filters` values before the post-patch DocType sync.

	MariaDB auto-adds a CHECK(json_valid(link_filters)) constraint on JSON
	columns. Legacy rows where link_filters was stored as '' (empty string) —
	common after Form Builder edits on older Frappe versions — violate this
	check, which makes the subsequent `bench migrate` schema sync fail with:

	    (4025, 'CONSTRAINT `tabDocField.link_filters` failed ...')

	We normalize any non-null, non-valid-JSON value in link_filters to NULL on
	DocField, Custom Field, and Customize Form Field so the re-insert during
	sync passes the CHECK. Idempotent; safe to re-run.
	"""
	for table in TARGET_TABLES:
		_sanitize_link_filters(table)
	frappe.db.commit()


def _sanitize_link_filters(table):
	if not frappe.db.sql(
		"""
			SELECT 1 FROM information_schema.columns
			WHERE table_schema = DATABASE()
			  AND table_name = %s
			  AND column_name = 'link_filters'
		""",
		(table,),
	):
		return

	rows = frappe.db.sql(
		f"SELECT name, link_filters FROM `{table}` WHERE link_filters IS NOT NULL AND link_filters != ''",
		as_dict=True,
	)
	bad = []
	for row in rows:
		try:
			json.loads(row["link_filters"])
		except (TypeError, ValueError):
			bad.append(row["name"])

	# Empty strings also fail json_valid; treat them as NULL up front
	empties = frappe.db.sql(
		f"SELECT name FROM `{table}` WHERE link_filters = ''",
		as_dict=True,
	)
	bad.extend(r["name"] for r in empties)

	if not bad:
		print(f"[sanitize_link_filters] {table}: no invalid link_filters")
		return

	for chunk_start in range(0, len(bad), 500):
		chunk = bad[chunk_start : chunk_start + 500]
		placeholders = ", ".join(["%s"] * len(chunk))
		frappe.db.sql(
			f"UPDATE `{table}` SET link_filters = NULL WHERE name IN ({placeholders})",
			chunk,
		)
	print(f"[sanitize_link_filters] {table}: nulled {len(bad)} invalid link_filters row(s)")
