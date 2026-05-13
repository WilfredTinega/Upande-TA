import frappe


MODULE = "Upande TA"


def after_install():
    frappe.db.commit()


def after_migrate():
    frappe.db.commit()
