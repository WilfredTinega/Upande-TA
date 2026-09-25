# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""What each meal cost on one date. Every day is a different dish, so a price
applies to that date only: the Canteen Analysis prices a meal from the record
for its exact date (the employee's company first, else the blank-company
record) and leaves it unpriced when there is none."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import formatdate


def duplicate_meal_types(rows):
	"""Meal types that appear on more than one row."""
	seen, dupes = set(), []
	for row in rows:
		meal = row.get("meal_type")
		if meal in seen and meal not in dupes:
			dupes.append(meal)
		seen.add(meal)
	return dupes


def title_for(date, rows):
	"""e.g. "26-09-2026 · Lunch: Rice & Beans" (the lunch row, else the first)."""
	row = next((r for r in rows if r.get("meal_type") == "Lunch"), None) or (rows[0] if rows else None)
	title = formatdate(date)
	if row:
		title += " · " + (row.get("meal_type") or "")
		if row.get("menu"):
			title += ": " + row.get("menu")
	return title


class DailyMealCost(Document):
	def validate(self):
		dupes = duplicate_meal_types(self.meals)
		if dupes:
			frappe.throw(_("Only one row per meal: {0} is entered more than once.").format(", ".join(dupes)))
		existing = frappe.db.get_value(
			"Daily Meal Cost",
			{
				"date": self.date,
				"company": self.company or ("is", "not set"),
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("{0} already holds the meal costs for {1}{2}.").format(
					frappe.get_desk_link("Daily Meal Cost", existing),
					formatdate(self.date),
					" · " + self.company if self.company else "",
				),
				frappe.DuplicateEntryError,
			)
		self.title = title_for(self.date, self.meals)
