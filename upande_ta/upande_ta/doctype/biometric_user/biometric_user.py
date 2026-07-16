# Copyright (c) 2026, Upande LTD and contributors

import json
import urllib.request

import frappe
from frappe.model.document import Document


class BiometricUser(Document):
    def before_insert(self):
        if not frappe.flags.get("allow_biometric_parent_insert"):
            frappe.throw(
                "Biometric User parents are created automatically when you add a "
                "device in Biometric Setting. Add the device there instead."
            )

    def validate(self):
        if self.device_sn and not self.device_location:
            self.device_location = _lookup_device_location(self.device_sn)


def _lookup_device_location(device_sn):
    if not device_sn:
        return ""
    row = frappe.db.get_value(
        "Biometric Device",
        {"parent": "Biometric Setting", "device_sn": device_sn},
        "device_location",
    )
    return row or ""


def _employee_has_custom_farm():
    return "custom_farm" in frappe.db.get_table_columns("Employee")


def _parse_farms(value):
    """Split the comma-separated `farms` field of a Biometric Device row into a
    clean list of Farm docnames."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(",")
    return [f.strip() for f in items if f and f.strip()]


def _coerce_farms_arg(value):
    """Normalise the `farms` argument received over HTTP into a list of farm
    docnames. The frontend may send a JSON array string (``["A", "B"]``), a
    plain comma-separated string, an empty/blank string (null form arg), or an
    already-decoded list. Falls back to comma-splitting when the string isn't
    valid JSON so we never raise on unexpected input."""
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass  # not JSON — let _parse_farms comma-split the raw string
    return _parse_farms(value)


def _device_farms(device_sn):
    """Return the list of Farm docnames a device is linked to, read from the
    single Biometric Setting's devices table. Empty list = no restriction."""
    if not device_sn:
        return []
    farms = frappe.db.get_value(
        "Biometric Device",
        {"parent": "Biometric Setting", "device_sn": device_sn},
        "farms",
    )
    return _parse_farms(farms)


def _employee_farms_by_pin(user_ids):
    """Map device PIN (Employee.attendance_device_id) -> custom_farm for the
    given PINs, in a single query. Returns {} when the custom_farm field is
    absent."""
    user_ids = [u for u in {str(u).strip() for u in (user_ids or [])} if u]
    if not user_ids or not _employee_has_custom_farm():
        return {}
    rows = frappe.get_all(
        "Employee",
        filters={"attendance_device_id": ["in", user_ids]},
        fields=["attendance_device_id", "custom_farm"],
    )
    return {r.attendance_device_id: r.custom_farm for r in rows}


def _ensure_biometric_user_parent(device_sn):
    if not device_sn:
        frappe.throw("device_sn is required to resolve a Biometric User parent")

    existing = frappe.db.get_value("Biometric User", {"device_sn": device_sn}, "name")
    if existing:
        return existing

    device_location = _lookup_device_location(device_sn) or device_sn
    doc = frappe.get_doc({
        "doctype":         "Biometric User",
        "device_sn":       device_sn,
        "device_location": device_location,
    })
    frappe.flags.allow_biometric_parent_insert = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.allow_biometric_parent_insert = False
    frappe.db.commit()
    return doc.name


def _get_parent_doc(device_sn):
    parent_name = _ensure_biometric_user_parent(device_sn)
    return frappe.get_doc("Biometric User", parent_name)


def _find_child_row(parent_doc, user_id):
    if not user_id:
        return None
    for row in (parent_doc.users or []):
        if (row.user_id or "").strip() == str(user_id).strip():
            return row
    return None


def _set_template_deleted_flag(device_sn, user_id, value):
    if not device_sn or not user_id:
        return 0
    if not frappe.db.exists("DocType", "Biometric Template"):
        return 0
    parent_name = frappe.db.get_value("Biometric Template", {"device_sn": device_sn}, "name")
    if not parent_name:
        return 0
    rows = frappe.get_all(
        "Bio Template",
        filters={
            "parent":      parent_name,
            "parentfield": "bio_templates",
            "user_id":     user_id,
        },
        pluck="name",
    )
    if not rows:
        return 0
    for row_name in rows:
        frappe.db.set_value("Bio Template", row_name, "deleted", 1 if value else 0)
    return len(rows)


