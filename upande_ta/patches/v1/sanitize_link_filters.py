import json

import frappe


TARGET_TABLES = ("tabDocField", "tabCustom Field", "tabCustomize Form Field")


def execute():
	"""Null out invalid `link_filters` values and drop the auto json_valid CHECK.

	Legacy `''` values trip MariaDB's CHECK(json_valid(link_filters)) the moment
	any UPDATE touches the row (including DocType sync's delete+reinsert).
	Idempotent.
	"""
	for table in TARGET_TABLES:
		_sanitize_link_filters(table)
		_drop_link_filters_check(table)
	frappe.db.commit()


def _has_link_filters_column(table):
	return bool(
		frappe.db.sql(
			"""
				SELECT 1 FROM information_schema.columns
				WHERE table_schema = DATABASE()
				  AND table_name = %s
				  AND column_name = 'link_filters'
			""",
			(table,),
		)
	)


def _sanitize_link_filters(table):
	if not _has_link_filters_column(table):
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


def after_migrate_drop_check():
	"""Re-drop the CHECK after `sync_all()` re-creates it via JSON-column ALTERs."""
	for table in TARGET_TABLES:
		_drop_link_filters_check(table)
	frappe.db.commit()


def _drop_link_filters_check(table):
	"""Strip the inline CHECK(json_valid(link_filters)) by redefining the column.

	The CHECK is part of the column definition, not a standalone table
	constraint, so DROP CONSTRAINT reports it missing. MODIFY COLUMN drops it.
	"""
	if not _has_link_filters_column(table):
		return

	constraints = frappe.db.sql(
		"""
			SELECT CONSTRAINT_NAME
			FROM information_schema.check_constraints
			WHERE constraint_schema = DATABASE()
			  AND table_name = %s
			  AND CHECK_CLAUSE LIKE %s
		""",
		(table, "%link_filters%"),
	)
	if not constraints:
		print(f"[sanitize_link_filters] {table}: link_filters CHECK already absent")
		return

	# Mirrors Frappe's JSON column definition (longtext utf8mb4_bin) so MODIFY
	# doesn't silently rewrite charset/collation.
	try:
		frappe.db.sql_ddl(
			f"ALTER TABLE `{table}` MODIFY COLUMN `link_filters` "
			"longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL"
		)
		print(f"[sanitize_link_filters] {table}: stripped inline CHECK on link_filters")
	except Exception as e:
		print(f"[sanitize_link_filters] {table}: could not strip CHECK on link_filters: {e}")
