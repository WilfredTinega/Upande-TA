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


# Above this many people the sync runs as a background job: each one is two or
# three Node-RED posts with a 10s timeout, too long for a desk request.
_SYNC_INLINE_LIMIT = 50


@frappe.whitelist()
def sync_templates_from_device(device_sn, source_device_sn=None):
    """Sync ``device_sn`` from ``source_device_sn``'s templates (A to B).

    Everyone holding templates on the source is Added to the target, or
    Updated where the target already lists their PIN, with templates pinned to
    the source (see ``bulk_command``). With no source, or the target itself,
    this is the old roster sync from the device's own templates.
    """
    device_sn = str(device_sn or "").strip()
    source_device_sn = str(source_device_sn or "").strip()
    if not device_sn:
        frappe.throw("device_sn is required")
    if not source_device_sn or source_device_sn == device_sn:
        return hydrate_users_from_templates(device_sn)
    if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
        frappe.throw("Enable Users")

    source_label = _lookup_device_location(source_device_sn) or source_device_sn
    source_parent = _template_parent_for_device(source_device_sn)
    if not source_parent:
        frappe.throw(f"No templates on {source_label}")

    rows = frappe.get_all(
        "Bio Template",
        filters={"parent": source_parent, "parentfield": "bio_templates", "deleted": 0},
        fields=["user_id", "employee", "employee_name",
                "fp_valid", "face_valid", "palm_valid",
                "fingerprint_template", "face_template", "palm_template"],
    )
    pins = {}
    for r in rows:
        pin = (r.user_id or "").strip()
        held = (
            (r.fp_valid and (r.fingerprint_template or "").strip())
            or (r.face_valid and (r.face_template or "").strip())
            or (r.palm_valid and (r.palm_template or "").strip())
        )
        if pin and r.employee and held:
            pins.setdefault(pin, r.employee_name or "")
    if not pins:
        frappe.throw(f"No templates on {source_label}")

    on_target = {
        (u.user_id or "").strip(): u
        for u in (_get_parent_doc(device_sn).users or [])
    }
    add, update = [], []
    for pin, name in pins.items():
        existing = on_target.get(pin)
        user = {
            "user_id":       pin,
            "employee_name": (existing.employee_name if existing else None) or name,
            "privilege":     (existing.privilege if existing else None) or "0",
        }
        (update if existing else add).append(user)

    kwargs = {"device_sn": device_sn, "source_device_sn": source_device_sn,
              "add": add, "update": update}
    if len(add) + len(update) <= _SYNC_INLINE_LIMIT:
        return _run_template_sync(**kwargs)

    frappe.enqueue(
        "upande_ta.upande_ta.doctype.biometric_user.biometric_user._run_template_sync",
        queue="long",
        timeout=3600,
        notify_user=frappe.session.user,
        **kwargs,
    )
    return {"queued_job": 1, "add": len(add), "update": len(update)}