def _apply_user_values(row, values):
    for k, v in values.items():
        row.set(k, v)


def _upsert_child(parent_doc, user_id, values):
    row = _find_child_row(parent_doc, user_id)
    if row:
        _apply_user_values(row, values)
        return row
    payload = {"user_id": user_id, **values}
    return parent_doc.append("users", payload)


def _delete_child(parent_doc, user_id):
    target = _find_child_row(parent_doc, user_id)
    if not target:
        return False
    parent_doc.users = [r for r in (parent_doc.users or []) if r is not target]
    return True


@frappe.whitelist()
def hydrate_users_from_templates(device_sn):
    if not device_sn:
        frappe.throw("device_sn is required")
    if not frappe.db.exists("DocType", "Biometric Template"):
        return {"created": 0, "skipped": 0, "reason": "Biometric Template doctype not migrated"}

    template_parent = frappe.db.get_value("Biometric Template", {"device_sn": device_sn}, "name")
    if not template_parent:
        return {"created": 0, "skipped": 0, "reason": "No Biometric Template for this device"}

    template_rows = frappe.get_all(
        "Bio Template",
        filters={
            "parent":      template_parent,
            "parentfield": "bio_templates",
            "deleted":     0,
        },
        fields=["employee", "employee_name", "user_id", "privilege"],
    )
    if not template_rows:
        return {"created": 0, "skipped": 0}

    parent = _get_parent_doc(device_sn)
    existing_pins = {(r.user_id or "").strip() for r in (parent.users or [])}

    created = 0
    skipped = 0
    for t in template_rows:
        if not t.user_id or not t.employee:
            skipped += 1
            continue
        if t.user_id in existing_pins:
            skipped += 1
            continue
        parent.append("users", {
            "user_id":       t.user_id,
            "employee":      t.employee,
            "employee_name": t.employee_name or "",
            "privilege":     t.privilege or "0",
            "status":        "Active",
        })
        existing_pins.add(t.user_id)
        created += 1

    if created:
        parent.save(ignore_permissions=True)
        frappe.db.commit()
    return {"created": created, "skipped": skipped}


@frappe.whitelist()
def send_device_command(name, command_type, override=None):
    if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
        frappe.throw("Enable Users")

    parent_name, device_sn, child = _resolve_child_by_name(name)

    if isinstance(override, str):
        override = json.loads(override)
    override = override or {}

    user_id       = override.get("user_id")       or (child.user_id if child else None)
    employee_name = override.get("employee_name") or (child.employee_name if child else None)
    privilege     = override.get("privilege")     or (child.privilege if child else None) or "0"
    employee      = child.employee if child else None

    if not device_sn:
        frappe.throw("device_sn is required")

    cmd_id = frappe.generate_hash(length=10)
    parent = frappe.get_doc("Biometric User", parent_name)

    tpl = None
    command = None

    if command_type == "Add User":
        if not user_id or not employee_name:
            frappe.throw("User ID and Employee Name are required")

        tpl = _get_template_row(employee)
        command = _build_userinfo_command(cmd_id, user_id, employee_name, privilege, tpl)

        _upsert_child(parent, user_id, {
            "employee":       employee,
            "employee_name":  employee_name,
            "privilege":      privilege,
            "status":         "Active",
        })
        _set_template_deleted_flag(device_sn, user_id, False)

    elif command_type == "Delete User":
        if not user_id:
            frappe.throw("User ID is required")

        command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
        _delete_child(parent, user_id)
        _set_template_deleted_flag(device_sn, user_id, True)

    else:
        frappe.throw(f"Unknown command type: {command_type}")

    parent.save(ignore_permissions=True)
    frappe.db.commit()

    _post_to_nodered({
        "command_id":    cmd_id,
        "command_type":  command_type,
        "device_sn":     device_sn,
        "user_id":       user_id,
        "employee_name": employee_name,
        "command":       command,
    })

    biodata_queued = 0
    if command_type == "Add User":
        biodata_queued = _queue_biodata_for_user(device_sn, user_id, employee, tpl)

    return {
        "status":         "sent",
        "command_id":     cmd_id,
        "command":        command,
        "biodata_queued": biodata_queued,
    }


