# Copyright (c) 2026, Upande LTD and contributors

import frappe
from frappe.model.document import Document

class BiometricTemplate(Document):
    def validate(self):
        if self.device_sn and not self.device_name:
            self.device_name = _lookup_device_name(self.device_sn)

def _lookup_device_name(device_sn):
    if not device_sn:
        return ""
    row = frappe.db.get_value(
        "Biometric Device",
        {"parent": "Biometric Setting", "device_sn": device_sn},
        "device_location",
    )
    return row or ""

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

def _ensure_biometric_template_parent(device_sn):
    if not device_sn:
        frappe.throw("device_sn is required to resolve a Biometric Template parent")

    device_name = frappe.db.get_value(
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
        "doctype":     "Biometric Template",
        "device_sn":   device_sn,
        "device_name": device_name,
    })
    doc.insert(ignore_permissions=True)
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
        frappe.response["message"] = {"status": "error", "error": "Missing user_id"}
        return

    if not device_sn:
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = {"status": "error", "error": "Missing device_sn"}
        return

    kind = bio_type.lower()
    is_user_record = kind == "user"
    prefix = _BIO_PREFIX.get(kind)

    if not is_user_record and not prefix:
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = {
            "status": "error",
            "error":  f"Unsupported bio_type: {bio_type!r}",
        }
        return

    if not is_user_record and not _str(data.get("template")):
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = {"status": "error", "error": "Missing template"}
        return

    if device_sn and not is_user_record:
        wanted_pin = frappe.cache().get_value(f"poll_biodata_filter:{device_sn}")
        if wanted_pin and _str(wanted_pin) != user_id:
            frappe.response["message"] = {
                "status": "skipped",
                "reason": f"PIN {user_id} filtered out (requested {wanted_pin})",
            }
            return

    employee_name = frappe.db.get_value(
        "Employee", {"attendance_device_id": user_id}, "name"
    )
    if not employee_name:
        frappe.response["message"] = {
            "status": "skipped",
            "reason": f"No employee found for PIN {user_id}",
        }
        return

    now = frappe.utils.now_datetime()

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
        ("name",) + tuple(new_values),
        as_dict=True,
    )

    if existing:
        changed = {k: v for k, v in new_values.items() if existing.get(k) != v}
        if changed:
            changed["captured_at"] = now

            changed["deleted"] = 0
            frappe.db.set_value(
                "Bio Template", existing["name"], changed,
                update_modified=False,
            )
            frappe.db.commit()
            status = "updated"
        else:
            status = "unchanged"
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
            "employee":    employee_name,
            "user_id":     user_id,
            "captured_at": now,
            **new_values,
        })
        row.db_insert()
        frappe.db.commit()
        status = "inserted"

    frappe.response["message"] = {
        "status":    status,
        "employee":  employee_name,
        "user_id":   user_id,
        "bio_type":  bio_type,
        "device_sn": device_sn,
        "parent":    parent_name,
        "row_name":  row_name,
    }