def _run_template_sync(device_sn, source_device_sn, add, update, notify_user=None):
    queued = failed = 0
    errors = []
    for users, command_type in ((add, "Add User"), (update, "Update User")):
        if not users:
            continue
        result = bulk_command(device_sn, users, command_type, source_device_sn)
        queued += int(result.get("queued") or 0)
        failed += int(result.get("failed") or 0)
        errors.extend(result.get("errors") or [])

    out = {"added": len(add), "updated": len(update), "queued": queued,
           "failed": failed, "errors": errors[:50]}
    if notify_user:
        target = _lookup_device_location(device_sn) or device_sn
        source = _lookup_device_location(source_device_sn) or source_device_sn
        frappe.publish_realtime(
            "upande_ta_template_sync_done",
            dict(out, device=target, source=source),
            user=notify_user,
        )
    return out


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
    caps   = _device_capabilities(device_sn)

    tpl = None
    command = None

    if command_type == "Add User":
        if not user_id or not employee_name:
            frappe.throw("User ID and Employee Name are required")

        tpl = _get_template_row(employee, device_sn)
        command = _build_userinfo_command(cmd_id, user_id, employee_name, privilege, tpl, caps)

        _upsert_child(parent, user_id, {
            "employee":       employee,
            "employee_name":  employee_name,
            "privilege":      privilege,
            "status":         "Active",
        })
        was_off_device = _is_template_deleted(device_sn, user_id)
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
        biodata_queued = _queue_biodata_for_user(
            device_sn, user_id, employee, tpl, force=was_off_device, caps=caps
        )

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
def bulk_command(device_sn, users, command_type, source_device_sn=None):
    if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
        frappe.throw("Enable Users")

    if isinstance(users, str):
        users = json.loads(users)

    if not device_sn or not users:
        frappe.throw("device_sn and users are required")

    # Templates pinned to one terminal ("sync A to B"); only Add/Update push any.
    source_device_sn = (
        str(source_device_sn or "").strip()
        if command_type in ("Add User", "Update User") else ""
    )
    source_label = (
        _lookup_device_location(source_device_sn) or source_device_sn
        if source_device_sn else ""
    )
    if source_device_sn and not _template_parent_for_device(source_device_sn):
        frappe.throw(f"No templates on {source_label}")

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
    caps = _device_capabilities(device_sn)

    farm_gated = command_type in ("Add User", "Update User")
    allowed_farms = _device_farms(device_sn) if farm_gated else []
    farm_by_pin = (
        _employee_farms_by_pin([u.get("user_id") for u in users])
        if allowed_farms else {}
    )

    for user in users:
        try:
            user_id       = str(user.get("user_id") or "").strip()
            # Full name: the roster row and the Node-RED payload keep it, and
            # _build_userinfo_command fits it to the device's Name= field.
            employee_name = str(user.get("employee_name") or "").strip()
            privilege     = str(user.get("privilege") or "0").strip()
            skip_name     = bool(user.get("skip_name"))
            skip_face     = bool(user.get("skip_face"))

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
            force_biodata = False

            if command_type == "Add User":
                if not employee_name and not skip_name:
                    failed.append({"user_id": user_id, "reason": "Missing name"})
                    continue

                employee = frappe.db.get_value(
                    "Employee",
                    {"attendance_device_id": user_id},
                    "name",
                )

                tpl = _get_template_row(employee, device_sn, source_device_sn)
                if source_device_sn:
                    reason = _source_push_refusal(tpl, caps, source_label, skip_face)
                    if reason:
                        failed.append({"user_id": user_id, "reason": reason})
                        continue
                device_name = "" if skip_name else employee_name
                command = _build_userinfo_command(cmd_id, user_id, device_name, privilege, tpl, caps)

                _upsert_child(parent, user_id, {
                    "employee":       employee,
                    "employee_name":  employee_name,
                    "privilege":      privilege,
                    "status":         "Active",
                })
                force_biodata = _is_template_deleted(device_sn, user_id)
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

                tpl = _get_template_row(employee, device_sn, source_device_sn)
                if source_device_sn:
                    reason = _source_push_refusal(tpl, caps, source_label, skip_face)
                    if reason:
                        failed.append({"user_id": user_id, "reason": reason})
                        continue
                device_name = "" if skip_name else employee_name
                command = _build_userinfo_command(cmd_id, user_id, device_name, privilege, tpl, caps)

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

            # A pinned source is pushed whole even where the target already
            # holds identical bytes, so it ends up with exactly A's templates.
            if source_device_sn:
                force_biodata = True

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
                "force_biodata": force_biodata,
                "skip_face":     skip_face,
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
            _queue_biodata_for_user(
                device_sn,
                entry["user_id"],
                employee=entry["employee"],
                tpl=entry["tpl"],
                force=entry["force_biodata"],
                caps=caps,
                skip_modalities=("Face",) if entry["skip_face"] else (),
            )

    return {
        "status":  "done",
        "queued":  len(queued),
        "failed":  len(failed),
        "details": queued,
        "errors":  failed,
    }


def _source_push_refusal(tpl, caps, source_label, skip_face=False):
    """Why a pinned-source push would send no template at all, else None."""
    if not tpl:
        return f"No templates on {source_label}"
    held = [
        label for label, *_mid, tmp_f in _BIO_TYPES
        if tpl.get(tmp_f) and not (skip_face and label == "Face")
    ]
    if not held:
        return f"No templates on {source_label}"
    if not any(_device_supports(caps, _MODALITY_CAPABILITY[label]) for label in held):
        return f"{', '.join(held)} not enabled on this device"
    return None


