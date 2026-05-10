import frappe


def execute():
	if not frappe.db.table_exists("DefaultValue"):
		return

	frappe.db.sql(
		"""
		DELETE FROM `tabDefaultValue`
		WHERE parenttype = '__UserSettings'
		  AND defkey = 'Biometric Template'
		"""
	)
	frappe.db.commit()