def _resolve_child_by_name(child_row_name):
    if not child_row_name:
        frappe.throw("row name is required")
    row = frappe.db.get_value(
        "Bio User",
        child_row_name,
        ["parent", "user_id", "employee", "employee_name", "privilege"],
        as_dict=True,
    )
    if not row:
        frappe.throw(f"Bio User row {child_row_name} not found")
    device_sn = frappe.db.get_value("Biometric User", row.parent, "device_sn")
    child = frappe._dict(row)
    return row.parent, device_sn, child


@frappe.whitelist()
def get_devices():
    settings = frappe.get_single("Biometric Setting")
    return [
        {
            "name":            d.device_sn,
            "device_sn":       d.device_sn,
            "device_location": d.device_location or d.device_sn,
            "farms":           _parse_farms(d.farms),
        }
        for d in (settings.devices or [])
    ]


@frappe.whitelist()
def get_device_users(device_sn):
    if not device_sn:
        return []
    parent_name = frappe.db.get_value("Biometric User", {"device_sn": device_sn}, "name")
    if not parent_name:
        return []
    rows = frappe.get_all(
        "Bio User",
        filters={"parent": parent_name, "parentfield": "users"},
        fields=["name", "user_id", "employee_name", "privilege", "status"],
        order_by="employee_name asc",
    )
    return [
        {
            "row_name":      r.name,
            "user_id":       r.user_id,
            "employee_name": r.employee_name,
            "privilege":     r.privilege or "0",
            "status":        r.status or "Active",
        }
        for r in rows
    ]


@frappe.whitelist()
def get_employees(status="Active", employee=None, designation=None, department=None,
                  company=None, farm=None, farms=None):
    filters = {"attendance_device_id": ["!=", ""]}
    if status == "Active":
        filters["status"] = "Active"
    else:
        filters["status"] = ["in", ["Left", "Inactive"]]

    if employee:
        filters["name"] = employee
    if designation:
        filters["designation"] = designation
    if department:
        filters["department"] = department
    if company:
        filters["company"] = company

    has_farm = _employee_has_custom_farm()
    farms = _coerce_farms_arg(farms)
    if farms and has_farm:
        # A single explicit `farm` pick narrows within the device scope.
        scoped = [farm] if (farm and farm in farms) else farms
        filters["custom_farm"] = ["in", scoped]
    elif farm and has_farm:
        filters["custom_farm"] = farm

    fields = [
        "name", "first_name", "last_name", "attendance_device_id",
        "designation", "department", "company",
    ]
    if has_farm:
        fields.append("custom_farm")

    employees = frappe.get_all(
        "Employee",
        filters=filters,
        fields=fields,
        order_by="first_name asc",
    )
    result = []
    for e in employees:
        full_name = f"{e.first_name or ''} {e.last_name or ''}".strip()
        result.append({
            "employee":    e.name,
            "user_id":     e.attendance_device_id,
            "full_name":   full_name,
            "designation": e.designation,
            "department":  e.department,
            "company":     e.company,
            "farm":        e.get("custom_farm") if has_farm else None,
        })
    return result