@frappe.whitelist()
def bulk_command_per_device(assignments, command_type, source_device_sn=None):
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
                result = bulk_command(sn, users, command_type, source_device_sn)
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
def bulk_command_multi(device_sns, users, command_type, source_device_sn=None):
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
            result = bulk_command(sn, users, command_type, source_device_sn)
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


# Bits of one user's state on one device, in get_user_sync_status's matrix.
_SYNC_ON = 1        # listed on the device's Bio User roster
_SYNC_CREDENTIALS = (  # (bit, label, capability, template column, valid column)
    (2,  "FP",       "supports_fingerprint", "fingerprint_template", "fp_valid"),
    (4,  "Face",     "supports_face",        "face_template",        "face_valid"),
    (8,  "Palm",     "supports_palm",        "palm_template",        "palm_valid"),
    (16, "Card",     "supports_card",        "card",                 None),
    (32, "Password", "supports_password",    "password",             None),
)


@frappe.whitelist()
def get_user_sync_status(device_sn):
    """Where each user of ``device_sn`` is enrolled, across every device.

    ``devices`` lists every configured terminal with its capability bits;
    ``status[pin]`` holds one bitmask per device (same order): _SYNC_ON when
    the roster lists the PIN, plus a bit per credential the device holds a
    valid copy of. ``eligible[pin]`` is the device indexes the employee's farm
    may be enrolled on (farm-scoped devices refuse everyone else).
    """
    if not device_sn:
        return {"devices": [], "status": {}, "eligible": {}}

    settings_rows = frappe.get_all(
        "Biometric Device",
        filters={"parent": "Biometric Setting", "parentfield": "devices"},
        fields=["device_sn", "device_location", "farms"],
        order_by="idx asc",
    )
    devices = [r for r in settings_rows if r.device_sn]
    index = {d.device_sn: i for i, d in enumerate(devices)}

    pins = {
        (u.user_id or "").strip()
        for u in frappe.get_all(
            "Bio User",
            filters={"parent": frappe.db.get_value("Biometric User", {"device_sn": device_sn}, "name") or "",
                     "parentfield": "users"},
            fields=["user_id"],
        )
    } - {""}
    if not pins:
        return {"devices": [], "status": {}, "eligible": {}, "farm": {}}

    status = {pin: [0] * len(devices) for pin in pins}

    roster_parents = {
        p.name: p.device_sn
        for p in frappe.get_all("Biometric User", fields=["name", "device_sn"])
        if p.device_sn in index
    }
    if roster_parents:
        for r in frappe.get_all(
            "Bio User",
            filters={"parent": ("in", list(roster_parents)), "parentfield": "users",
                     "user_id": ("in", list(pins))},
            fields=["parent", "user_id"],
        ):
            status[r.user_id][index[roster_parents[r.parent]]] |= _SYNC_ON

    template_parents = {}
    if frappe.db.exists("DocType", "Biometric Template"):
        template_parents = {
            p.name: p.device_sn
            for p in frappe.get_all("Biometric Template", fields=["name", "device_sn"])
            if p.device_sn in index
        }
    if template_parents:
        columns = {"parent", "user_id"}
        for _bit, _label, _cap, tmp_f, valid_f in _SYNC_CREDENTIALS:
            columns.add(tmp_f)
            if valid_f:
                columns.add(valid_f)
        for r in frappe.get_all(
            "Bio Template",
            filters={"parent": ("in", list(template_parents)), "parentfield": "bio_templates",
                     "deleted": 0, "user_id": ("in", list(pins))},
            fields=sorted(columns),
        ):
            bits = 0
            for bit, _label, _cap, tmp_f, valid_f in _SYNC_CREDENTIALS:
                value = str(r.get(tmp_f) or "").strip()
                if value and value != "0" and (valid_f is None or r.get(valid_f)):
                    bits |= bit
            status[r.user_id][index[template_parents[r.parent]]] |= bits

    farm_by_pin = _employee_farms_by_pin(pins)
    device_farms = [_parse_farms(d.farms) for d in devices]
    eligible = {
        pin: [i for i, farms in enumerate(device_farms)
              if not farms or farm_by_pin.get(pin) in farms]
        for pin in pins
    }

    out_devices = []
    for d, farms in zip(devices, device_farms):
        caps = _device_capabilities(d.device_sn)
        out_devices.append({
            "device_sn":       d.device_sn,
            "device_location": d.device_location or d.device_sn,
            "farms":           farms,
            "caps":            sum(bit for bit, _l, cap, _t, _v in _SYNC_CREDENTIALS
                                   if _device_supports(caps, cap)),
        })
    farm = {pin: farm_by_pin.get(pin) or "" for pin in pins}
    return {"devices": out_devices, "status": status, "eligible": eligible, "farm": farm}


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


