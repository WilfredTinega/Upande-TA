# Copyright (c) 2026, Upande LTD and contributors

import frappe
import json
import urllib.request
from frappe.model.document import Document

class BiometricUser(Document):
    pass

def _employee_has_custom_farm():
    return "custom_farm" in frappe.db.get_table_columns("Employee")

def _get_user_row(device_sn, user_id):
    rows = frappe.get_all(
        "Biometric User",
        filters={"device_sn": device_sn, "user_id": user_id},
        fields=["name"],
        limit=1,
    )
    if not rows:
        return None
    return frappe.get_doc("Biometric User", rows[0].name)

def _delete_user_row(device_sn, user_id):
    existing = _get_user_row(device_sn, user_id)
    if not existing:
        return False
    frappe.delete_doc(
        "Biometric User", existing.name,
        ignore_permissions=True, force=True,
    )
    return True

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

def _upsert_user_row(device_sn, user_id, values):
    existing = _get_user_row(device_sn, user_id)
    if existing:
        for k, v in values.items():
            existing.set(k, v)
        existing.save(ignore_permissions=True)
        return existing
    row = frappe.get_doc({
        "doctype":   "Biometric User",
        "device_sn": device_sn,
        "user_id":   user_id,
        **values,
    })
    row.insert(ignore_permissions=True)
    return row

@frappe.whitelist()
def hydrate_users_from_templates(device_sn):
    if not device_sn:
        frappe.throw("device_sn is required")
    if not frappe.db.exists("DocType", "Biometric Template"):
        return {"created": 0, "skipped": 0, "reason": "Biometric Template doctype not migrated"}

    parent_name = frappe.db.get_value("Biometric Template", {"device_sn": device_sn}, "name")
    if not parent_name:
        return {"created": 0, "skipped": 0, "reason": "No Biometric Template for this device"}

    template_rows = frappe.get_all(
        "Bio Template",
        filters={
            "parent":      parent_name,
            "parentfield": "bio_templates",
            "deleted":     0,
        },
        fields=["employee", "employee_name", "user_id", "privilege"],
    )
    if not template_rows:
        return {"created": 0, "skipped": 0}

    existing_pins = {
        r.user_id
        for r in frappe.get_all(
            "Biometric User",
            filters={"device_sn": device_sn, "user_id": ["in", [t.user_id for t in template_rows if t.user_id]]},
            fields=["user_id"],
        )
    }

    created = 0
    skipped = 0
    for t in template_rows:
        if not t.user_id or not t.employee:
            skipped += 1
            continue
        if t.user_id in existing_pins:
            skipped += 1
            continue
        _upsert_user_row(device_sn, t.user_id, {
            "employee":      t.employee,
            "employee_name": t.employee_name or "",
            "privilege":     t.privilege or "0",
            "status":        "Active",
        })
        created += 1

    if created:
        frappe.db.commit()
    return {"created": created, "skipped": skipped}

@frappe.whitelist()
def send_device_command(name, command_type, override=None):
    if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
        frappe.throw("Enable Users")

    doc = frappe.get_doc("Biometric User", name)

    if isinstance(override, str):
        override = json.loads(override)
    override = override or {}

    user_id       = override.get("user_id")       or doc.user_id
    employee_name = override.get("employee_name") or doc.employee_name
    privilege     = override.get("privilege")     or doc.privilege or "0"
    device_sn     = doc.device_sn

    if not device_sn:
        frappe.throw("device_sn is required")

    cmd_id = frappe.generate_hash(length=10)

    tpl = None

    if command_type == "Add User":
        if not user_id or not employee_name:
            frappe.throw("User ID and Employee Name are required")

        tpl = _get_template_row(doc.employee)
        command = _build_userinfo_command(cmd_id, user_id, employee_name, privilege, tpl)

        _upsert_user_row(device_sn, user_id, {
            "employee":       doc.employee,
            "employee_name":  employee_name,
            "privilege":      privilege,
            "status":         "Active",
            "command_status": "Pending",
            "add_user":       command,
        })
        _set_template_deleted_flag(device_sn, user_id, False)

    elif command_type == "Delete User":
        if not user_id:
            frappe.throw("User ID is required")

        command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
        _delete_user_row(device_sn, user_id)
        _set_template_deleted_flag(device_sn, user_id, True)

    else:
        frappe.throw(f"Unknown command type: {command_type}")

    frappe.db.commit()

    _post_to_nodered({
        "command_id":    cmd_id,
        "command_type":  command_type,
        "device_sn":     device_sn,
        "user_id":       user_id,
        "employee_name": employee_name,
        "command":       command
    })

    biodata_queued = 0
    if command_type == "Add User":
        biodata_queued = _queue_biodata_for_user(device_sn, user_id, doc.employee, tpl)

    return {
        "status":         "sent",
        "command_id":     cmd_id,
        "command":        command,
        "biodata_queued": biodata_queued
    }

