import frappe


def execute():
	"""Migrate the single-farm `farm` Link on Biometric Device rows to the new
	multi-farm `farms` (comma-separated Small Text) field.

	The `farm` field has been removed from the doctype; Frappe leaves the old
	column on `tabBiometric Device` as an orphan, so we can still read it here.
	Idempotent: only fills `farms` where it is empty and `farm` had a value.
	"""
	if not frappe.db.table_exists("Biometric Device"):
		return

	# Ensure the new `farms` column exists before we write to it.
	frappe.reload_doc("upande_ta", "doctype", "biometric_device")

	cols = frappe.db.get_table_columns("Biometric Device")
	if "farm" not in cols:
		print("[migrate_device_farm_to_farms] no legacy `farm` column; nothing to migrate")
		return

	rows = frappe.db.sql(
		"""
			SELECT name, farm
			FROM `tabBiometric Device`
			WHERE farm IS NOT NULL AND farm != ''
			  AND (farms IS NULL OR farms = '')
		""",
		as_dict=True,
	)
	if not rows:
		print("[migrate_device_farm_to_farms] no device rows need migration")
		return

	updated = 0
	for r in rows:
		frappe.db.set_value(
			"Biometric Device", r["name"], "farms", r["farm"],
			update_modified=False,
		)
		updated += 1

	frappe.db.commit()
	print(f"[migrate_device_farm_to_farms] copied farm -> farms on {updated} device row(s)")
