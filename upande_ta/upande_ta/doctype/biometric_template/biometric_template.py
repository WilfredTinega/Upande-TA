# Copyright (c) 2026, Upande LTD and contributors

import json

import frappe
from frappe.model.document import Document

class BiometricTemplate(Document):
    def before_insert(self):
        if not frappe.flags.get("allow_biometric_parent_insert"):
            frappe.throw(
                "Biometric Template parents are created automatically when you add a "
                "device in Biometric Setting. Add the device there instead."
            )

    def validate(self):
        if self.device_location and not self.device_sn:
            self.device_sn = _lookup_device_sn(self.device_location)
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


def _lookup_device_sn(device_location):
    if not device_location:
        return ""
    row = frappe.db.get_value(
        "Biometric Device",
        {"parent": "Biometric Setting", "device_location": device_location},
        "device_sn",
    )
    return row or ""


def _is_registered_device(device_sn):
    if not device_sn:
        return False
    return bool(frappe.db.exists(
        "Biometric Device",
        {"parent": "Biometric Setting", "parentfield": "devices", "device_sn": device_sn},
    ))

@frappe.whitelist()
def get_setting_devices():
    rows = frappe.get_all(
        "Biometric Device",
        filters={"parent": "Biometric Setting"},
        fields=["device_sn", "device_location"],
        order_by="idx asc",
    )
    return rows

_USER_FIELDS = {
    "card":           "card",
    "vice_card":      "vice_card",
    "password":       "password",
    "privilege":      "privilege",
    "group":          "user_group",
    "timezone_group": "timezone_group",
    "verify_mode":    "verify_mode",
    "start_datetime": "start_datetime",
    "end_datetime":   "end_datetime",
}

_BIO_PREFIX = {
    "fingerprint": "fp",
    "face":        "face",
    "palm":        "palm",
}

_BIO_TEMPLATE_FIELD = {
    "fp":   "fingerprint_template",
    "face": "face_template",
    "palm": "palm_template",
}

def _str(v):
    return "" if v is None else str(v).strip()

def _int(v, default=0):
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default

def _parse_wanted_pins(raw):
    if raw in (None, ""):
        return None
    s = _str(raw)
    if not s:
        return None
    if s.startswith("["):
        try:
            return {_str(p) for p in json.loads(s) if _str(p)}
        except (TypeError, ValueError):
            return None
    return {s}

