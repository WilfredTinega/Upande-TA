# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class HolidayAssignmentToolEmployee(Document):
	"""One employee selected into a Holiday Assignment Tool run.

	Carries the employee's `prior_holiday_list` (resolved at from_date - 1, which
	is what the restore boundary returns them to) and `assignments_json`, the list
	of Holiday List Assignment names this document created for them — that is what
	on_cancel reads back to unwind the run.
	"""

	pass
