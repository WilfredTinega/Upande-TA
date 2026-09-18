# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class HolidayAssignmentToolException(Document):
	"""A single date inside the run's range that uses a different Holiday List.

	This is the carve-out the tool exists for: "Sunday off for September, but
	Wednesday on the 16th". plan_segments() turns each of these into two
	boundaries — the exception itself, and a return to the base list the next day.
	"""

	pass