@frappe.whitelist()
def get_active_filter_options(department=None, designation=None, company=None, farm=None, farms=None):
    has_farm = _employee_has_custom_farm()
    device_farms = _coerce_farms_arg(farms)
    fields = ["designation", "department", "company"]
    if has_farm:
        fields.append("custom_farm")
    all_employees = frappe.get_all("Employee", fields=fields)

    # When the selected device(s) restrict farms, only consider employees of
    # those farms everywhere below.
    if device_farms and has_farm:
        all_employees = [e for e in all_employees if e.get("custom_farm") in device_farms]

    companies = sorted({e.company for e in all_employees if e.company})
    farms     = sorted({e.custom_farm for e in all_employees if e.get("custom_farm")}) if has_farm else []

    scope = all_employees
    if company:
        scope = [e for e in scope if e.company == company]
    if farm and has_farm:
        scope = [e for e in scope if e.get("custom_farm") == farm]

    departments = sorted({e.department for e in scope if e.department})

    designation_pool = scope
    if department:
        designation_pool = [e for e in designation_pool if e.department == department]
    designations = sorted({e.designation for e in designation_pool if e.designation})

    employee_filters = {"status": "Active"}
    if department:
        employee_filters["department"] = department
    if designation:
        employee_filters["designation"] = designation
    if company:
        employee_filters["company"] = company
    if has_farm:
        if farm:
            employee_filters["custom_farm"] = farm
        elif device_farms:
            employee_filters["custom_farm"] = ["in", device_farms]
    employee_count = frappe.db.count("Employee", employee_filters)

    return {
        "designations":      designations,
        "departments":       departments,
        "companies":         companies,
        "farms":             farms,
        "designation_count": len(designations),
        "department_count":  len(departments),
        "company_count":     len(companies),
        "farm_count":        len(farms),
        "employee_count":    employee_count,
    }


@frappe.whitelist()
def bulk_command(device_sn, users, command_type):
    if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
        frappe.throw("Enable Users")

    if isinstance(users, str):
        users = json.loads(users)

    if not device_sn or not users:
        frappe.throw("device_sn and users are required")

    parent = _get_parent_doc(device_sn)

    queued = []
    failed = []
    post_queue = []

    # Farm scoping: when a device is linked to one or more farms, only employees
    # of those farms may be ADDED or UPDATED on it. Delete is exempt — deletion
    # is cleanup (removing stale/foreign/old-PIN enrollments is exactly what an
    # operator needs on a farm-scoped device), so gating it would trap orphans.
    # Devices with no farm assigned are unrestricted. This is the authoritative
    # guard for every entry point (bulk_command_per_device / _multi call here).
    farm_gated = command_type in ("Add User", "Update User")
    allowed_farms = _device_farms(device_sn) if farm_gated else []
    farm_by_pin = (
        _employee_farms_by_pin([u.get("user_id") for u in users])
        if allowed_farms else {}
    )

    for user in users:
        try:
            user_id       = str(user.get("user_id") or "").strip()
            employee_name = str(user.get("employee_name") or "").strip()[:24]
            privilege     = str(user.get("privilege") or "0").strip()
            skip_name     = bool(user.get("skip_name"))

            if not user_id:
                failed.append({"user_id": user_id, "reason": "Missing PIN"})
                continue

            if allowed_farms:
                emp_farm = farm_by_pin.get(user_id)
                if emp_farm not in allowed_farms:
                    failed.append({
                        "user_id": user_id,
                        "reason": "Employee not assigned to this device's farm(s): "
                                  + ", ".join(allowed_farms),
                    })
                    continue

            cmd_id = frappe.generate_hash(length=10)

            tpl = None
            employee = None

            if command_type == "Add User":
                if not employee_name and not skip_name:
                    failed.append({"user_id": user_id, "reason": "Missing name"})
                    continue

                employee = frappe.db.get_value(
                    "Employee",
                    {"attendance_device_id": user_id},
                    "name",
                )

                tpl = _get_template_row(employee)
                device_name = "" if skip_name else employee_name
                command = _build_userinfo_command(cmd_id, user_id, device_name, privilege, tpl)

                _upsert_child(parent, user_id, {
                    "employee":       employee,
                    "employee_name":  employee_name,
                    "privilege":      privilege,
                    "status":         "Active",
                })
                _set_template_deleted_flag(device_sn, user_id, False)

            elif command_type == "Update User":
                existing = _find_child_row(parent, user_id)
                if not existing:
                    failed.append({"user_id": user_id, "reason": "User not on device"})
                    continue

                # Preserve the existing employee link if the PIN no longer
                # resolves to an Employee (e.g. the PIN was changed): re-resolving
                # to None here would silently wipe the row's employee link.
                employee = frappe.db.get_value(
                    "Employee",
                    {"attendance_device_id": user_id},
                    "name",
                ) or existing.employee

                tpl = _get_template_row(employee)
                device_name = "" if skip_name else employee_name
                command = _build_userinfo_command(cmd_id, user_id, device_name, privilege, tpl)

                _upsert_child(parent, user_id, {
                    "employee":      employee,
                    "employee_name": employee_name,
                    "privilege":     privilege,
                })

            elif command_type == "Delete User":
                command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
                _delete_child(parent, user_id)
                _set_template_deleted_flag(device_sn, user_id, True)

            else:
                frappe.throw(f"Unknown command type: {command_type}")

            post_queue.append({
                "payload": {
                    "command_id":    cmd_id,
                    "command_type":  command_type,
                    "device_sn":     device_sn,
                    "user_id":       user_id,
                    "employee_name": employee_name,
                    "skip_name":     1 if (command_type == "Add User" and skip_name) else 0,
                    "command":       command,
                },
                "user_id":       user_id,
                "employee":      employee,
                "tpl":           tpl,
                "command_type":  command_type,
                "command_id":    cmd_id,
            })

            queued.append({"user_id": user_id, "command_id": cmd_id})

        except Exception as e:
            failed.append({"user_id": user.get("user_id"), "reason": str(e)})

    parent.save(ignore_permissions=True)
    frappe.db.commit()

    for entry in post_queue:
        _post_to_nodered(entry["payload"])
        if entry["command_type"] in ("Add User", "Update User"):
            _queue_biodata_for_user(device_sn, entry["user_id"], employee=entry["employee"], tpl=entry["tpl"])

    return {
        "status":  "done",
        "queued":  len(queued),
        "failed":  len(failed),
        "details": queued,
        "errors":  failed,
    }


