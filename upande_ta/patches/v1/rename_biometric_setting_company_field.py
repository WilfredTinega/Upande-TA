import frappe


def execute():
	"""Rename Biometric Setting `company` (Check) to `scope_company`.

	The original `company` Check field collided with ERPNext's global validate
	hook `check_for_running_deletion_job`, which reads `doc.company` expecting
	a Link/str. On v15 Pydantic-validated whitelists this raised
	FrappeTypeError on every save.
	"""
	if not frappe.db.exists("DocType", "Biometric Setting"):
		return

	def _single_value(field):
		row = frappe.db.sql(
			"SELECT value FROM `tabSingles` WHERE doctype='Biometric Setting' AND field=%s",
			(field,),
		)
		return row[0][0] if row else None

	old, new = "company", "scope_company"
	old_value = _single_value(old)
	new_value = _single_value(new)

	if old_value is not None and new_value is None:
		frappe.db.sql(
			"INSERT INTO `tabSingles` (doctype, field, value) VALUES ('Biometric Setting', %s, %s)",
			(new, old_value),
		)
		print(f"[rename_biometric_setting_company_field] copied {old} -> {new}")

	if old_value is not None:
		frappe.db.sql(
			"DELETE FROM `tabSingles` WHERE doctype='Biometric Setting' AND field=%s",
			(old,),
		)
		print(f"[rename_biometric_setting_company_field] removed stale {old}")

	frappe.db.commit()
	frappe.reload_doc("upande_ta", "doctype", "biometric_setting")
