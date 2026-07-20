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
from frappe import _
from frappe.utils import cint


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
				# Only shown once actually verified (i.e. it holds a value).
				"depends_on": "eval:doc.requires_biometric && doc.biometric_verified_at",
				"insert_after": "biometric_status",
				"module": MODULE,
			},
			{
				"fieldname": "matched_biometric_log",
				"label": "Matched Biometric Log",
				"fieldtype": "Link",
				"options": "Biometric Logs",
				"read_only": 1,
				# Only shown once a log has been matched (i.e. it holds a value).
				"depends_on": "eval:doc.requires_biometric && doc.matched_biometric_log",
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


# ── Automatic biometric verification ─────────────────────────────────────────
# No button: verification happens on its own from either direction, as long as
# the employee's scan and the Stock Entry save land within the verification
# window of each other.
#   • Stock Entry ``validate``  → look BACK for a fresh scan (scan-then-save).
#   • Biometric Logs ``after_insert`` → look FORWARD for a just-saved draft
#     awaiting this employee (save-then-scan).
# The window is configurable on Biometric Setting → Stock Verification tab
# (``stock_verification_window_minutes``); falls back to 1 minute.

DEFAULT_VERIFY_WINDOW_MINUTES = 1


def _verify_window_minutes():
	"""Verification window (minutes) from Biometric Setting, min 1."""
	try:
		value = frappe.db.get_single_value(
			"Biometric Setting", "stock_verification_window_minutes"
		)
	except Exception:
		value = None
	return int(value) if value and int(value) > 0 else DEFAULT_VERIFY_WINDOW_MINUTES


def _recent_log_for(employee):
	"""Latest Biometric Log for ``employee`` if it scanned within the window."""
	rows = frappe.get_all(
		"Biometric Logs",
		filters={"employee": employee},
		fields=["name", "time"],
		order_by="time desc",
		limit=1,
	)
	if not rows:
		return None
	cutoff = frappe.utils.add_to_date(
		frappe.utils.now_datetime(), minutes=-_verify_window_minutes()
	)
	if frappe.utils.get_datetime(rows[0].time) >= cutoff:
		return rows[0]
	return None


def auto_verify_biometric(doc, method=None):
	"""Stock Entry ``validate``: verify against the latest fresh scan, no button.

	Silently leaves the status Pending when there is no recent scan — the
	Biometric Logs ``after_insert`` hook completes it if the scan arrives shortly
	after the save.
	"""
	if not getattr(doc, "requires_biometric", 0):
		return

	# A brand-new entry always lands as Pending — verification comes afterwards
	# (a live scan, a re-save, or the manual "Check Biometric Log" button), so
	# the store-keeper sees the status after the first save rather than having
	# it silently verify on creation.
	if doc.is_new():
		if not doc.biometric_status:
			doc.biometric_status = "Pending"
		return

	if not doc.bio_employee:
		return
	if doc.biometric_status == "Verified":
		return

	log = _recent_log_for(doc.bio_employee)
	if not log:
		return

	doc.biometric_status = "Verified"
	doc.biometric_verified_at = frappe.utils.now_datetime()
	doc.matched_biometric_log = log.name


def verify_pending_stock_entries(doc, method=None):
	"""Biometric Logs ``after_insert`` / ``on_update``: VERIFY any recently-saved
	draft Stock Entry that is still awaiting this employee's biometric
	verification. Never submits — submission is always confirmed by a human via
	the modal on the open form (auto-shown when automatic submission is enabled,
	or on button click otherwise).

	Only drafts touched within the window are considered, so a fresh scan can't
	resurrect a stale/abandoned draft. ``notify_update()`` pushes the Verified
	state to any open form so it can raise that modal.
	"""
	if not doc.employee:
		return

	cutoff = frappe.utils.add_to_date(
		frappe.utils.now_datetime(), minutes=-_verify_window_minutes()
	)
	pending = frappe.get_all(
		"Stock Entry",
		filters={
			"docstatus": 0,
			"requires_biometric": 1,
			"bio_employee": doc.employee,
			"biometric_status": ["!=", "Verified"],
			"modified": [">=", cutoff],
		},
		pluck="name",
	)
	if not pending:
		return

	verified_at = frappe.utils.now_datetime()
	for name in pending:
		try:
			se = frappe.get_doc("Stock Entry", name)
			se.biometric_status = "Verified"
			se.biometric_verified_at = verified_at
			se.matched_biometric_log = doc.name
			se.flags.ignore_permissions = True
			se.save()
			se.notify_update()
			# Explicitly nudge any open form for this entry to reload (and then
			# raise the submit modal). notify_update alone does not reliably
			# refresh an already-open form.
			frappe.publish_realtime(
				"biometric_stock_entry_verified",
				{"stock_entry": name},
				doctype="Stock Entry",
				docname=name,
				after_commit=True,
			)
		except Exception:
			frappe.log_error(
				title="Biometric verification failed",
				message=f"Stock Entry {name} for scan {doc.name}:\n{frappe.get_traceback()}",
			)


@frappe.whitelist()
def check_biometric_log(stock_entry):
	"""Manual verification trigger for the "Check Biometric Log" button.

	Re-checks the latest scan for the receiving employee and, if it is within
	the configured window, marks the entry Verified (draft — it does NOT submit;
	the client raises the Yes/No submit modal after a ``verified`` result).
	Returns a small status dict the client uses to message the store-keeper.
	"""
	se = frappe.get_doc("Stock Entry", stock_entry)

	if not se.get("requires_biometric"):
		return {"status": "not_required"}
	if not se.bio_employee:
		frappe.throw(_("Please select the Employee (Receiving) first."))

	employee_label = se.bio_employee_name or se.bio_employee

	# Already done (e.g. the save that preceded this click auto-verified it).
	if se.biometric_status == "Verified":
		return {"status": "verified", "employee": employee_label}

	rows = frappe.get_all(
		"Biometric Logs",
		filters={"employee": se.bio_employee},
		fields=["name", "employee_name", "time"],
		order_by="time desc",
		limit=1,
	)
	if not rows:
		return {"status": "no_log", "employee": employee_label}

	log = rows[0]
	window = _verify_window_minutes()
	diff_seconds = frappe.utils.time_diff_in_seconds(
		frappe.utils.now_datetime(), frappe.utils.get_datetime(log.time)
	)

	if diff_seconds > window * 60:
		return {
			"status": "too_old",
			"employee": employee_label,
			"log": log.name,
			"time": str(log.time),
			"minutes": round(diff_seconds / 60),
			"window": window,
		}

	se.biometric_status = "Verified"
	se.biometric_verified_at = frappe.utils.now_datetime()
	se.matched_biometric_log = log.name
	se.flags.ignore_permissions = True
	se.save()

	return {
		"status": "verified",
		"employee": log.employee_name or employee_label,
		"seconds": round(diff_seconds),
		"log": log.name,
	}


@frappe.whitelist()
def revert_biometric(stock_entry):
	"""Send a verified draft back to Pending (declined submission), so no
	"verified draft" limbo lingers. Uses ``db_set`` so it does NOT re-run
	``validate`` (which would immediately re-verify from the still-fresh scan).
	"""
	se = frappe.get_doc("Stock Entry", stock_entry)
	if se.docstatus != 0:
		return
	se.db_set("biometric_status", "Pending", update_modified=False)
	se.db_set("biometric_verified_at", None, update_modified=False)
	se.db_set("matched_biometric_log", None, update_modified=False)
	se.notify_update()


@frappe.whitelist()
def material_request_employee_query(doctype, txt, searchfield, start, page_length, filters):
	"""Link query for Stock Entry's bio_employee field.

	filters["material_request"] is resolved client-side (see stock_entry.js)
	from the current form's item rows -- not re-derived server-side from a
	saved Stock Entry, so this works for unsaved drafts too. Scoped to that
	Material Request's Employee Data rows that haven't been issued via
	another Stock Entry yet (Employee Request.issued_via_stock_entry empty).
	Falls back to a plain Employee search when there's no Material Request
	context, matching bio_employee's existing unrestricted behavior -- also
	used when the Employee Request doctype doesn't exist at all (this app is
	installed on other sites that don't have upande_stores, where
	material_request can still be set on a Stock Entry Detail row for
	reasons unrelated to this feature).
	"""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	material_request = filters.get("material_request")
	if material_request and not frappe.has_permission("Material Request", "read", material_request):
		# Caller can't read this Material Request -- degrade gracefully to the
		# same unrestricted Employee search used when there's no Material
		# Request context at all, rather than leaking which employees are
		# tied to a Material Request the user isn't allowed to see.
		material_request = None
	offset = cint(start) or 0
	limit = cint(page_length) or 20
	txt_lower = (txt or "").lower()

	if not material_request or not frappe.db.table_exists("Employee Request"):
		# list(...): frappe.get_all(..., as_list=True) returns a tuple of tuples
		# on this Frappe version -- normalize to the documented list[tuple] return
		# type (also what the standard Link-field query contract expects).
		# No status filter here: before this feature existed, bio_employee had
		# no custom query at all -- Frappe's generic, fully unrestricted link
		# search. This fallback must match that exactly, including Suspended/
		# Inactive/Left employees.
		return list(
			frappe.get_all(
				"Employee",
				or_filters={"name": ["like", f"%{txt}%"], "employee_name": ["like", f"%{txt}%"]},
				fields=["name", "employee_name"],
				limit_start=offset,
				limit=limit,
				as_list=True,
			)
		)

	rows = frappe.get_all(
		"Employee Request",
		filters={"parent": material_request, "parenttype": "Material Request"},
		fields=["employee", "employee_name", "issued_via_stock_entry"],
	)
	results = [
		(row.employee, row.employee_name)
		for row in rows
		if not row.issued_via_stock_entry
		and (txt_lower in (row.employee or "").lower() or txt_lower in (row.employee_name or "").lower())
	]
	return results[offset : offset + limit]