@frappe.whitelist()
def bulk_command_per_device(assignments, command_type):
    import time
    from pymysql.err import OperationalError

    if isinstance(assignments, str):
        assignments = json.loads(assignments)
    if not assignments:
        frappe.throw("No device assignments provided")

    # Run device assignments in a deterministic order so concurrent requests
    # acquire locks in the same sequence, sharply reducing deadlock risk.
    ordered = sorted(
        [
            (str(e.get("device_sn") or "").strip(), e.get("users") or [])
            for e in assignments
        ],
        key=lambda x: x[0],
    )

    overall_queued = 0
    overall_failed = 0
    by_device = []
    errors = []

    for sn, users in ordered:
        if not sn or not users:
            continue

        attempts = 0
        while True:
            attempts += 1
            try:
                result = bulk_command(sn, users, command_type)
                overall_queued += int(result.get("queued") or 0)
                overall_failed += int(result.get("failed") or 0)
                by_device.append({
                    "device_sn": sn,
                    "queued":    result.get("queued") or 0,
                    "failed":    result.get("failed") or 0,
                    "errors":    result.get("errors") or [],
                })
                break
            except OperationalError as e:
                # MariaDB deadlock = error code 1213. Retry a few times with
                # short backoff before giving up on this device.
                code = getattr(e, "args", [None])[0]
                if code == 1213 and attempts < 3:
                    frappe.db.rollback()
                    time.sleep(0.15 * attempts)
                    continue
                overall_failed += 1
                errors.append({"device_sn": sn, "reason": str(e)})
                break
            except Exception as e:
                overall_failed += 1
                errors.append({"device_sn": sn, "reason": str(e)})
                break

    return {
        "status":    "done",
        "queued":    overall_queued,
        "failed":    overall_failed,
        "by_device": by_device,
        "errors":    errors,
    }


