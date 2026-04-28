# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt
import frappe
import json
import urllib.request
from frappe.model.document import Document


class DeviceUsers(Document):

    def add_to_device(self):
        if not self.user_id or not self.employee_name:
            frappe.throw("User ID and Employee Name are required to add a user")

        cmd_id = frappe.generate_hash(length=10)
        command = (
            f"C:{cmd_id}:DATA UPDATE USERINFO"
            f"\tPIN={self.user_id}"
            f"\tName={self.employee_name}"
            f"\tPri={self.privilege or 0}"
            f"\tPasswd={self.password or ''}"
            f"\tCard={self.card or 0}"
        )
        self.add_user       = command
        self.command_status = "Pending"
        self.status         = "Active"

        _post_to_nodered({
            "command_id":    cmd_id,
            "command_type":  "Add User",
            "device_sn":     self.parent,
            "user_id":       self.user_id,
            "employee_name": self.employee_name,
            "command":       command
        })

    def update_on_device(self):
        if not self.user_id or not self.employee_name:
            frappe.throw("User ID and Employee Name are required to update a user")

        cmd_id = frappe.generate_hash(length=10)
        command = (
            f"C:{cmd_id}:DATA UPDATE USERINFO"
            f"\tPIN={self.user_id}"
            f"\tName={self.employee_name}"
            f"\tPri={self.privilege or 0}"
            f"\tPasswd={self.password or ''}"
            f"\tCard={self.card or 0}"
        )
        self.update_user    = command
        self.command_status = "Pending"

        _post_to_nodered({
            "command_id":    cmd_id,
            "command_type":  "Update User",
            "device_sn":     self.parent,
            "user_id":       self.user_id,
            "employee_name": self.employee_name,
            "command":       command
        })

    def delete_from_device(self):
        if not self.user_id:
            frappe.throw("User ID is required to delete a user")

        cmd_id = frappe.generate_hash(length=10)
        command = f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={self.user_id}"

        self.delete_user    = command
        self.command_status = "Pending"
        self.status         = "Pending Delete"

        _post_to_nodered({
            "command_id":   cmd_id,
            "command_type": "Delete User",
            "device_sn":    self.parent,
            "user_id":      self.user_id,
            "command":      command
        })


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