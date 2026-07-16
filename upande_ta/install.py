import json
import os

import frappe


def after_install():
    """Run after the app is installed on a site."""
    ensure_desktop_icon()
    ensure_ta_dashboard_block()


def after_migrate():
    """Run after every `bench migrate` for a site that has this app installed."""
    ensure_desktop_icon()
    ensure_ta_dashboard_block()


def ensure_ta_dashboard_block():
    """Sync the shipped "T&A Dashboard" Custom HTML Block into the site.

    Custom HTML Block is not a module-scoped doctype, so Frappe does not
    auto-import the record from the app folder on install/migrate. The T&A
    workspace embeds this block by name, so without it the workspace renders
    "undefined". This upserts the record from the shipped JSON (idempotent)."""
    if not frappe.db.exists("DocType", "Custom HTML Block"):
        return

    path = frappe.get_app_path(
        "upande_ta", "upande_ta", "custom_html_block", "ta_dashboard", "ta_dashboard.json"
    )
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    name = data.get("name")
    if not name:
        return

    fields = {k: data.get(k, "") for k in ("html", "script", "style", "private")}

    if frappe.db.exists("Custom HTML Block", name):
        doc = frappe.get_doc("Custom HTML Block", name)
        for k, v in fields.items():
            doc.set(k, v)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "Custom HTML Block",
            "name": name,
            **fields,
        }).insert(ignore_permissions=True, ignore_if_duplicate=True)

    frappe.db.commit()


def ensure_desktop_icon():
    """Create / refresh the launcher Desktop Icon for the T&A workspace."""
    name = "T&A"
    payload = {
        "doctype": "Desktop Icon",
        "name": name,
        "label": name,
        "app": "upande_ta",
        "icon_type": "App",
        "link_type": "External",
        "link": "/app/t%26a",
        "logo_url": "/assets/upande_ta/images/upande_logo.ico",
        "force_show": 1,
        "hidden": 0,
        "standard": 1,
    }

    if frappe.db.exists("Desktop Icon", name):
        doc = frappe.get_doc("Desktop Icon", name)
        for k, v in payload.items():
            if k in ("doctype", "name"):
                continue
            doc.set(k, v)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc(payload).insert(ignore_permissions=True, ignore_if_duplicate=True)

    frappe.clear_cache()