# label, fallback Type= code, bio_no, bio_index, valid, major_ver, minor_ver,
# stored type_code, template. The fallback code is only used when the device
# never told us one: echoing back the Type= the device itself reported keeps
# firmwares that disagree on the face code (E1 answers 2, others 9) working.
_BIO_TYPES = (
    ("Fingerprint", 1, "fp_bio_no",   "fp_bio_index",   "fp_valid",   "fp_major_ver",   "fp_minor_ver",   "fp_type_code",   "fingerprint_template"),
    ("Face",        9, "face_bio_no", "face_bio_index", "face_valid", "face_major_ver", "face_minor_ver", "face_type_code", "face_template"),
    ("Palm",        8, "palm_bio_no", "palm_bio_index", "palm_valid", "palm_major_ver", "palm_minor_ver", "palm_type_code", "palm_template"),
)

_TEMPLATE_FIELDS = (
    "name", "deleted",
    "card", "vice_card", "password", "privilege",
    "user_group", "timezone_group", "verify_mode",
    "start_datetime", "end_datetime",
    "fp_bio_no", "fp_bio_index", "fp_valid", "fp_major_ver", "fp_minor_ver", "fp_type_code", "fingerprint_template",
    "face_bio_no", "face_bio_index", "face_valid", "face_major_ver", "face_minor_ver", "face_type_code", "face_template",
    "palm_bio_no", "palm_bio_index", "palm_valid", "palm_major_ver", "palm_minor_ver", "palm_type_code", "palm_template",
)


_CAPABILITY_FIELDS = (
    "supports_fingerprint",
    "supports_face",
    "supports_palm",
    "supports_card",
    "supports_password",
)

# Which capability gates each biometric modality.
_MODALITY_CAPABILITY = {
    "Fingerprint": "supports_fingerprint",
    "Face":        "supports_face",
    "Palm":        "supports_palm",
}


def _device_capabilities(device_sn):
    """The credentials this terminal can actually store, from Biometric Setting.

    Only a ticked capability is sent: a device missing from Biometric Setting
    gets ``{}``, which ``_device_supports`` reads as "nothing allowed". A site
    that has not migrated the capability fields yet has no boxes to tick, so it
    keeps its previous behaviour (everything allowed).
    """
    if not device_sn:
        return {}
    if not frappe.db.has_column("Biometric Device", "supports_fingerprint"):
        return dict.fromkeys(_CAPABILITY_FIELDS, 1)
    row = frappe.db.get_value(
        "Biometric Device",
        {
            "parent":      "Biometric Setting",
            "parentfield": "devices",
            "device_sn":   device_sn,
        },
        list(_CAPABILITY_FIELDS),
        as_dict=True,
    )
    return row or {}


