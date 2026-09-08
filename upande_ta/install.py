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
    """Sync the shipped "Upande TA Dashboard" Custom HTML Block into the site.

    Custom HTML Block is not a module-scoped doctype, so Frappe does not
    auto-import the record from the app folder on install/migrate. The Upande
    TA workspace embeds this block by name, so without it the workspace renders
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


# The launcher tile HRMS ships for itself. Nesting under it is what puts the
# app inside the HR folder on the apps screen instead of loose at the top
# level, beside ERPNext and Framework. Every HRMS area (Leaves, Payroll,
# Shift & Attendance) is parented to it the same way, so this lands among
# its peers.
HR_PARENT_ICON = "Frappe HR"

# The one Workspace this app owns. The launcher tile, the Desk route and the
# protected-workspace guard in overrides/workspace.py all key off this single
# name, so there is nowhere for a second "T&A" identity to reappear from.
WORKSPACE = "Upande TA"


def ensure_desktop_icon():
    """Create / refresh the launcher Desktop Icon for the Upande TA workspace.

    Nested under Frappe HR, as a child tile (`icon_type = "Link"`) rather than a
    top-level app tile.

    The apps-screen launcher is not the same doctype on every Frappe this app
    supports, so nothing about its schema is assumed:

      * on a desk with no Desktop Icon doctype at all -- v15 and earlier, where
        the nav is the Workspace on its own -- this is a no-op rather than a
        migrate-aborting error on a missing table;
      * the payload is filtered to the fields the site's own doctype declares,
        so a version without `parent_icon` still gets a working flat icon
        instead of failing to save;
      * `parent_icon` is only set once the HRMS tile is actually there. It is a
        Link field, so pointing it at a missing record would throw. On a site
        where HRMS has not landed yet the icon is created flat, and the next
        migrate nests it.

    The link stays external (`/app/upande-ta`) rather than a Workspace Sidebar
    reference: this app ships no Workspace Sidebar record, and a Dynamic Link to
    one that does not exist would fail validation.
    """
    if not frappe.db.exists("DocType", "Desktop Icon"):
        return

    name = WORKSPACE
    payload = {
        "doctype": "Desktop Icon",
        "name": name,
        "label": name,
        "app": "upande_ta",
        "icon_type": "Link",
        "link_type": "External",
        "link": f"/app/{WORKSPACE.lower().replace(' ', '-')}",
        "logo_url": "/assets/upande_ta/images/upande_logo.ico",
        "hidden": 0,
        "standard": 1,
    }

    meta = frappe.get_meta("Desktop Icon")
    if meta.has_field("parent_icon") and frappe.db.exists("Desktop Icon", HR_PARENT_ICON):
        payload["parent_icon"] = HR_PARENT_ICON

    payload = {
        k: v for k, v in payload.items() if k in ("doctype", "name") or meta.has_field(k)
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
