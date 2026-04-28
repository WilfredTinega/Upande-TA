# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
import json
import urllib.request
from frappe.model.document import Document


class BiometricUsers(Document):
    pass


@frappe.whitelist()
def send_device_command(doc_name, row_name, command_type, override=None):
    doc = frappe.get_doc("Biometric Users", doc_name)

    row = None
    for r in doc.device_users:
        if r.name == row_name:
            row = r
            break

    if not row:
        frappe.throw(f"Row {row_name} not found in device_users")

    if isinstance(override, str):
        override = json.loads(override)
    override = override or {}

    user_id       = override.get("user_id")       or row.user_id
    employee_name = override.get("employee_name") or row.employee_name
    privilege     = override.get("privilege")     or row.privilege or "0"

    cmd_id = frappe.generate_hash(length=10)

    if command_type in ("Add User", "Update User"):
        if not user_id or not employee_name:
            frappe.throw("User ID and Employee Name are required")

        command = (
            f"C:{cmd_id}:DATA UPDATE USERINFO"
            f"\tPIN={user_id}"
            f"\tName={employee_name}"
            f"\tPri={privilege}"
            f"\tPasswd="
            f"\tCard=0"
        )

        if command_type == "Add User":
            row.add_user = command
        else:
            row.update_user = command

        row.command_status = "Pending"
        row.status         = "Active"
        row.employee_name  = employee_name
        row.privilege      = privilege

    elif command_type == "Delete User":
        if not user_id:
            frappe.throw("User ID is required")

        command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
        doc.device_users = [r for r in doc.device_users if r.name != row_name]

    else:
        frappe.throw(f"Unknown command type: {command_type}")

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    payload = {
        "command_id":    cmd_id,
        "command_type":  command_type,
        "device_sn":     doc.device_sn,
        "user_id":       user_id,
        "employee_name": employee_name,
        "command":       command
    }

    _post_to_nodered(payload)

    return {"status": "sent", "command_id": cmd_id, "command": command}


@frappe.whitelist()
def get_devices():
    return frappe.get_all(
        "Biometric Users",
        fields=["name", "device_sn", "device_location"],
        order_by="device_location asc"
    )


@frappe.whitelist()
def get_device_users(device_sn):
    if not frappe.db.exists("Biometric Users", device_sn):
        return []
    doc = frappe.get_doc("Biometric Users", device_sn)
    return [
        {
            "row_name":      r.name,
            "user_id":       r.user_id,
            "employee_name": r.employee_name,
            "privilege":     r.privilege or "0",
            "status":        r.status or "Active"
        }
        for r in doc.device_users
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
    settings = frappe.get_single("Server Settings")
    return {
        "is_delete": settings.is_delete or 0,
        "is_update": settings.is_update or 0
    }


@frappe.whitelist()
def bulk_command(device_sn, users, command_type):
    if isinstance(users, str):
        users = json.loads(users)

    if not device_sn or not users:
        frappe.throw("device_sn and users are required")

    if not frappe.db.exists("Biometric Users", device_sn):
        frappe.throw(f"Device {device_sn} not found")

    doc    = frappe.get_doc("Biometric Users", device_sn)
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

            if command_type in ("Add User", "Update User"):
                if not employee_name:
                    failed.append({"user_id": user_id, "reason": "Missing name"})
                    continue

                command = (
                    f"C:{cmd_id}:DATA UPDATE USERINFO"
                    f"\tPIN={user_id}"
                    f"\tName={employee_name}"
                    f"\tPri={privilege}"
                    f"\tPasswd="
                    f"\tCard=0"
                )

                existing_row = next((r for r in doc.device_users if r.user_id == user_id), None)

                if existing_row:
                    existing_row.employee_name  = employee_name
                    existing_row.privilege      = privilege
                    existing_row.command_status = "Pending"
                    existing_row.status         = "Active"
                    if command_type == "Add User":
                        existing_row.add_user = command
                    else:
                        existing_row.update_user = command
                else:
                    doc.append("device_users", {
                        "user_id":         user_id,
                        "employee_name":   employee_name,
                        "privilege":       privilege,
                        "status":          "Active",
                        "command_status":  "Pending",
                        "enrollment_date": now,
                        "add_user":        command if command_type == "Add User" else ""
                    })

            elif command_type == "Delete User":
                command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={user_id}"
                doc.device_users = [r for r in doc.device_users if r.user_id != user_id]

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

            queued.append({"user_id": user_id, "command_id": cmd_id})

        except Exception as e:
            failed.append({"user_id": user.get("user_id"), "reason": str(e)})

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status":  "done",
        "queued":  len(queued),
        "failed":  len(failed),
        "details": queued,
        "errors":  failed
    }


def _post_to_nodered(payload):
    try:
        settings = frappe.get_single("Server Settings")
        ip       = (settings.server_ip or "").strip()
        port     = (settings.server_port or "").strip()
        endpoint = (settings.end_point or "").strip()

        if not ip or not port or not endpoint:
            frappe.log_error("Server Settings incomplete", "Node-RED Post Error")
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