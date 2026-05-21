import frappe


def after_install():
    """Run after the app is installed on a site."""
    ensure_desktop_icon()


def after_migrate():
    """Run after every `bench migrate` for a site that has this app installed."""
    ensure_desktop_icon()


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