def _device_type_codes(device_sn):
    """The BIODATA ``Type=`` code this specific terminal uses, per modality.

    Firmwares disagree: the readers in this fleet report 1 for fingerprint and
    9 for face, while a Horus E1 reports 2 for fingerprint. A template pushed
    back under the wrong code is filed by the device as the wrong modality,
    which is how fingerprints ended up as faces. So instead of one global
    constant we learn each device's codes from what it actually reported —
    majority value per modality across its own Bio Template rows.

    Returns ``{"Fingerprint": 2, ...}``, omitting modalities the device has
    never reported. Cached briefly: a device's firmware does not change
    mid-shift, and this is called once per user in bulk pushes.
    """
    if not device_sn:
        return {}

    cache_key = f"bio_type_codes:{device_sn}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return {k: int(v) for k, v in (cached or {}).items()}

    codes = {}
    parent_name = _template_parent_for_device(device_sn)
    if parent_name:
        for label, _default, _no, _idx, _valid, _major, _minor, type_f, _tmp in _BIO_TYPES:
            if not frappe.db.has_column("Bio Template", type_f):
                continue
            row = frappe.db.sql(
                f"""
                SELECT `{type_f}` AS code, COUNT(*) AS n
                  FROM `tabBio Template`
                 WHERE parent = %s
                   AND parentfield = 'bio_templates'
                   AND COALESCE(`{type_f}`, 0) > 0
                 GROUP BY `{type_f}`
                 ORDER BY n DESC
                 LIMIT 1
                """,
                (parent_name,),
                as_dict=True,
            )
            if row:
                codes[label] = int(row[0]["code"])

    frappe.cache().set_value(cache_key, codes, expires_in_sec=3600)
    return codes


def _device_algo_versions(device_sn):
    """Each device's own biometric algorithm version, per modality.

    Templates are algorithm-specific and **not portable across engines**, and
    the ``Type=`` code cannot tell the engines apart: this fleet runs three
    face algorithms — 40.1 (756-char), 35.x (748-char) and 5.6 (344-char) —
    and every one of them reports ``Type=9``. Pushing a 40.1 template to a 5.6
    terminal is accepted by the device and then silently discarded, so the face
    "never saves". Fingerprints are uniform here (13.0, 1400-char) and travel
    fine.

    Returns ``{"Face": (40, 1), ...}``, omitting a modality the device has
    never delivered. Cached for an hour; ``store_biotemplate`` invalidates it.
    """
    if not device_sn:
        return {}

    cache_key = f"bio_algo_versions:{device_sn}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return {k: tuple(v) for k, v in (cached or {}).items()}

    versions = {}
    parent_name = _template_parent_for_device(device_sn)
    if parent_name:
        for label, _c, _no, _idx, _v, major_f, minor_f, _t, tmp_f in _BIO_TYPES:
            row = frappe.db.sql(
                f"""
                SELECT COALESCE(`{major_f}`, 0) AS major,
                       COALESCE(`{minor_f}`, 0) AS minor,
                       COUNT(*) AS n
                  FROM `tabBio Template`
                 WHERE parent = %s
                   AND parentfield = 'bio_templates'
                   AND `{tmp_f}` IS NOT NULL AND `{tmp_f}` <> ''
                 GROUP BY 1, 2
                 ORDER BY n DESC
                 LIMIT 1
                """,
                (parent_name,),
                as_dict=True,
            )
            if row and (row[0]["major"] or row[0]["minor"]):
                versions[label] = (int(row[0]["major"]), int(row[0]["minor"]))

    frappe.cache().set_value(cache_key, versions, expires_in_sec=3600)
    return versions


def _device_supports(caps, field):
    """Only a ticked capability allows a push; unknown means not allowed."""
    return bool((caps or {}).get(field))


def _template_parent_for_device(device_sn):
    if not device_sn:
        return None
    if not frappe.db.exists("DocType", "Biometric Template"):
        return None
    return frappe.db.get_value("Biometric Template", {"device_sn": device_sn}, "name")


def _get_device_template_row(employee, device_sn):
    """The Bio Template row this *specific* device captured, or None.

    Rows hang off a per-device ``Biometric Template`` parent and are written
    only by ``store_biotemplate`` under the posting device's parent, so a row
    found here is proof that this device holds those templates.
    """
    if not employee or not device_sn:
        return None
    parent_name = _template_parent_for_device(device_sn)
    if not parent_name:
        return None
    rows = frappe.get_all(
        "Bio Template",
        filters={
            "parent":      parent_name,
            "parentfield": "bio_templates",
            "employee":    employee,
        },
        fields=list(_TEMPLATE_FIELDS),
        limit=1,
    )
    return rows[0] if rows else None


