import frappe
from frappe.model.utils.rename_field import rename_field


_RENAMES = [
	("device_location",         "users_device_sn"),
	("biodata_device_location", "biodata_device_sn"),
]


def execute():
	"""Rename mislabelled Biometric Setting fields to match their actual content.

	The fields previously named *_device_location actually store device serial
	numbers (set by JS from match.device_sn). Rename them to *_device_sn so the
	field name matches the data.
	"""
	if not frappe.db.exists("DocType", "Biometric Setting"):
		return

	frappe.reload_doc("upande_ta", "doctype", "biometric_setting")

	def _single_value(field):
		row = frappe.db.sql(
			"SELECT value FROM `tabSingles` WHERE doctype='Biometric Setting' AND field=%s",
			(field,),
		)
		return row[0][0] if row else None

	for old, new in _RENAMES:
		old_value = _single_value(old)
		new_value = _single_value(new)

		if old_value and not new_value:
			frappe.db.sql(
				"""
					INSERT INTO `tabSingles` (doctype, field, value)
					VALUES ('Biometric Setting', %s, %s)
				""",
				(new, old_value),
			)
			print(f"[rename_biometric_setting_device_fields] copied {old} -> {new}")

		if old_value is not None:
			frappe.db.sql(
				"DELETE FROM `tabSingles` WHERE doctype='Biometric Setting' AND field=%s",
				(old,),
			)
			print(f"[rename_biometric_setting_device_fields] removed stale {old}")

	frappe.db.commit()