@frappe.whitelist()
def get_devices():
    settings = frappe.get_single("Biometric Setting")
    return [
        {
            "name":            d.device_sn,
            "device_sn":       d.device_sn,
            "device_location": d.device_location or d.device_sn
        }
        for d in (settings.devices or [])
    ]

@frappe.whitelist()
def get_device_users(device_sn):
    rows = frappe.get_all(
        "Biometric User",
        filters={"device_sn": device_sn},
        fields=["name", "user_id", "employee_name", "privilege", "status"],
        order_by="employee_name asc"
    )
    return [
        {
            "row_name":      r.name,
            "user_id":       r.user_id,
            "employee_name": r.employee_name,
            "privilege":     r.privilege or "0",
            "status":        r.status or "Active"
        }
        for r in rows
    ]

@frappe.whitelist()
def get_employees(status="Active", employee=None, designation=None, department=None,
                  company=None, farm=None):
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
    if farm and has_farm:
        filters["custom_farm"] = farm

    fields = [
        "name", "first_name", "last_name", "attendance_device_id",
        "designation", "department", "company"
    ]
    if has_farm:
        fields.append("custom_farm")

    employees = frappe.get_all(
        "Employee",
        filters=filters,
        fields=fields,
        order_by="first_name asc"
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
            "farm":        e.get("custom_farm") if has_farm else None
        })
    return result

@frappe.whitelist()
def get_active_filter_options(department=None, designation=None, company=None, farm=None):
    has_farm = _employee_has_custom_farm()
    fields = ["designation", "department", "company"]
    if has_farm:
        fields.append("custom_farm")
    all_employees = frappe.get_all("Employee", fields=fields)

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
    if farm and has_farm:
        employee_filters["custom_farm"] = farm
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
        "employee_count":    employee_count
    }