def _is_template_deleted(device_sn, user_id):
    """True when this device is not currently holding the user's templates.

    Read *before* ``_set_template_deleted_flag(..., False)`` clears the flag on
    an Add: a re-added user needs a full biodata push even though our stored
    bytes are unchanged. No row at all also counts as "not held".
    """
    parent_name = _template_parent_for_device(device_sn)
    if not parent_name or not user_id:
        return True
    row = frappe.db.get_value(
        "Bio Template",
        {"parent": parent_name, "parentfield": "bio_templates", "user_id": user_id},
        "deleted",
    )
    if row is None:
        return True
    return bool(row)


# Only credentials that belong to the PERSON travel between terminals: a card
# number and a password are theirs wherever they go.
#
# privilege, user_group, timezone_group and verify_mode are deliberately NOT
# here. They are per-device access control, and borrowing them silently changes
# what someone can do on a terminal they were merely added to — privilege 14
# (super admin) exists on 40 rows in this fleet, and verify_mode carries
# device-specific values (0, -1, 15). Left alone, _build_userinfo_command falls
# back to safe defaults: privilege 0, group 1, Verify=-1 ("use the device
# default"), which is what a freshly added user should get.
_SHAREABLE_USER_FIELDS = ("card", "vice_card", "password")

# The mirror of the above: fields that belong to the TERMINAL, never to the
# person. When the merge base is another device's row (this device has none of
# its own), these are dropped so _build_userinfo_command falls back to Pri=0 /
# Grp=1 / Verify=-1 instead of inheriting them.
_DEVICE_LOCAL_USER_FIELDS = (
    "privilege", "user_group", "timezone_group", "verify_mode",
    "start_datetime", "end_datetime",
)


def _get_source_template_row(employee, device_sn, source_device_sn):
    """What to push to ``device_sn`` when the operator named the source device.

    Every biometric modality comes from ``source_device_sn``'s row and nowhere
    else: a modality the source lacks — or holds but is not ticked for in its
    capabilities — is blanked rather than filled from a third terminal, so the
    target ends up with exactly the source's templates.
    The target's own row (when it has one) still supplies the device-local
    access fields; card and password come from the source first.
    Returns None when the source holds nothing for this employee.
    """
    source = _get_device_template_row(employee, source_device_sn)
    if source is None:
        return None
    source_caps = _device_capabilities(source_device_sn)
    own = _get_device_template_row(employee, device_sn) if device_sn != source_device_sn else source
    merged = dict(own or source)
    if own is None:
        for _f in _DEVICE_LOCAL_USER_FIELDS:
            merged.pop(_f, None)

    for label, _code, no_f, idx_f, valid_f, major_f, minor_f, _type_f, tmp_f in _BIO_TYPES:
        has = (
            source.get(tmp_f) and source.get(valid_f)
            and _device_supports(source_caps, _MODALITY_CAPABILITY[label])
        )
        for field in (tmp_f, valid_f, no_f, idx_f, major_f, minor_f):
            merged[field] = source.get(field) if has else None

    for field in _SHAREABLE_USER_FIELDS:
        if source.get(field) not in (None, "", "0"):
            merged[field] = source.get(field)

    if not any(merged.get(tmp_f) for *_rest, tmp_f in _BIO_TYPES):
        return None
    return merged


