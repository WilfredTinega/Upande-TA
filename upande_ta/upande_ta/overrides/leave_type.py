# Copyright (c) 2026, Upande LTD and contributors

import frappe


LEAVE_TYPE_ABBR_FIELD = "abbreviation"

LEAVE_TYPE_ABBR = {
	"maternity leave": "ML",
	"paternity leave": "PL",
	"unpaid leave": "UL",
	"annual leave": "AL",
	"compassionate leave": "CL",
	"compationate leave": "CL",
}

_abbr_cache = {}


def generate_leave_abbr(leave_type: str) -> str:
	if not leave_type:
		return "L"

	key = leave_type.strip().lower()
	if key in LEAVE_TYPE_ABBR:
		return LEAVE_TYPE_ABBR[key]

	if key in _abbr_cache:
		return _abbr_cache[key]

	cleaned = "".join(c if c.isalnum() else " " for c in leave_type)
	words = [w for w in cleaned.split() if w]
	abbr = "".join(w[0].upper() for w in words)[:4] or "L"

	taken = set(LEAVE_TYPE_ABBR.values()) | set(_abbr_cache.values())
	if abbr in taken:
		base = abbr
		suffix_chars = (words[0][1:] if words else "") + "2"
		for ch in suffix_chars:
			candidate = (base + ch.upper())[:4]
			if candidate not in taken:
				abbr = candidate
				break

	_abbr_cache[key] = abbr
	return abbr


def ensure_abbreviation_field():
	if not frappe.db.table_exists("Leave Type"):
		return

	from frappe.custom.doctype.custom_field.custom_field import create_custom_field

	if not frappe.db.exists(
		"Custom Field", {"dt": "Leave Type", "fieldname": LEAVE_TYPE_ABBR_FIELD}
	):
		create_custom_field(
			"Leave Type",
			{
				"fieldname": LEAVE_TYPE_ABBR_FIELD,
				"label": "Abbreviation",
				"fieldtype": "Data",
				"length": 6,
				"insert_after": "leave_type_name",
				"description": (
					"Short code shown in the Monthly Attendance Sheet grid "
					"(e.g. AL, ML, PL). Leave blank to auto-generate."
				),
				"module": "Upande TA",
			},
		)

	populate_abbreviations()


def populate_abbreviations():
	if not frappe.db.has_column("Leave Type", LEAVE_TYPE_ABBR_FIELD):
		return

	rows = frappe.get_all(
		"Leave Type", fields=["name", LEAVE_TYPE_ABBR_FIELD], order_by="creation asc"
	)

	taken = {
		str(r[LEAVE_TYPE_ABBR_FIELD]).strip().upper()
		for r in rows
		if r.get(LEAVE_TYPE_ABBR_FIELD) and str(r[LEAVE_TYPE_ABBR_FIELD]).strip()
	}

	for r in rows:
		current = r.get(LEAVE_TYPE_ABBR_FIELD)
		if current and str(current).strip():
			continue

		abbr = generate_leave_abbr(r["name"])
		if abbr.upper() in taken:
			base, n = abbr, 2
			while f"{base}{n}"[:6].upper() in taken:
				n += 1
			abbr = f"{base}{n}"[:6]

		taken.add(abbr.upper())
		frappe.db.set_value(
			"Leave Type", r["name"], LEAVE_TYPE_ABBR_FIELD, abbr, update_modified=False
		)


def remove_abbreviation_field():
	name = frappe.db.get_value(
		"Custom Field", {"dt": "Leave Type", "fieldname": LEAVE_TYPE_ABBR_FIELD}
	)
	if name:
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
