import frappe
from frappe.model.sync import sync_for


def execute():
	frappe.reload_doc("upande_ta", "doctype", "bio_template", force=True)
	frappe.reload_doc("upande_ta", "doctype", "biometric_template", force=True)

	sync_for("upande_ta", reset_permissions=False)

	if frappe.db.table_exists("Biometric Template"):
		frappe.db.sql(
			"""
			DELETE FROM `tabDefaultValue`
			WHERE parenttype = '__UserSettings'
			  AND defkey = 'Biometric Template'
			"""
		)

	frappe.clear_cache(doctype="Bio Template")
	frappe.clear_cache(doctype="Biometric Template")
	frappe.db.commit()
