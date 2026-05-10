import frappe


STALE_COLUMNS = ("captured_at", "source_device")
DOCTYPE = "Bio Template"
TABLE = "tabBio Template"


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		return

	_remove_custom_fields()
	_remove_property_setters()
	_drop_columns()

	frappe.db.commit()
	frappe.clear_cache(doctype=DOCTYPE)


def _remove_custom_fields():
	for fieldname in STALE_COLUMNS:
		names = frappe.get_all(
			"Custom Field",
			filters={"dt": DOCTYPE, "fieldname": fieldname},
			pluck="name",
		)
		for name in names:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)


def _remove_property_setters():
	for fieldname in STALE_COLUMNS:
		names = frappe.get_all(
			"Property Setter",
			filters={"doc_type": DOCTYPE, "field_name": fieldname},
			pluck="name",
		)
		for name in names:
			frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)


def _drop_columns():
	existing = {
		row[0]
		for row in frappe.db.sql(
			"""
			SELECT column_name
			FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = %s
			""",
			(TABLE,),
		)
	}

	for col in STALE_COLUMNS:
		if col in existing:
			frappe.db.sql_ddl(f"ALTER TABLE `{TABLE}` DROP COLUMN `{col}`")
