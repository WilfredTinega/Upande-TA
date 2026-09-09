import frappe

_MODALITIES = (
	("fp_raw_log", "fp_type_code"),
	("face_raw_log", "face_type_code"),
	("palm_raw_log", "palm_type_code"),
)


def execute():
	"""Recover each device's reported BIODATA ``Type=`` code from the raw logs.

	``store_biotemplate`` did not persist the device's ``Type=`` marker even
	though the columns existed, so the only surviving copy is the raw log line
	kept alongside every template. Type codes are per-firmware (this fleet's
	readers report 1 for fingerprint and 9 for face; a Horus E1 reports 2 for
	fingerprint), and a template pushed back under the wrong code is filed by
	the device as the wrong modality — so recovering them per device matters.
	"""
	if not frappe.db.table_exists("Bio Template"):
		return

	for raw_field, code_field in _MODALITIES:
		if not frappe.db.has_column("Bio Template", code_field):
			continue
		if not frappe.db.has_column("Bio Template", raw_field):
			continue

		frappe.db.sql(
			f"""
			UPDATE `tabBio Template`
			   SET `{code_field}` =
			       SUBSTRING(REGEXP_SUBSTR(`{raw_field}`, '(?i)Type=[0-9]+'), 6) + 0
			 WHERE COALESCE(`{code_field}`, 0) = 0
			   AND `{raw_field}` REGEXP '(?i)Type=[0-9]+'
			"""
		)

	frappe.db.commit()