@frappe.whitelist()
def bulk_command_multi(device_sns, users, command_type):
    if isinstance(device_sns, str):
        device_sns = json.loads(device_sns)
    device_sns = [str(sn).strip() for sn in (device_sns or []) if str(sn).strip()]
    if not device_sns:
        frappe.throw("Select at least one device")

    overall_queued = 0
    overall_failed = 0
    by_device = []
    errors = []

    for sn in device_sns:
        try:
            result = bulk_command(sn, users, command_type)
            overall_queued += int(result.get("queued") or 0)
            overall_failed += int(result.get("failed") or 0)
            by_device.append({
                "device_sn": sn,
                "queued":    result.get("queued") or 0,
                "failed":    result.get("failed") or 0,
                "errors":    result.get("errors") or [],
            })
        except Exception as e:
            overall_failed += 1
            errors.append({"device_sn": sn, "reason": str(e)})

    return {
        "status":    "done",
        "queued":    overall_queued,
        "failed":    overall_failed,
        "by_device": by_device,
        "errors":    errors,
    }


@frappe.whitelist()
def get_device_users_multi(device_sns):
    if isinstance(device_sns, str):
        device_sns = json.loads(device_sns)
    device_sns = [str(sn).strip() for sn in (device_sns or []) if str(sn).strip()]
    if not device_sns:
        return {"users": [], "pins_by_device": {}}

    parents = frappe.get_all(
        "Biometric User",
        filters={"device_sn": ("in", device_sns)},
        fields=["name", "device_sn"],
    )
    parent_to_sn = {p.name: p.device_sn for p in parents}
    pins_by_device = {sn: set() for sn in device_sns}
    users_by_pin = {}

    if not parent_to_sn:
        return {"users": [], "pins_by_device": {sn: [] for sn in device_sns}}

    rows = frappe.get_all(
        "Bio User",
        filters={"parent": ("in", list(parent_to_sn)), "parentfield": "users"},
        fields=["name", "user_id", "employee_name", "privilege", "status", "parent"],
        order_by="employee_name asc",
    )
    for r in rows:
        sn = parent_to_sn.get(r.parent)
        if not sn or not r.user_id:
            continue
        pins_by_device[sn].add(r.user_id)
        existing = users_by_pin.get(r.user_id)
        if not existing:
            users_by_pin[r.user_id] = {
                "row_name":      r.name,
                "user_id":       r.user_id,
                "employee_name": r.employee_name,
                "privilege":     r.privilege or "0",
                "status":        r.status or "Active",
            }

    return {
        "users": sorted(users_by_pin.values(), key=lambda u: (u.get("employee_name") or "")),
        "pins_by_device": {sn: sorted(pins) for sn, pins in pins_by_device.items()},
    }


@frappe.whitelist()
def get_employee_devices(employee):
    if not employee:
        frappe.throw("employee is required")

    pin = (frappe.db.get_value("Employee", employee, "attendance_device_id") or "").strip()
    if not pin:
        return {"pin": "", "devices": []}

    child_rows = frappe.get_all(
        "Bio User",
        filters={"user_id": pin, "parentfield": "users"},
        fields=["parent"],
    )
    if not child_rows:
        return {"pin": pin, "devices": []}

    parent_names = {r.parent for r in child_rows if r.parent}
    if not parent_names:
        return {"pin": pin, "devices": []}

    sn_rows = frappe.get_all(
        "Biometric User",
        filters={"name": ("in", list(parent_names))},
        fields=["device_sn"],
    )
    sns_on = {r.device_sn for r in sn_rows if r.device_sn}
    if not sns_on:
        return {"pin": pin, "devices": []}

    devices = [
        {
            "device_sn":       d.device_sn,
            "device_location": d.device_location or d.device_sn,
        }
        for d in (frappe.get_single("Biometric Setting").devices or [])
        if d.device_sn and d.device_sn in sns_on
    ]
    return {"pin": pin, "devices": devices}


