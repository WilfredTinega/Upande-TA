import frappe


_INDEX_NAME = "idx_bio_template_parent_employee"


def execute():
	"""Deduplicate Bio Template rows by (parent, employee) and add a unique index.

	Guarantees one Bio Template row per employee per Biometric Template parent.
	Idempotent: safe to re-run if the index already exists.
	"""
	if not frappe.db.table_exists("Bio Template"):
		return

	_dedupe_rows()
	_add_unique_index()


def _dedupe_rows():
	groups = frappe.db.sql(
		"""
			SELECT parent, employee, COUNT(*) AS cnt
			FROM `tabBio Template`
			WHERE parentfield = 'bio_templates'
			  AND employee IS NOT NULL
			  AND employee != ''
			GROUP BY parent, employee
			HAVING COUNT(*) > 1
		""",
		as_dict=True,
	)
	if not groups:
		print("[dedupe_and_lock_bio_template] no duplicates")
		return

	merged = 0
	for g in groups:
		rows = frappe.db.sql(
			"""
				SELECT *
				FROM `tabBio Template`
				WHERE parent = %s
				  AND parentfield = 'bio_templates'
				  AND employee   = %s
				ORDER BY creation ASC
			""",
			(g.parent, g.employee),
			as_dict=True,
		)
		if len(rows) < 2:
			continue

		survivor = rows[0]
		victims  = rows[1:]

		merged_values = {}
		skip_cols = {"name", "parent", "parentfield", "parenttype", "idx", "creation", "modified", "owner", "modified_by"}
		for col, current in survivor.items():
			if col in skip_cols:
				continue
			if current not in (None, "", 0):
				continue
			for v in victims:
				vv = v.get(col)
				if vv not in (None, "", 0):
					merged_values[col] = vv
					break

		if merged_values:
			frappe.db.set_value(
				"Bio Template", survivor["name"], merged_values,
				update_modified=False,
			)

		victim_names = [v["name"] for v in victims]
		frappe.db.sql(
			"""
				DELETE FROM `tabBio Template`
				WHERE name IN %(names)s
			""",
			{"names": tuple(victim_names)},
		)
		merged += len(victims)

	frappe.db.commit()
	print(f"[dedupe_and_lock_bio_template] merged {merged} duplicate row(s)")


def _add_unique_index():
	existing = frappe.db.sql(
		"""
			SELECT INDEX_NAME
			FROM INFORMATION_SCHEMA.STATISTICS
			WHERE TABLE_SCHEMA = DATABASE()
			  AND TABLE_NAME = 'tabBio Template'
			  AND INDEX_NAME = %s
		""",
		(_INDEX_NAME,),
	)
	if existing:
		print(f"[dedupe_and_lock_bio_template] index {_INDEX_NAME} already exists")
		return

	frappe.db.sql_ddl(
		f"""
			CREATE UNIQUE INDEX `{_INDEX_NAME}`
			ON `tabBio Template` (parent, parentfield, employee)
		"""
	)
	print(f"[dedupe_and_lock_bio_template] created unique index {_INDEX_NAME}")
