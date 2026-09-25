# Copyright (c) 2026, Upande Limited and contributors
# For license information, please see license.txt
"""Controller for the Canteen Analysis portal page (/canteen-analysis).

Tells the kitchen how many employees to cook lunch for on a date. The numbers
come from ``upande_ta.upande_ta.api.canteen_analysis``; this file only gates
the page to signed-in users, the same rule as /attendance-insights.
"""

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(
			frappe._("You need to be signed in to view the canteen analysis."),
			frappe.PermissionError,
		)

	context.no_cache = 1
	context.show_sidebar = False
	context.full_width = 1
	context.title = frappe._("Canteen Analysis")
	return context
