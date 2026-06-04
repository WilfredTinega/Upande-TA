import frappe


_BACKUP_TABLE = "__upande_ta_biometric_user_backup"


def execute():
	"""Migrate flat Biometric User docs into parent/child structure.

	Old shape: one Biometric User doc per (device_sn, user_id), name = "{device_sn}-{user_id}"
	New shape: one Biometric User doc per device, with a `users` child table of Bio User rows.

	Safety:
	- Skip if new shape is already in place AND no flat-shape rows remain.
	- Snapshot old rows into a backup table before any destructive write, so the
	  patch is fully recoverable until the backup is dropped.
	- Idempotent: re-running on an already-migrated site is a no-op.
	"""
	if not frappe.db.table_exists("Biometric User"):
		return

	existing_cols = set(frappe.db.get_table_columns("Biometric User"))

	is_old_shape = "user_id" in existing_cols and "employee" in existing_cols

	if not is_old_shape:
		# Already migrated (or fresh install with the new schema).
		print("[migrate_biometric_user_to_child] new shape detected; nothing to migrate")
		return

	old_rows = frappe.db.sql(
		"""
			SELECT name, device_sn, user_id, employee, employee_name,
			       privilege, status
			FROM `tabBiometric User`
			WHERE device_sn IS NOT NULL AND device_sn != ''
			  AND user_id   IS NOT NULL AND user_id   != ''
		""",
		as_dict=True,
	)

	if not old_rows:
		print("[migrate_biometric_user_to_child] no flat-shape rows to migrate; reloading schema")
		frappe.reload_doc("upande_ta", "doctype", "bio_user")
		frappe.reload_doc("upande_ta", "doctype", "biometric_user")
		return

	# Snapshot into a backup table that survives the patch.
	# Keep it indefinitely; safe to drop manually once verified.
	frappe.db.sql_ddl(
		f"DROP TABLE IF EXISTS `{_BACKUP_TABLE}`"
	)
	frappe.db.sql_ddl(
		f"CREATE TABLE `{_BACKUP_TABLE}` AS SELECT * FROM `tabBiometric User`"
	)
	print(f"[migrate_biometric_user_to_child] snapshotted old rows into `{_BACKUP_TABLE}`")

	frappe.reload_doc("upande_ta", "doctype", "bio_user")
	frappe.reload_doc("upande_ta", "doctype", "biometric_user")

	# Determine which parent-side location field the current JSON uses
	parent_cols = set(frappe.db.get_table_columns("Biometric User"))
	loc_field = "device_location" if "device_location" in parent_cols else (
		"device_name" if "device_name" in parent_cols else None
	)

	# Wipe stale rows now that backup is committed
	frappe.db.sql("DELETE FROM `tabBio User` WHERE parenttype = 'Biometric User'")
	frappe.db.sql("DELETE FROM `tabBiometric User`")
	frappe.db.commit()

	by_device = {}
	for r in old_rows:
		by_device.setdefault(r.device_sn, []).append(r)

	created_parents = 0
	created_children = 0
	skipped_orphan_devices = 0
	for device_sn, rows in by_device.items():
		device_location = frappe.db.get_value(
			"Biometric Device",
			{"parent": "Biometric Setting", "device_sn": device_sn},
			"device_location",
		) or device_sn

		parent_payload = {
			"doctype":   "Biometric User",
			"device_sn": device_sn,
		}
		if loc_field:
			parent_payload[loc_field] = device_location

		try:
			parent = frappe.get_doc(parent_payload)
			frappe.flags.allow_biometric_parent_insert = True
			try:
				parent.insert(ignore_permissions=True)
			finally:
				frappe.flags.allow_biometric_parent_insert = False
		except Exception as e:
			skipped_orphan_devices += 1
			print(f"[migrate_biometric_user_to_child] could not create parent for {device_sn}: {e}; rows preserved in `{_BACKUP_TABLE}`")
			continue

		seen_pins = set()
		for old in rows:
			pin = (old.user_id or "").strip()
			if not pin or pin in seen_pins:
				continue
			seen_pins.add(pin)
			parent.append("users", {
				"user_id":       pin,
				"employee":      old.employee,
				"employee_name": old.employee_name or "",
				"privilege":     old.privilege or "0",
				"status":        old.status or "Active",
			})
			created_children += 1

		if seen_pins:
			parent.save(ignore_permissions=True)
		created_parents += 1

	frappe.db.commit()
	print(
		f"[migrate_biometric_user_to_child] migrated {len(old_rows)} old rows into "
		f"{created_parents} parent docs / {created_children} child rows; "
		f"{skipped_orphan_devices} device(s) skipped; backup retained at `{_BACKUP_TABLE}`"
	)
