# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt
import frappe
import json
import urllib.request
from frappe.model.document import Document


class BiometricUser(Document):
    pass


@frappe.whitelist()
def send_device_command(name, command_type, override=None):
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

    if command_type in ("Add User", "Update User"):
        if not user_id or not employee_name:
            frappe.throw("User ID and Employee Name are required")

        tpl = _get_template_row(doc.employee)
        command = _build_userinfo_command(cmd_id, user_id, employee_name, privilege, tpl)

        if command_type == "Add User":
            doc.add_user = command
        else:
            doc.update_user = command

        doc.command_status = "Pending"
        doc.status         = "Active"
        doc.employee_name  = employee_name
        doc.privilege      = privilege
        doc.save(ignore_permissions=True)

    elif command_type == "Delete User":
        if not user_id:
            frappe.throw("User ID is required")

        command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
        frappe.delete_doc("Biometric User", name, ignore_permissions=True)

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
def get_employees(status="Active", employee=None, designation=None, department=None):
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

    employees = frappe.get_all(
        "Employee",
        filters=filters,
        fields=[
            "name", "first_name", "last_name", "attendance_device_id",
            "designation", "department"
        ],
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
            "department":  e.department
        })
    return result


@frappe.whitelist()
def get_active_filter_options(department=None, designation=None):
    all_employees = frappe.get_all(
        "Employee",
        fields=["designation", "department"]
    )
    departments = sorted({e.department for e in all_employees if e.department})

    designation_pool = all_employees
    if department:
        designation_pool = [e for e in designation_pool if e.department == department]
    designations = sorted({e.designation for e in designation_pool if e.designation})

    employee_filters = {"status": "Active"}
    if department:
        employee_filters["department"] = department
    if designation:
        employee_filters["designation"] = designation
    employee_count = frappe.db.count("Employee", employee_filters)

    return {
        "designations":      designations,
        "departments":       departments,
        "designation_count": len(designations),
        "department_count":  len(departments),
        "employee_count":    employee_count
    }


@frappe.whitelist()
def get_server_settings():
    settings = frappe.get_single("Biometric Setting")
    return {
        "is_delete": getattr(settings, "is_delete", 0) or 0,
        "is_update": getattr(settings, "is_update", 0) or 0,
    }


@frappe.whitelist()
def bulk_command(device_sn, users, command_type):
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

            if not user_id:
                failed.append({"user_id": user_id, "reason": "Missing PIN"})
                continue

            cmd_id = frappe.generate_hash(length=10)
            record_name = f"{device_sn}-{user_id}"

            tpl = None

            if command_type in ("Add User", "Update User"):
                if not employee_name:
                    failed.append({"user_id": user_id, "reason": "Missing name"})
                    continue

                employee = frappe.db.get_value(
                    "Employee",
                    {"attendance_device_id": user_id},
                    "name"
                )

                tpl = _get_template_row(employee)
                command = _build_userinfo_command(cmd_id, user_id, employee_name, privilege, tpl)

                if frappe.db.exists("Biometric User", record_name):
                    rec = frappe.get_doc("Biometric User", record_name)
                    rec.employee_name  = employee_name
                    rec.privilege      = privilege
                    rec.command_status = "Pending"
                    rec.status         = "Active"
                    if employee and not rec.employee:
                        rec.employee = employee
                    if command_type == "Add User":
                        rec.add_user = command
                    else:
                        rec.update_user = command
                    rec.save(ignore_permissions=True)
                else:
                    rec = frappe.get_doc({
                        "doctype":         "Biometric User",
                        "device_sn":       device_sn,
                        "user_id":         user_id,
                        "employee":        employee,
                        "employee_name":   employee_name,
                        "privilege":       privilege,
                        "status":          "Active",
                        "command_status":  "Pending",
                        "enrollment_date": now,
                        "add_user":        command if command_type == "Add User" else ""
                    })
                    rec.insert(ignore_permissions=True)

            elif command_type == "Delete User":
                command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
                if frappe.db.exists("Biometric User", record_name):
                    frappe.delete_doc("Biometric User", record_name, ignore_permissions=True)

            else:
                frappe.throw(f"Unknown command type: {command_type}")

            _post_to_nodered({
                "command_id":    cmd_id,
                "command_type":  command_type,
                "device_sn":     device_sn,
                "user_id":       user_id,
                "employee_name": employee_name,
                "command":       command
            })

            if command_type == "Add User":
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


_BIO_TYPES = (
    ("Fingerprint", 1, "fp_bio_no",   "fp_bio_index",   "fp_valid",   "fp_major_ver",   "fp_minor_ver",   "fingerprint_template"),
    ("Face",        9, "face_bio_no", "face_bio_index", "face_valid", "face_major_ver", "face_minor_ver", "face_template"),
    ("Palm",        8, "palm_bio_no", "palm_bio_index", "palm_valid", "palm_major_ver", "palm_minor_ver", "palm_template"),
)


_TEMPLATE_FIELDS = (
    "name", "source_device",
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
        "Biometric Template",
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
    card           = field("card")          or "0"
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

    if tpl.get("source_device") == device_sn:
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
