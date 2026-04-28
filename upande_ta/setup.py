import frappe


MODULE = "Upande TA"


def after_install():
    create_employee_biometric_template_doctype()
    create_employee_biometric_fields()
    frappe.db.commit()


def after_migrate():
    if not frappe.db.exists("DocType", "Employee Biometric Template"):
        create_employee_biometric_template_doctype()
    create_employee_biometric_fields()
    frappe.db.commit()


def create_employee_biometric_fields():
    fields = [
        {
            "fieldname":    "biometric_section",
            "fieldtype":    "Section Break",
            "label":        "Biometric Templates",
            "insert_after": "shift_request_approver",
        },
        {
            "fieldname":    "biometric_templates",
            "fieldtype":    "Table",
            "label":        "Biometric Templates",
            "options":      "Employee Biometric Template",
            "insert_after": "biometric_section",
        },
        {
            "fieldname":    "biometric_section_end",
            "fieldtype":    "Section Break",
            "insert_after": "biometric_templates",
        },
    ]

    for f in fields:
        custom_field_name = f"Employee-{f['fieldname']}"
        values = {
            "dt":           "Employee",
            "module":       MODULE,
            "fieldname":    f["fieldname"],
            "fieldtype":    f["fieldtype"],
            "label":        f.get("label", ""),
            "options":      f.get("options", ""),
            "insert_after": f["insert_after"],
            "collapsible":  f.get("collapsible", 0),
        }

        if frappe.db.exists("Custom Field", custom_field_name):
            doc = frappe.get_doc("Custom Field", custom_field_name)
            doc.update(values)
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({"doctype": "Custom Field", **values}).insert(ignore_permissions=True)

    frappe.clear_cache(doctype="Employee")


def create_employee_biometric_template_doctype():
    if frappe.db.exists("DocType", "Employee Biometric Template"):
        return

    frappe.get_doc({
        "doctype":       "DocType",
        "name":          "Employee Biometric Template",
        "module":        MODULE,
        "custom":        1,
        "istable":       1,
        "editable_grid": 1,
        "fields": [
            {
                "fieldname":    "bio_type",
                "fieldtype":    "Select",
                "label":        "Type",
                "options":      "Fingerprint\nFace\nPalm\nCard",
                "in_list_view": 1,
                "reqd":         1,
                "columns":      2,
            },
            {
                "fieldname":    "bio_no",
                "fieldtype":    "Int",
                "label":        "Slot No",
                "in_list_view": 1,
                "default":      "0",
                "columns":      1,
            },
            {
                "fieldname": "bio_index",
                "fieldtype": "Int",
                "label":     "Index",
                "default":   "0",
            },
            {
                "fieldname":    "valid",
                "fieldtype":    "Check",
                "label":        "Valid",
                "default":      "1",
                "in_list_view": 1,
                "columns":      1,
            },
            {
                "fieldname": "major_ver",
                "fieldtype": "Int",
                "label":     "Major Ver",
                "default":   "0",
            },
            {
                "fieldname": "minor_ver",
                "fieldtype": "Int",
                "label":     "Minor Ver",
                "default":   "0",
            },
            {
                "fieldname": "size",
                "fieldtype": "Int",
                "label":     "Size",
            },
            {
                "fieldname":    "source_device",
                "fieldtype":    "Data",
                "label":        "Source Device",
                "in_list_view": 1,
                "columns":      2,
            },
            {
                "fieldname":    "captured_at",
                "fieldtype":    "Datetime",
                "label":        "Captured At",
                "in_list_view": 1,
                "columns":      2,
            },
            {
                "fieldname": "template",
                "fieldtype": "Long Text",
                "label":     "Template (TMP)",
            },
        ],
        "permissions": [
            {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
            {"role": "HR Manager",     "read": 1, "write": 1},
            {"role": "HR User",        "read": 1},
        ],
    }).insert(ignore_permissions=True)

    frappe.clear_cache(doctype="Employee Biometric Template")