def _get_template_row(employee, device_sn=None, source_device_sn=None):
    """Resolve what to push to ``device_sn``, merged **per credential**.

    ``source_device_sn`` pins the templates to one terminal instead — see
    ``_get_source_template_row``.

    The device's own row wins credential by credential, not wholesale. That
    distinction matters: ``store_biotemplate`` creates a Bio Template row for
    the plain USERINFO ("user") record too, so a device very often holds an
    *empty shell* row for an employee — no template bytes at all. Preferring
    that row as a whole meant an employee enrolled elsewhere got nothing
    pushed, silently, because ``face_template`` on the shell was blank.

    So: start from this device's own row, then fill each modality it lacks from
    the newest other row that actually has one. Type codes are deliberately
    NOT merged — ``_queue_biodata_for_user`` reads those from the device's own
    row so a neighbour's code is never echoed to this terminal.
    """
    if not employee:
        return None
    if source_device_sn:
        return _get_source_template_row(employee, device_sn, source_device_sn)

    rows = frappe.get_all(
        "Bio Template",
        filters={"employee": employee},
        fields=list(_TEMPLATE_FIELDS),
        order_by="deleted asc, modified desc",
    )
    if not rows:
        return None

    # The device's own row is the base when it has one, otherwise the newest
    # row anywhere. Either way the merge below runs: the newest row can be an
    # empty shell too, so taking it wholesale had the same failure mode.
    own = _get_device_template_row(employee, device_sn)
    base = own or rows[0]

    merged = dict(base)
    if own is None:
        # base belongs to another terminal. Its templates are what we came for,
        # but copying the row wholesale would also carry its access control —
        # a Pri=14 super admin on one reader would become one here.
        for _f in _DEVICE_LOCAL_USER_FIELDS:
            merged.pop(_f, None)
    others = [r for r in rows if r.get("name") != base.get("name")]
    algo = _device_algo_versions(device_sn)

    for label, _code, no_f, idx_f, valid_f, major_f, minor_f, _type_f, tmp_f in _BIO_TYPES:
        if merged.get(tmp_f):
            continue

        candidates = [o for o in others if o.get(tmp_f) and o.get(valid_f)]
        if not candidates:
            continue

        # Every enrollment is shared with the selected device — a version
        # mismatch never withholds it. The device is told which algorithm the
        # payload is (MajorVer/MinorVer travel with the template) and decides
        # for itself.
        #
        # The target's own algorithm version is only a *preference*, used to
        # pick between enrollments when someone has more than one: an employee
        # with both a 40.1 and a 5.6 face gets the one that terminal speaks.
        # With a single enrollment, that one is pushed regardless.
        want = algo.get(label)
        source = None
        if want:
            source = next(
                (
                    o for o in candidates
                    if (int(o.get(major_f) or 0), int(o.get(minor_f) or 0)) == want
                ),
                None,
            )
        source = source or candidates[0]

        for field in (tmp_f, valid_f, no_f, idx_f, major_f, minor_f):
            merged[field] = source.get(field)

    for field in _SHAREABLE_USER_FIELDS:
        if merged.get(field) not in (None, "", "0"):
            continue
        for other in others:
            if other.get(field) not in (None, "", "0"):
                merged[field] = other.get(field)
                break

    return merged


_DEVICE_NAME_LIMIT = 24


def _device_name(full_name, limit=_DEVICE_NAME_LIMIT):
    """Fit an employee name into the device's ``Name=`` field.

    ZKTeco caps USERINFO ``Name=`` at 24 characters and a blind slice mangles
    the surname — "Martin Bundotich Kipkoech" reached the device as
    "Martin Bundotich Kipkoec". Middle names are dropped instead: **first and
    last only, always**, even when the full name would have fitted, so a person
    reads the same on every terminal regardless of how long their name is.

    Degrades further only if first + last still will not fit — initial + last,
    then the surname alone, and a hard slice as the last resort for a single
    name longer than the limit. An empty name (the ``skip_name`` path) is
    returned unchanged.
    """
    parts = (full_name or "").split()
    if not parts:
        return ""

    if len(parts) == 1:
        candidates = [parts[0]]
    else:
        first, last = parts[0], parts[-1]
        candidates = [f"{first} {last}", f"{first[0]}. {last}", last]

    for candidate in candidates:
        if len(candidate) <= limit:
            return candidate
    return candidates[-1][:limit].strip()


