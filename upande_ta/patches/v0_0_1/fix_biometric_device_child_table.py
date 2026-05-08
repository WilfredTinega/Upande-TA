import frappe


def execute():
	doctype = "Biometric Device"
	table = f"tab{doctype}"

	if frappe.db.table_exists(doctype):
		cols = {row[0] for row in frappe.db.sql(f"SHOW COLUMNS FROM `{table}`")}
		if "parent" in cols:
			return
		frappe.db.sql(f"DROP TABLE `{table}`")
		frappe.db.commit()

	frappe.reload_doctype(doctype, force=True)