_BIO_TYPES = (
    ("Fingerprint", 1, "fp_bio_no",   "fp_bio_index",   "fp_valid",   "fp_major_ver",   "fp_minor_ver",   "fingerprint_template"),
    ("Face",        9, "face_bio_no", "face_bio_index", "face_valid", "face_major_ver", "face_minor_ver", "face_template"),
    ("Palm",        8, "palm_bio_no", "palm_bio_index", "palm_valid", "palm_major_ver", "palm_minor_ver", "palm_template"),
)

_TEMPLATE_FIELDS = (
    "name",
    "card", "vice_card", "password", "privilege",
    "user_group", "timezone_group", "verify_mode",
    "start_datetime", "end_datetime",
    "fp_bio_no", "fp_bio_index", "fp_valid", "fp_major_ver", "fp_minor_ver", "fingerprint_template",
    "face_bio_no", "face_bio_index", "face_valid", "face_major_ver", "face_minor_ver", "face_template",
    "palm_bio_no", "palm_bio_index", "palm_valid", "palm_major_ver", "palm_minor_ver", "palm_template",
)


def _get_template_row(employee):
    if not employee:
        return None
    rows = frappe.get_all(
        "Bio Template",
        filters={"employee": employee},
        fields=list(_TEMPLATE_FIELDS),
        limit=1,
    )
    return rows[0] if rows else None


def _build_userinfo_command(cmd_id, user_id, employee_name, fallback_privilege, tpl):
    def field(key, default=""):
        if tpl is None:
            return default
        v = tpl.get(key)
        return "" if v is None else str(v)

    privilege      = field("privilege")     or str(fallback_privilege or "0")
    password       = field("password")
    card           = field("card")
    vice_card      = field("vice_card")
    user_group     = field("user_group")    or "1"
    timezone_group = field("timezone_group")
    verify_mode    = field("verify_mode")   or "-1"
    start_datetime = field("start_datetime") or "0"
    end_datetime   = field("end_datetime")   or "0"

    return (
        f"C:{cmd_id}:DATA UPDATE USERINFO"
        f"\tPIN={user_id}"
        f"\tName={employee_name}"
        f"\tPri={privilege}"
        f"\tPasswd={password}"
        f"\tCard={card}"
        f"\tGrp={user_group}"
        f"\tTZ={timezone_group}"
        f"\tVerify={verify_mode}"
        f"\tViceCard={vice_card}"
        f"\tStartDatetime={start_datetime}"
        f"\tEndDatetime={end_datetime}"
    )


def _queue_biodata_for_user(device_sn, user_id, employee=None, tpl=None):
    if not device_sn or not user_id:
        return 0

    if not employee:
        employee = frappe.db.get_value(
            "Employee",
            {"attendance_device_id": user_id},
            "name",
        )
    if not employee:
        return 0

    if tpl is None:
        tpl = _get_template_row(employee)
    if not tpl:
        return 0

    sent = 0
    for label, type_code, no_f, idx_f, valid_f, major_f, minor_f, tmp_f in _BIO_TYPES:
        template = tpl.get(tmp_f)
        if not template:
            continue
        if not tpl.get(valid_f):
            continue

        cmd_id = frappe.generate_hash(length=10)
        command = (
            f"C:{cmd_id}:DATA UPDATE BIODATA"
            f"\tPin={user_id}"
            f"\tNo={tpl.get(no_f) or 0}"
            f"\tIndex={tpl.get(idx_f) or 0}"
            f"\tValid=1"
            f"\tDuress=0"
            f"\tType={type_code}"
            f"\tMajorVer={tpl.get(major_f) or 0}"
            f"\tMinorVer={tpl.get(minor_f) or 0}"
            f"\tFormat=0"
            f"\tTmp={template}"
        )

        _post_to_nodered({
            "command_id":    cmd_id,
            "command_type":  f"Add BioData ({label})",
            "device_sn":     device_sn,
            "user_id":       user_id,
            "employee_name": employee,
            "command":       command,
        })
        sent += 1

    return sent


