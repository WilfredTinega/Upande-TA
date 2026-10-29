import frappe

_FACE_FIELDS = (
	"face_template", "face_raw_log", "face_valid", "face_size", "face_type_code",
	"face_bio_no", "face_bio_index", "face_major_ver", "face_minor_ver",
)


def execute():
	"""Move enrolment photos that were stored as faces back to the photo field.

	A Node-RED parser for Granding terminals posted the USERPIC enrolment photo
	(a base64 JPEG) as ``bio_type: "Face"``, so it landed in the Bio Template
	face slot. Left there it would be pushed to terminals as a face template,
	which no face engine can use. Each such row gets the JPEG attached as its
	photo (unless it already has one) and its face fields cleared.
	"""
	if not frappe.db.table_exists("Bio Template"):
		return

	from upande_ta.upande_ta.doctype.biometric_template.biometric_template import (
		_attach_photo,
	)

	rows = frappe.db.sql(
		"""
		SELECT name, user_id, face_template, face_size, face_raw_log, photo
		FROM `tabBio Template`
		WHERE face_template LIKE '/9j/%%' OR face_raw_log LIKE 'USERPIC%%'
		""",
		as_dict=True,
	)
	for row in rows:
		if not row.photo and row.face_template:
			url = _attach_photo(row.name, f"{row.user_id or row.name}.jpg", row.face_template)
			if url:
				frappe.db.set_value(
					"Bio Template", row.name,
					{
						"photo":          url,
						"photo_filename": f"{row.user_id or row.name}.jpg",
						"photo_size":     row.face_size or 0,
						"photo_raw_log":  row.face_raw_log or "",
					},
					update_modified=False,
				)
		frappe.db.set_value(
			"Bio Template", row.name,
			{f: (0 if f in ("face_valid", "face_size", "face_type_code", "face_bio_no",
			                "face_bio_index", "face_major_ver", "face_minor_ver") else None)
			 for f in _FACE_FIELDS},
			update_modified=False,
		)
	if rows:
		print(f"upande_ta: moved {len(rows)} enrolment photo(s) out of the Bio Template face slot")
