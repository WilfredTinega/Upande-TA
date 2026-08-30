# Copyright (c) 2026, Upande Limited and contributors
# For license information, please see license.txt
"""Controller for the Attendance Insights portal page (/attendance-insights).

The dashboard used to live as a Web Page record edited in the desk, which meant
its markup, styles and 158k of script were site data: not in version control,
not reviewable, and different on every site it had been copied to. It is a file
in the app now, so a change ships with a deploy like any other code.

A Web Page of the same route still wins over this file if one exists on the
site — frappe resolves database routes first — so a site being moved across
should have its old record removed once this is deployed.

The route was /attendance-dashboard until the page was renamed; hooks.py
redirects the old path so existing links and bookmarks still land here.
"""

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(
			frappe._("You need to be signed in to view the attendance dashboard."),
			frappe.PermissionError,
		)

	context.no_cache = 1
	context.show_sidebar = False
	# The register tables are wide (a row per employee, per day); the default
	# portal container crops them.
	context.full_width = 1
	context.title = frappe._("Attendance Insights")
	return context
