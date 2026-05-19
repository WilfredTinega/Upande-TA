import json

import frappe


# Tables whose `link_filters` JSON column carries a MariaDB auto-CHECK constraint
# that breaks `bench migrate` when legacy rows hold empty strings or other
# non-JSON values.
TARGET_TABLES = ("tabDocField", "tabCustom Field", "tabCustomize Form Field")


def execute():
	"""Make `bench migrate` survive legacy invalid `link_filters` values.

	MariaDB auto-adds a CHECK(json_valid(link_filters)) constraint on JSON
	columns. Two things can violate it during migrate:

	1. Legacy rows where `link_filters` was stored as '' (empty string) from
	   older Form Builder code. The CHECK then fails the moment any UPDATE
	   touches the row, including the delete+reinsert Frappe does on every
	   DocType reload.

	2. Frappe's INSERT into `tabDocField` during DocType sync omits NULL
	   columns, so the DB default kicks in. On some MariaDB versions / SQL
	   modes the default for an unspecified JSON column lands as '' before
	   the CHECK runs, even when no app code ever writes `link_filters`.

	Fix path:
	  a) Null out any existing invalid `link_filters` values across DocField,
	     Custom Field, and Customize Form Field (handles case 1).
	  b) Drop the auto-generated CHECK constraint on those columns so the
	     subsequent DocType sync can re-insert rows without tripping it
	     (handles case 2). Frappe never relies on the CHECK — it validates JSON
	     in Python on read — so removing it is safe.

	Idempotent; safe to re-run.
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


def _drop_link_filters_check(table):
	"""Drop the auto-generated CHECK constraint on link_filters.

	MariaDB names it `<table>.<column>` for JSON columns. The constraint is
	auto-recreated only when the column is explicitly declared as JSON in a
	CREATE/ALTER — Frappe's schema sync uses `longtext` for MariaDB (see
	frappe/database/schema.py: "MariaDB JSON is same as longtext"), so once
	dropped it stays dropped through migrate.
	"""
	if not _has_link_filters_column(table):
		return

	constraint_name = f"{table}.link_filters"
	exists = frappe.db.sql(
		"""
			SELECT 1 FROM information_schema.check_constraints
			WHERE constraint_schema = DATABASE()
			  AND table_name = %s
			  AND constraint_name = %s
		""",
		(table, constraint_name),
	)
	if not exists:
		print(f"[sanitize_link_filters] {table}: link_filters CHECK already absent")
		return

	try:
		frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP CONSTRAINT `{constraint_name}`")
		print(f"[sanitize_link_filters] {table}: dropped CHECK `{constraint_name}`")
	except Exception as e:
		print(f"[sanitize_link_filters] {table}: could not drop CHECK `{constraint_name}`: {e}")
