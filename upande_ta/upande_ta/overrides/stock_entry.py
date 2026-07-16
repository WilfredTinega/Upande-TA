# Copyright (c) 2026, Upande LTD and contributors

"""Stock Entry "Biometric Verification" custom fields.

Created programmatically on install/migrate and removed on uninstall (NOT
shipped as fixtures), mirroring the Leave Type ``abbreviation`` pattern.

``_field_spec()`` is the **single source of truth**. On every migrate,
``ensure_biometric_stock_entry_fields`` creates/updates the fields it defines
and **prunes any app-owned custom field on the managed doctypes that is no
longer in the spec** — so dropping a field from the code auto-removes it from
sites on the next migrate (declarative reconciliation).

A **Stock Entry Type** carries a ``require_biometric`` flag; Stock Entry's
``requires_biometric`` is a read-only field fetched from the selected type, so
the "Biometric Verification" section appears only for entry types that enable
it. UI logic lives in ``public/js/stock_entry.js`` (wired via ``doctype_js``);
it reads ``Biometric Logs`` (also owned by this app) for the latest live scan.
"""

import frappe


MODULE = "Upande TA"
# Doctypes whose Upande-TA-owned custom fields this module fully manages.
MANAGED_DOCTYPES = ("Stock Entry Type", "Stock Entry")


def _field_spec():
	"""The custom fields this module owns, keyed by doctype. Source of truth."""
	depends = "eval:doc.requires_biometric"
	return {
		"Stock Entry Type": [
			{
				"fieldname": "require_biometric",
				"label": "Require Biometric Verification",
				"fieldtype": "Check",
				"default": "0",
				"description": "When enabled, Stock Entries of this type show the Biometric Verification section.",
				"insert_after": "purpose",
				"module": MODULE,
			},
		],
		"Stock Entry": [
			{
				"fieldname": "biometric_verification_section",
				"label": "Biometric Verification",
				"fieldtype": "Section Break",
				# Placed right after the "Default Warehouse" section
				# (its last field is target_address_display).
				"insert_after": "target_address_display",
				"collapsible": 0,
				"depends_on": depends,
				"module": MODULE,
			},
			{
				"fieldname": "requires_biometric",
				"label": "Requires Biometric Verification",
				"fieldtype": "Check",
				"default": "0",
				# Driven by the Stock Entry Type flag — read-only on the entry.
				"fetch_from": "stock_entry_type.require_biometric",
				"fetch_if_empty": 0,
				"read_only": 1,
				"insert_after": "biometric_verification_section",
				"module": MODULE,
			},
			{
				"fieldname": "bio_employee",
				"label": "Employee (Receiving)",
				"fieldtype": "Link",
				"options": "Employee",
				"depends_on": depends,
				"insert_after": "requires_biometric",
				"module": MODULE,
			},
			{
				"fieldname": "bio_employee_name",
				"label": "Employee Name",
				"fieldtype": "Data",
				"fetch_from": "bio_employee.employee_name",
				"read_only": 1,
				"depends_on": depends,
				"insert_after": "bio_employee",
				"module": MODULE,
			},
			{
				"fieldname": "department",
				"label": "Department",
				"fieldtype": "Link",
				"options": "Department",
				"fetch_from": "bio_employee.department",
				"read_only": 1,
				"depends_on": depends,
				"insert_after": "bio_employee_name",
				"module": MODULE,
			},
			{
				"fieldname": "biometric_verification_column",
				"fieldtype": "Column Break",
				"insert_after": "department",
				"module": MODULE,
			},
			{
				"fieldname": "biometric_status",
				"label": "Verification Status",
				"fieldtype": "Select",
				"options": "Pending\nVerified\nFailed",
				"default": "Pending",
				"read_only": 1,
				"depends_on": depends,
				"insert_after": "biometric_verification_column",
				"module": MODULE,
			},
			{
				"fieldname": "biometric_verified_at",
				"label": "Verified At",
				"fieldtype": "Datetime",
				"read_only": 1,
				"depends_on": depends,
				"insert_after": "biometric_status",
				"module": MODULE,
			},
			{
				"fieldname": "matched_biometric_log",
				"label": "Matched Biometric Log",
				"fieldtype": "Link",
				"options": "Biometric Logs",
				"read_only": 1,
				"depends_on": depends,
				"insert_after": "biometric_verified_at",
				"module": MODULE,
			},
		],
	}


def ensure_biometric_stock_entry_fields():
	"""Create/update the defined fields and prune app-owned fields no longer
	in the spec. Idempotent; no-op if ERPNext's Stock Entry table isn't present.
	"""
	if not frappe.db.table_exists("Stock Entry"):
		return

	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	spec = _field_spec()
	create_custom_fields(spec, update=True)

	# Declarative reconciliation: any Upande-TA-owned custom field on a managed
	# doctype that is NOT in the spec is stale (e.g. the superseded SCP
	# child-table fields) — remove it.
	for doctype, defs in spec.items():
		defined = {d["fieldname"] for d in defs}
		for row in frappe.get_all(
			"Custom Field",
			filters={"dt": doctype, "module": MODULE},
			fields=["name", "fieldname"],
		):
			if row.fieldname not in defined:
				frappe.delete_doc("Custom Field", row.name, ignore_permissions=True, force=True)


def remove_biometric_stock_entry_fields():
	"""Delete every Upande-TA-owned custom field on the managed doctypes
	(uninstall)."""
	for doctype in MANAGED_DOCTYPES:
		for name in frappe.get_all(
			"Custom Field", filters={"dt": doctype, "module": MODULE}, pluck="name"
		):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