def _ensure_biometric_template_parent(device_sn):
    if not device_sn:
        frappe.throw("device_sn is required to resolve a Biometric Template parent")

    device_location = frappe.db.get_value(
        "Biometric Device",
        {"parent": "Biometric Setting", "device_sn": device_sn},
        "device_location",
    ) or device_sn

    existing = frappe.db.get_value(
        "Biometric Template",
        {"device_sn": device_sn},
        "name",
    )
    if existing:
        return existing

    doc = frappe.get_doc({
        "doctype":         "Biometric Template",
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

@frappe.whitelist(allow_guest=True)
def store_biotemplate():
    data      = frappe.request.get_json() or {}
    bio_type  = _str(data.get("bio_type"))
    device_sn = _str(data.get("device_sn") or data.get("source_device"))
    user_id   = _str(data.get("user_id") or data.get("employee_id") or data.get("employee"))

    if not user_id:
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = {"status": "error", "message": "Missing user_id"}
        return

    if not device_sn:
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = {"status": "error", "message": "Missing device_sn"}
        return

    if not _is_registered_device(device_sn):
        frappe.response["http_status_code"] = 404
        frappe.response["message"] = {
            "status":    "error",
            "message":   f"Device {device_sn} is not registered in Biometric Setting",
            "device_sn": device_sn,
        }
        return

    kind = bio_type.lower()
    is_user_record = kind == "user"
    prefix = _BIO_PREFIX.get(kind)

    if not is_user_record and not prefix:
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = {
            "status":  "error",
            "message": f"Unsupported bio_type: {bio_type!r}",
        }
        return

    if not is_user_record and not _str(data.get("template")):
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = {"status": "error", "message": "Missing template"}
        return

    if device_sn and not is_user_record:
        wanted_raw = frappe.cache().get_value(f"poll_biodata_filter:{device_sn}")
        wanted_pins = _parse_wanted_pins(wanted_raw)
        if wanted_pins and user_id not in wanted_pins:
            frappe.response["message"] = {
                "status":    "skipped",
                "message":   f"PIN {user_id} filtered out (requested {sorted(wanted_pins)})",
                "user_id":   user_id,
                "device_sn": device_sn,
            }
            return

    employee_row = frappe.db.get_value(
        "Employee",
        {"attendance_device_id": user_id},
        ["name", "employee_name"],
        as_dict=True,
    )
    if not employee_row:
        frappe.response["message"] = {
            "status":    "skipped",
            "message":   f"No employee found for PIN {user_id}",
            "user_id":   user_id,
            "device_sn": device_sn,
        }
        return
    employee_name = employee_row.name
    employee_full_name = employee_row.employee_name or ""

    new_values = {}

    if is_user_record:
        for src, dst in _USER_FIELDS.items():
            new_values[dst] = _str(data.get(src))
    else:
        new_values.update({
            f"{prefix}_bio_no":    _int(data.get("bio_no")),
            f"{prefix}_bio_index": _int(data.get("bio_index")),
            f"{prefix}_valid":     _int(data.get("valid"), 1),
            f"{prefix}_major_ver": _int(data.get("major_ver")),
            f"{prefix}_minor_ver": _int(data.get("minor_ver")),
            f"{prefix}_size":      _int(data.get("size")),
            f"{prefix}_raw_log":   _str(data.get("raw_log")),
            _BIO_TEMPLATE_FIELD[prefix]: _str(data.get("template")),
        })

    parent_name = _ensure_biometric_template_parent(device_sn)

    existing = frappe.db.get_value(
        "Bio Template",
        {
            "parent":      parent_name,
            "parentfield": "bio_templates",
            "employee":    employee_name,
        },
        ("name", "employee_name") + tuple(new_values),
        as_dict=True,
    )

    changed_fields = []
    if existing:
        changed = {k: v for k, v in new_values.items() if existing.get(k) != v}
        if changed:
            changed["deleted"] = 0
            if employee_full_name and existing.get("employee_name") != employee_full_name:
                changed["employee_name"] = employee_full_name
            frappe.db.set_value(
                "Bio Template", existing["name"], changed,
                update_modified=False,
            )
            frappe.db.commit()
            status = "updated"
            changed_fields = sorted(changed.keys())
            message = (
                f"Existing Bio Template row for employee {employee_name} on device "
                f"{device_sn} updated; {len(changed_fields)} field(s) changed."
            )
        else:
            status = "unchanged"
            message = (
                f"Bio Template row for employee {employee_name} on device {device_sn} "
                f"already up to date; no fields changed."
            )
        row_name = existing["name"]
    else:
        row_name = frappe.generate_hash(length=10)
        idx = (frappe.db.sql(
            """
            SELECT COALESCE(MAX(idx), 0) + 1
            FROM `tabBio Template`
            WHERE parent = %s AND parentfield = %s
            """,
            (parent_name, "bio_templates"),
        )[0][0]) or 1

        row = frappe.get_doc({
            "doctype":     "Bio Template",
            "name":        row_name,
            "parent":      parent_name,
            "parenttype":  "Biometric Template",
            "parentfield": "bio_templates",
            "idx":         idx,
            "employee":      employee_name,
            "employee_name": employee_full_name,
            "user_id":       user_id,
            **new_values,
        })
        try:
            row.db_insert()
            frappe.db.commit()
            status = "inserted"
            changed_fields = sorted(new_values.keys())
            message = (
                f"New Bio Template row created for employee {employee_name} on "
                f"device {device_sn}."
            )
        except frappe.db.IntegrityError:
            frappe.db.rollback()
            existing_name = frappe.db.get_value(
                "Bio Template",
                {
                    "parent":      parent_name,
                    "parentfield": "bio_templates",
                    "employee":    employee_name,
                },
                "name",
            )
            if not existing_name:
                raise
            update_payload = {k: v for k, v in new_values.items() if v not in (None, "")}
            update_payload["deleted"] = 0
            if employee_full_name:
                update_payload["employee_name"] = employee_full_name
            frappe.db.set_value(
                "Bio Template", existing_name, update_payload,
                update_modified=False,
            )
            frappe.db.commit()
            row_name = existing_name
            status = "updated"
            changed_fields = sorted(update_payload.keys())
            message = (
                f"Concurrent insert detected for employee {employee_name} on device "
                f"{device_sn}; merged into existing row instead of creating a duplicate."
            )

    frappe.response["message"] = {
        "status":         status,
        "message":        message,
        "changed_fields": changed_fields,
        "employee":       employee_name,
        "user_id":        user_id,
        "bio_type":       bio_type,
        "device_sn":      device_sn,
        "parent":    parent_name,
        "row_name":  row_name,
    }
