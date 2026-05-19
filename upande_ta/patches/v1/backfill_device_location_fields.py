import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	"""Backfill device_location / device_sn companion fields across biometric doctypes.

	After the picker swap (location-as-Select, SN-as-readonly companion):
	- Biometric Template: rename `device_name` -> `device_location` (drops legacy col)
	- Biometric User: rename legacy `device_name` -> `device_location`, then backfill
	- Biometric Checkin (poll rows): drop legacy `device_name`, backfill `device_sn`
	"""
	_rename_template_device_name_to_location()
	_drop_legacy_device_name_on("Biometric User", new_col="device_location")
	_drop_legacy_device_name_on("Biometric Checkin", new_col="device_sn")
	_backfill_biometric_user_location()
	_backfill_biometric_checkin_sn()
	frappe.db.commit()


def _drop_legacy_device_name_on(doctype, new_col):
	if not frappe.db.table_exists(doctype):
		return
	cols = frappe.db.get_table_columns(doctype)
	if "device_name" not in cols:
		return
	if new_col in cols:
		# Copy any leftover values from device_name into the new column first
		frappe.db.sql(
			f"UPDATE `tab{doctype}` SET `{new_col}` = device_name "
			f"WHERE (`{new_col}` IS NULL OR `{new_col}` = '') "
			f"  AND device_name IS NOT NULL AND device_name != ''"
		)
	try:
		frappe.db.sql_ddl(f"ALTER TABLE `tab{doctype}` DROP COLUMN `device_name`")
		print(f"[backfill_device_location_fields] dropped legacy {doctype}.device_name")
	except Exception as e:
		print(f"[backfill_device_location_fields] could not drop {doctype}.device_name: {e}")


def _rename_template_device_name_to_location():
	if not frappe.db.table_exists("Biometric Template"):
		return
	cols = frappe.db.get_table_columns("Biometric Template")
	has_old = "device_name" in cols
	has_new = "device_location" in cols

	if not has_old:
		print("[backfill_device_location_fields] template.device_name already renamed")
		return

	# Copy any stale device_name values into device_location (covers both fresh
	# renames and partially-renamed sites where both columns coexist)
	if has_new:
		frappe.db.sql(
			"UPDATE `tabBiometric Template` SET device_location = device_name "
			"WHERE (device_location IS NULL OR device_location = '') "
			"  AND device_name IS NOT NULL AND device_name != ''"
		)

	frappe.reload_doc("upande_ta", "doctype", "biometric_template")

	if not has_new:
		try:
			rename_field("Biometric Template", "device_name", "device_location")
			print("[backfill_device_location_fields] renamed Biometric Template.device_name -> device_location")
			return
		except Exception as e:
			print(f"[backfill_device_location_fields] rename_field failed: {e}; falling back to manual drop")

	# Both columns coexist — data already copied, drop the legacy column
	try:
		frappe.db.sql_ddl("ALTER TABLE `tabBiometric Template` DROP COLUMN `device_name`")
		print("[backfill_device_location_fields] dropped legacy Biometric Template.device_name column")
	except Exception as e:
		print(f"[backfill_device_location_fields] could not drop device_name: {e} (data already copied)")


def _backfill_biometric_user_location():
	if not frappe.db.table_exists("Biometric User"):
		return
	cols = frappe.db.get_table_columns("Biometric User")
	if "device_location" not in cols:
		return

	rows = frappe.db.sql(
		"""
			SELECT name, device_sn
			FROM `tabBiometric User`
			WHERE (device_location IS NULL OR device_location = '')
			  AND device_sn IS NOT NULL AND device_sn != ''
		""",
		as_dict=True,
	)
	if not rows:
		print("[backfill_device_location_fields] Biometric User has no missing device_location")
		return

	sn_to_loc = {
		r["device_sn"]: (r["device_location"] or r["device_sn"])
		for r in frappe.db.sql(
			"""
				SELECT device_sn, device_location
				FROM `tabBiometric Device`
				WHERE parent = 'Biometric Setting' AND parentfield = 'devices'
			""",
			as_dict=True,
		)
	}

	updated = 0
	for r in rows:
		loc = sn_to_loc.get(r["device_sn"]) or r["device_sn"]
		frappe.db.set_value(
			"Biometric User", r["name"], "device_location", loc,
			update_modified=False,
		)
		updated += 1
	print(f"[backfill_device_location_fields] backfilled device_location on {updated} Biometric User row(s)")


def _backfill_biometric_checkin_sn():
	if not frappe.db.table_exists("Biometric Checkin"):
		return
	cols = frappe.db.get_table_columns("Biometric Checkin")
	if "device_sn" not in cols:
		return

	rows = frappe.db.sql(
		"""
			SELECT name, device
			FROM `tabBiometric Checkin`
			WHERE (device_sn IS NULL OR device_sn = '')
			  AND device IS NOT NULL AND device != ''
		""",
		as_dict=True,
	)
	if not rows:
		print("[backfill_device_location_fields] Biometric Checkin has no missing device_sn")
		return

	device_rows = frappe.db.sql(
		"""
			SELECT device_sn, device_location
			FROM `tabBiometric Device`
			WHERE parent = 'Biometric Setting' AND parentfield = 'devices'
		""",
		as_dict=True,
	)
	loc_to_sn = {(d["device_location"] or d["device_sn"]): d["device_sn"] for d in device_rows if d["device_sn"]}
	sn_set = {d["device_sn"] for d in device_rows if d["device_sn"]}

	updated = 0
	for r in rows:
		value = (r["device"] or "").strip()
		sn = value if value in sn_set else loc_to_sn.get(value)
		if not sn:
			continue
		frappe.db.set_value(
			"Biometric Checkin", r["name"], "device_sn", sn,
			update_modified=False,
		)
		updated += 1
	print(f"[backfill_device_location_fields] backfilled device_sn on {updated} Biometric Checkin row(s)")