@frappe.whitelist()
def bulk_command(device_sn, users, command_type):
    if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
        frappe.throw("Enable Users")

    if isinstance(users, str):
        users = json.loads(users)

    if not device_sn or not users:
        frappe.throw("device_sn and users are required")

    queued = []
    failed = []
    now    = frappe.utils.now_datetime()

    for user in users:
        try:
            user_id       = str(user.get("user_id") or "").strip()
            employee_name = str(user.get("employee_name") or "").strip()[:24]
            privilege     = str(user.get("privilege") or "0").strip()
            skip_name     = bool(user.get("skip_name"))

            if not user_id:
                failed.append({"user_id": user_id, "reason": "Missing PIN"})
                continue

            cmd_id = frappe.generate_hash(length=10)

            tpl = None

            if command_type == "Add User":
                if not employee_name and not skip_name:
                    failed.append({"user_id": user_id, "reason": "Missing name"})
                    continue

                employee = frappe.db.get_value(
                    "Employee",
                    {"attendance_device_id": user_id},
                    "name"
                )

                tpl = _get_template_row(employee)
                device_name = "" if skip_name else employee_name
                command = _build_userinfo_command(cmd_id, user_id, device_name, privilege, tpl)

                _upsert_user_row(device_sn, user_id, {
                    "employee":        employee,
                    "employee_name":   employee_name,
                    "privilege":       privilege,
                    "status":          "Active",
                    "command_status":  "Pending",
                    "enrollment_date": now,
                    "add_user":        command,
                })
                _set_template_deleted_flag(device_sn, user_id, False)

            elif command_type == "Update User":
                existing = _get_user_row(device_sn, user_id)
                if not existing:
                    failed.append({"user_id": user_id, "reason": "User not on device"})
                    continue

                employee = frappe.db.get_value(
                    "Employee",
                    {"attendance_device_id": user_id},
                    "name"
                )

                tpl = _get_template_row(employee)
                device_name = "" if skip_name else employee_name
                command = _build_userinfo_command(cmd_id, user_id, device_name, privilege, tpl)

                _upsert_user_row(device_sn, user_id, {
                    "employee":       employee,
                    "employee_name":  employee_name,
                    "privilege":      privilege,
                    "command_status": "Pending",
                    "add_user":       command,
                })

            elif command_type == "Delete User":
                command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
                _delete_user_row(device_sn, user_id)
                _set_template_deleted_flag(device_sn, user_id, True)

            else:
                frappe.throw(f"Unknown command type: {command_type}")

            _post_to_nodered({
                "command_id":    cmd_id,
                "command_type":  command_type,
                "device_sn":     device_sn,
                "user_id":       user_id,
                "employee_name": employee_name,
                "skip_name":     1 if (command_type == "Add User" and skip_name) else 0,
                "command":       command
            })

            if command_type in ("Add User", "Update User"):
                _queue_biodata_for_user(device_sn, user_id, employee=employee, tpl=tpl)

            queued.append({"user_id": user_id, "command_id": cmd_id})

        except Exception as e:
            failed.append({"user_id": user.get("user_id"), "reason": str(e)})

    frappe.db.commit()

    return {
        "status":  "done",
        "queued":  len(queued),
        "failed":  len(failed),
        "details": queued,
        "errors":  failed
    }

@frappe.whitelist()
def add_employees_to_devices(employees, device_sns):
    if not frappe.db.get_single_value("Biometric Setting", "enable_users"):
        frappe.throw("Enable Users")

    if isinstance(employees, str):
        employees = json.loads(employees)
    if isinstance(device_sns, str):
        device_sns = json.loads(device_sns)

    device_sns = [str(sn).strip() for sn in (device_sns or []) if str(sn).strip()]
    if not device_sns:
        frappe.throw("At least one device is required")
    if not employees:
        frappe.throw("At least one employee is required")

    valid_devices = {
        row.device_sn
        for row in (frappe.get_single("Biometric Setting").devices or [])
        if row.device_sn
    }
    unknown = [sn for sn in device_sns if sn not in valid_devices]
    if unknown:
        frappe.throw(f"Unknown device(s): {', '.join(unknown)}")

    users = []
    skipped = []
    for emp_name in employees:
        emp = frappe.db.get_value(
            "Employee",
            emp_name,
            ["name", "first_name", "last_name", "attendance_device_id"],
            as_dict=True,
        )
        if not emp:
            skipped.append({"employee": emp_name, "reason": "Employee not found"})
            continue
        pin = (emp.attendance_device_id or "").strip()
        if not pin:
            skipped.append({"employee": emp_name, "reason": "No attendance_device_id"})
            continue
        full_name = f"{emp.first_name or ''} {emp.last_name or ''}".strip() or emp.name
        users.append({
            "user_id":       pin,
            "employee_name": full_name,
            "privilege":     "0",
            "skip_name":     0,
        })

    results = []
    for sn in device_sns:
        if not users:
            break
        result = bulk_command(sn, users, "Add User")
        results.append({"device_sn": sn, **result})

    return {
        "status":  "done",
        "results": results,
        "skipped": skipped,
    }

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
            "name"
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
            "command":       command
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
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            frappe.logger().info(f"Node-RED response: {resp.status}")

    except Exception as e:
        frappe.log_error(f"Node-RED post failed: {str(e)}", "Node-RED Post Error")
