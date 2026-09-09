# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt
"""Small shims for the differences between the Frappe versions we run on.

Keep this module tiny and dependency-free: it is imported from code that runs
during migrate and from scheduled jobs.
"""

import frappe


def in_test() -> bool:
	"""True while a test run is in progress.

	``frappe.in_test`` only exists from v16; v15 carries the same signal on
	``frappe.flags.in_test``. Reading the v16 attribute directly raises
	``AttributeError`` on v15, which is how a live absent-marking run died.
	"""
	flag = getattr(frappe, "in_test", None)
	if flag is None:
		flag = frappe.flags.get("in_test")
	return bool(flag)
