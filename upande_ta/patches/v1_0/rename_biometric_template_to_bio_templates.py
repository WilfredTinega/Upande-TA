import frappe


def execute():
    """Drop legacy `Biometric Template` child doctype now that it has been
    renamed to `Bio Templates`. Safe because no production data exists yet."""
    if frappe.db.exists("DocType", "Biometric Template"):
        old = frappe.get_doc("DocType", "Biometric Template")
        if old.istable:
            frappe.db.delete("DocType", {"name": "Biometric Template"})
            frappe.db.delete("Custom Field", {"dt": "Biometric Template"})
            frappe.db.delete("Property Setter", {"doc_type": "Biometric Template"})
            frappe.db.commit()
            frappe.db.sql("DROP TABLE IF EXISTS `tabBiometric Template`")
