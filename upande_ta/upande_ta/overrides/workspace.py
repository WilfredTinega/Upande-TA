import frappe
from frappe import _


PROTECTED = {"T&A"}


def _is_protected(doc):
	return doc.name in PROTECTED


def validate(doc, method=None):
	if not _is_protected(doc):
		return
	if doc.is_hidden:
		frappe.throw(_("The {0} workspace cannot be hidden.").format(doc.name))
	if not doc.public:
		frappe.throw(_("The {0} workspace must remain public.").format(doc.name))


def on_trash(doc, method=None):
	if _is_protected(doc):
		frappe.throw(_("The {0} workspace cannot be deleted.").format(doc.name))
