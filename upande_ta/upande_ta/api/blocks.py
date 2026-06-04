import os

import frappe


@frappe.whitelist()
def render_ta_dashboard():
	html = frappe.render_template("templates/blocks/ta_dashboard.html", {})
	css_path = os.path.join(
		frappe.get_app_path("upande_ta"), "public", "css", "ta_dashboard.bundle.css"
	)
	try:
		with open(css_path) as f:
			css = f.read()
	except FileNotFoundError:
		css = ""
	return {"html": html, "css": css}