def _post_to_nodered(payload):
    try:
        settings = frappe.get_single("Biometric Setting")
        ip       = (settings.server_ip or "").strip()
        port     = (settings.server_port or "").strip()
        endpoint = (settings.end_point or "").strip()

        if not ip or not port or not endpoint:
            frappe.log_error("Biometric Setting server config incomplete", "Node-RED Post Error")
            return

        url  = f"http://{ip}:{port}{endpoint}"
        body = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            frappe.logger().info(f"Node-RED response: {resp.status}")

    except Exception as e:
        frappe.log_error(f"Node-RED post failed: {str(e)}", "Node-RED Post Error")


def handle_pin_change(employee, old_pin, new_pin):
    """React to an Employee's device PIN (attendance_device_id / payroll number)
    changing. Re-key every biometric enrollment from the old PIN to the new one
    and re-sync each device the employee is on (remove old PIN, add new PIN).

    DB rows are re-keyed synchronously so records stay consistent immediately;
    the device commands are enqueued so a slow/unreachable node-RED never blocks
    the Employee save.
    """
    old_pin = (old_pin or "").strip()
    new_pin = (new_pin or "").strip()
    if not old_pin or not new_pin or old_pin == new_pin:
        return

    # Re-key the Bio User rows enrolled under the old PIN, collecting the devices.
    bio_user_rows = frappe.get_all(
        "Bio User",
        filters={"user_id": old_pin, "parentfield": "users"},
        fields=["name", "parent"],
    )
    device_sns = set()
    for r in bio_user_rows:
        device_sn = frappe.db.get_value("Biometric User", r.parent, "device_sn")
        if device_sn:
            device_sns.add(device_sn)
        frappe.db.set_value(
            "Bio User", r.name,
            {"user_id": new_pin, "employee": employee},
            update_modified=False,
        )

    # Re-key Bio Template rows (they are keyed on user_id too).
    for name in frappe.get_all("Bio Template", filters={"user_id": old_pin}, pluck="name"):
        frappe.db.set_value("Bio Template", name, "user_id", new_pin, update_modified=False)

    frappe.db.commit()

    if device_sns:
        frappe.enqueue(
            "upande_ta.upande_ta.doctype.biometric_user.biometric_user.resync_pin_on_devices",
            queue="short",
            employee=employee,
            old_pin=old_pin,
            new_pin=new_pin,
            device_sns=sorted(device_sns),
        )


def resync_pin_on_devices(employee, old_pin, new_pin, device_sns):
    """Background worker: on each device, delete the old PIN then (re)add the new
    PIN with the employee's name/template, and re-push biodata. Per-device
    failures are logged, never raised, so one dead device can't stall the rest."""
    tpl = _get_template_row(employee)
    employee_name = (frappe.db.get_value("Employee", employee, "employee_name") or "")[:24]

    for device_sn in (device_sns or []):
        try:
            del_id = frappe.generate_hash(length=10)
            _post_to_nodered({
                "command_id":    del_id,
                "command_type":  "Delete User",
                "device_sn":     device_sn,
                "user_id":       old_pin,
                "employee_name": employee_name,
                "command":       f"C:{del_id}:DATA DELETE USERINFO\tPIN={old_pin}",
            })

            add_id = frappe.generate_hash(length=10)
            _post_to_nodered({
                "command_id":    add_id,
                "command_type":  "Add User",
                "device_sn":     device_sn,
                "user_id":       new_pin,
                "employee_name": employee_name,
                "command":       _build_userinfo_command(add_id, new_pin, employee_name, "0", tpl),
            })

            _queue_biodata_for_user(device_sn, new_pin, employee=employee, tpl=tpl)
        except Exception as e:
            frappe.log_error(
                f"PIN resync failed for device {device_sn} ({old_pin}->{new_pin}): {e}",
                "Biometric PIN Resync",
            )
