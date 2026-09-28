# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class OvertimeRequestBudget(Document):
	"""Hours approved for a Department and/or Unit/Division over the request's
	period, before anyone is named. Bulk Overtime allocates them to the people
	the attendance shows working overtime, and never past this total."""

	pass