def _build_userinfo_command(cmd_id, user_id, employee_name, fallback_privilege, tpl, caps=None):
    def field(key, default=""):
        if tpl is None:
            return default
        v = tpl.get(key)
        return "" if v is None else str(v)

    privilege      = field("privilege")     or str(fallback_privilege or "0")
    password       = field("password")
    card           = field("card")
    vice_card      = field("vice_card")

    # A credential the terminal cannot hold is sent blank rather than omitted:
    # ZKTeco USERINFO is positional-by-key, and a device given a card number it
    # cannot store has been seen to reject the whole record.
    if not _device_supports(caps, "supports_card"):
        card = ""
        vice_card = ""
    if not _device_supports(caps, "supports_password"):
        password = ""
    user_group     = field("user_group")    or "1"
    timezone_group = field("timezone_group")
    verify_mode    = field("verify_mode")   or "-1"
    start_datetime = field("start_datetime") or "0"
    end_datetime   = field("end_datetime")   or "0"

    return (
        f"C:{cmd_id}:DATA UPDATE USERINFO"
        f"\tPIN={user_id}"
        f"\tName={_device_name(employee_name)}"
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


def _queue_biodata_for_user(device_sn, user_id, employee=None, tpl=None, force=False, caps=None,
                            skip_modalities=()):
    """Push the user's biometric templates to one device.

    Two filters apply, in order:

    1. Modalities the terminal does not support (its Biometric Setting
       capability flags) are never pushed — a fingerprint-only reader is not
       sent a face template, whatever the employee has enrolled elsewhere.
    2. Modalities the device already holds byte-for-byte are skipped:
       re-pushing them is a no-op, and a no-op that would overwrite good
       on-device data if our stored copy were ever corrupted. ``force``
       bypasses this second filter (fresh add, re-add, PIN re-key) but never
       the first.

    ``skip_modalities`` (e.g. ``("Face",)``) are never pushed, whatever the
    device supports — the operator opted out of that modality for this push.
    """
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
        tpl = _get_template_row(employee, device_sn)
    if not tpl:
        return 0

    # Fetched even when forcing: ``force`` only bypasses the already-on-device
    # comparison, never the Type= resolution, which needs this device's own row.
    own = _get_device_template_row(employee, device_sn)
    if caps is None:
        caps = _device_capabilities(device_sn)
    device_codes = _device_type_codes(device_sn)

    sent = 0
    skipped = 0
    unsupported = []
    for label, type_code, no_f, idx_f, valid_f, major_f, minor_f, type_f, tmp_f in _BIO_TYPES:
        if label in skip_modalities:
            continue
        template = tpl.get(tmp_f)
        if not template:
            continue
        if not tpl.get(valid_f):
            continue

        if not _device_supports(caps, _MODALITY_CAPABILITY[label]):
            unsupported.append(label)
            continue

        if (
            not force
            and own is not None
            and not own.get("deleted")
            and own.get(valid_f)
            and own.get(tmp_f) == template
        ):
            skipped += 1
            continue

        # Type= is resolved against the TARGET device, in this order:
        #   1. the code this device itself reported for this modality on this
        #      employee's own row;
        #   2. the code it reports for this modality generally;
        #   3. the fleet-wide default.
        # ``tpl`` may have come from another terminal (the enroll-once
        # fallback), and its neighbour's code must never be echoed to this one.
        type_value = (
            (own.get(type_f) if own else None)
            or device_codes.get(label)
            or type_code
        )

        cmd_id = frappe.generate_hash(length=10)
        command = (
            f"C:{cmd_id}:DATA UPDATE BIODATA"
            f"\tPin={user_id}"
            f"\tNo={tpl.get(no_f) or 0}"
            f"\tIndex={tpl.get(idx_f) or 0}"
            f"\tValid=1"
            f"\tDuress=0"
            f"\tType={type_value}"
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

    if skipped or unsupported:
        note = f"Biodata push {device_sn}/{user_id}: {sent} queued"
        if skipped:
            note += f", {skipped} already on device"
        if unsupported:
            note += f", not supported by device: {', '.join(unsupported)}"
        frappe.logger().info(note)

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
    employee_name = frappe.db.get_value("Employee", employee, "employee_name") or ""

    for device_sn in (device_sns or []):
        try:
            # Resolved per device: each terminal prefers its own enrollment,
            # and only accepts the credentials it can actually store.
            tpl  = _get_template_row(employee, device_sn)
            caps = _device_capabilities(device_sn)
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
                "command":       _build_userinfo_command(add_id, new_pin, employee_name, "0", tpl, caps),
            })

            # The old PIN was just deleted from the device, so the new PIN needs
            # every template re-pushed even though the stored bytes are unchanged.
            _queue_biodata_for_user(
                device_sn, new_pin, employee=employee, tpl=tpl, force=True, caps=caps
            )
        except Exception as e:
            frappe.log_error(
                f"PIN resync failed for device {device_sn} ({old_pin}->{new_pin}): {e}",
                "Biometric PIN Resync",
            )
