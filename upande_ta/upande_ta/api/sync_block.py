import json
import os
import frappe


def push_ta_dashboard():
	app_path = frappe.get_app_path("upande_ta")
	fix_path = os.path.join(
		app_path, "upande_ta", "custom_html_block", "ta_dashboard", "ta_dashboard.json"
	)
	with open(fix_path) as f:
		fix = json.load(f)
	doc = frappe.get_doc("Custom HTML Block", "T&A Dashboard")
	doc.html = fix["html"]
	doc.script = fix["script"]
	doc.style = fix.get("style", "")
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	print(
		"OK pushed. html=", len(doc.html or ""),
		"script=", len(doc.script or ""),
		"style=", len(doc.style or ""),
	)
