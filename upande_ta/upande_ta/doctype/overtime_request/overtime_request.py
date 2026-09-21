# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Overtime Request — the approval that comes before overtime is worked.

A supervisor lists who is to work overtime and for how long. The overtime runs
over a date range: a single day, an ISO week picked by its number (Week 38,
Week 40 — Monday to Sunday), a calendar month, or any two dates. **Requested
Hours are per day** — the range says which days are covered, not how the hours
are shared out over them. Bulk Overtime expands the range one
day at a time and pays each day against what the attendance shows was worked.

Approval runs through the **Overtime Request Approval** workflow: HR User
raises it, HR Manager approves or rejects, and each step is a Workflow Action
on the record. Approving is what submits the document, and docstatus 1 is what
Bulk Overtime pays from — which is why a rejection stays at docstatus 0 rather
than submitting. See ``upande_ta.patches.v1.create_overtime_workflows``.
"""

import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Coalesce
from frappe.utils import (
	add_days,
	cint,
	date_diff,
	flt,
	get_first_day,
	get_last_day,
	get_link_to_form,
	getdate,
	today,
)

#: The longest range a single request may cover. A month is the largest unit
#: the form offers, so anything past a year is a slip of the date picker — and
#: Bulk Overtime would expand it into one row per employee per day.
MAX_RANGE_DAYS = 366

SINGLE_DAY = "Single Day"
WEEK = "Week"
MONTH = "Month"
DATE_RANGE = "Date Range"


class OvertimeRequest(Document):
	def validate(self):
		self.set_date_range()
		self.validate_dates()
		self.validate_employees()
		self.set_totals()

	def before_submit(self):
		self.validate_not_already_requested()

	def on_submit(self):
		self.queue_bulk_overtime()

	def queue_bulk_overtime(self):
		"""An approved request should not also have to be fetched by hand.

		Built after this commit rather than inside it, so a batch that cannot
		be made — because the days have no attendance yet, which is the usual
		case, or because something else is already paying them — can never
		undo the approval that triggered it. Requests that cannot be paid now
		are picked up by the daily run once their attendance is in.
		"""
		frappe.enqueue(
			"upande_ta.upande_ta.doctype.bulk_overtime.bulk_overtime.auto_create_batch",
			queue="short",
			enqueue_after_commit=True,
			overtime_request=self.name,
		)

	# ──────────────────────────────────────────────────────────────────────
	# Dates
	# ──────────────────────────────────────────────────────────────────────

	def set_date_range(self):
		"""Settle ``overtime_date``/``to_date`` from whichever field the user
		actually filled, and write the other one back so the Week and Month
		pickers always show the range that is stored."""
		self.request_for = self.request_for or SINGLE_DAY

		if self.request_for == WEEK:
			if self.week:
				start = week_start(self.week_year or self.default_week_year(), self.week)
			elif self.overtime_date:
				start = add_days(getdate(self.overtime_date), -getdate(self.overtime_date).weekday())
			else:
				# nothing picked yet: overtime is nearly always asked for the
				# week it is being worked in, so that is where the field starts
				start = add_days(getdate(today()), -getdate(today()).weekday())
			self.overtime_date, self.to_date = start, add_days(start, 6)
			# written back off the resolved Monday, so week 1 of a year that
			# starts in December is stored under the year that numbers it
			self.week_year, week_no, _weekday = start.isocalendar()
			self.week = week_label(week_no)

		elif self.request_for == MONTH:
			if self.month:
				start = month_start(self.month)
			elif self.overtime_date:
				start = getdate(get_first_day(self.overtime_date))
			else:
				frappe.throw(_("Pick a Month."))
			self.overtime_date, self.to_date = start, getdate(get_last_day(start))
			self.month = start.strftime("%Y-%m")

		elif self.request_for == SINGLE_DAY:
			self.to_date = self.overtime_date

		elif not self.to_date:
			self.to_date = self.overtime_date

		if self.request_for != WEEK:
			self.week = self.week_year = None
		if self.request_for != MONTH:
			self.month = None

	def default_week_year(self) -> int:
		"""The year a week number is counted in when none was given: the one the
		start date already falls in, else the current one."""
		anchor = getdate(self.overtime_date) if self.overtime_date else getdate(today())
		return anchor.isocalendar()[0]

	def validate_dates(self):
		if not (self.overtime_date and self.to_date):
			return
		if getdate(self.to_date) < getdate(self.overtime_date):
			frappe.throw(_("To Date cannot be before From Date."))
		if self.number_of_days_in_range() > MAX_RANGE_DAYS:
			frappe.throw(
				_("A request cannot cover more than {0} days. Split it into shorter ranges.").format(
					MAX_RANGE_DAYS
				)
			)

	def number_of_days_in_range(self) -> int:
		if not (self.overtime_date and self.to_date):
			return 0
		return date_diff(self.to_date, self.overtime_date) + 1

	# ──────────────────────────────────────────────────────────────────────
	# Employees
	# ──────────────────────────────────────────────────────────────────────

	def validate_employees(self):
		if not self.employees:
			frappe.throw(_("Add at least one employee."))

		seen = set()
		for row in self.employees:
			if row.employee in seen:
				frappe.throw(
					_("Row #{0}: {1} is added more than once.").format(row.idx, frappe.bold(row.employee_name or row.employee))
				)
			seen.add(row.employee)

			if flt(row.requested_hours) <= 0:
				frappe.throw(
					_("Row #{0}: Requested Hours for {1} must be more than 0.").format(
						row.idx, frappe.bold(row.employee_name or row.employee)
					)
				)

		other_company = frappe.get_all(
			"Employee",
			filters={"name": ["in", list(seen)], "company": ["!=", self.company]},
			pluck="employee_name",
		)
		if other_company:
			frappe.throw(
				_("These employees are not in {0}: {1}").format(
					frappe.bold(self.company), ", ".join(frappe.bold(n) for n in other_company[:20])
				)
			)

	def validate_not_already_requested(self):
		"""One approved request per employee per day: Bulk Overtime pays a day
		once, so a second approval covering the same day would be ambiguous.
		Ranges clash when they overlap by even one day."""
		Request = frappe.qb.DocType("Overtime Request")
		Row = frappe.qb.DocType("Overtime Request Employee")
		# to_date is empty on requests saved before ranges existed
		other_end = Coalesce(Request.to_date, Request.overtime_date)
		clashes = (
			frappe.qb.from_(Row)
			.join(Request)
			.on(Request.name == Row.parent)
			.select(Row.employee, Row.employee_name, Request.name, Request.overtime_date, other_end.as_("end_date"))
			.where(
				(Request.docstatus == 1)
				& (Request.overtime_date <= self.to_date)
				& (other_end >= self.overtime_date)
				& (Request.name != self.name)
				& (Row.parenttype == "Overtime Request")
				& (Row.employee.isin([row.employee for row in self.employees]))
			)
			.run(as_dict=True)
		)
		if clashes:
			lines = "<br>".join(
				"{0} ({1} to {2}): {3}".format(
					frappe.bold(c.employee_name or c.employee),
					frappe.format(c.overtime_date, "Date"),
					frappe.format(c.end_date, "Date"),
					get_link_to_form("Overtime Request", c.name),
				)
				for c in clashes[:20]
			)
			frappe.throw(
				_("Already approved for overtime within {0} to {1}. Remove them or cancel the other request:<br>{2}").format(
					frappe.bold(frappe.format(self.overtime_date, "Date")),
					frappe.bold(frappe.format(self.to_date, "Date")),
					lines,
				),
				title=_("Already Requested"),
			)

	def set_totals(self):
		self.number_of_employees = len(self.employees)
		self.number_of_days = self.number_of_days_in_range()
		self.total_requested_hours = (
			sum(flt(row.requested_hours) for row in self.employees) * self.number_of_days
		)


# ──────────────────────────────────────────────────────────────────────────
# Weeks are picked by ISO number and year; months by the browser's own picker
# ──────────────────────────────────────────────────────────────────────────


def week_label(number: int) -> str:
	"""``38`` -> ``"Week 38"``, as the Week field lists it."""
	return f"Week {int(number):02d}"


def week_number(value) -> int:
	"""The number out of a Week field value. Accepts what the field stores
	("Week 38"), a bare number, and the "2026-W38" the browser's own week picker
	used to write here."""
	digits = "".join(character for character in str(value or "") if character.isdigit())
	# "2026-W38" carries its year in front of the week
	if str(value or "").upper().count("-W") and len(digits) > 2:
		digits = digits[4:]
	if not digits:
		frappe.throw(_("{0} is not a week. Pick one from the Week list.").format(frappe.bold(value)))
	return int(digits)


def week_start(year, value) -> datetime.date:
	"""Monday of an ISO week. ISO weeks belong to the year that numbers them,
	so week 1 can start in December and week 53 exists only in the years long
	enough to have one."""
	number = week_number(value)
	try:
		return datetime.date.fromisocalendar(int(year), number, 1)
	except (ValueError, TypeError):
		frappe.throw(
			_("{0} has no {1} — that year runs to week {2}. Pick another week or year.").format(
				frappe.bold(year), frappe.bold(week_label(number)), frappe.bold(weeks_in_year(year))
			)
		)


def weeks_in_year(year) -> int:
	"""52 for most years, 53 for the long ones."""
	return datetime.date(int(year), 12, 28).isocalendar()[1]


def month_start(value: str) -> datetime.date:
	"""First of a month written ``YYYY-MM``, the value an
	``<input type="month">`` produces."""
	try:
		return datetime.datetime.strptime(str(value), "%Y-%m").date()
	except ValueError:
		frappe.throw(_("{0} is not a month. Pick one with the Month field.").format(frappe.bold(value)))


@frappe.whitelist()
def bulk_overtime_status(overtime_request: str) -> dict:
	"""What the form needs to decide its Bulk Overtime button.

	``batch`` is the Bulk Overtime already paying this request, if any, and
	``approved`` says whether it has cleared its last approval — a request is
	only payable once it has.

	The batch cannot be looked up from the desk's generic list API:
	``frappe.desk.reportview`` has no notion of a parent doctype, so a child
	table is not readable through it. It is answered here, where the
	permission check belongs anyway.
	"""
	frappe.has_permission("Overtime Request", doc=overtime_request, throw=True)
	doc = frappe.get_doc("Overtime Request", overtime_request)
	return {"approved": is_finally_approved(doc), "batch": paying_batch(overtime_request)}


def is_finally_approved(doc) -> bool:
	"""Whether this request has cleared the last stage of its approval.

	docstatus 1 is the approval — nothing else reaches it, and a rejection
	deliberately stays at 0 — but where a workflow is in force its own states
	are read, so a chain with more stages than the one shipped still answers
	this correctly, whatever those stages are called.
	"""
	if doc.docstatus != 1:
		return False

	state = doc.get("workflow_state")
	if not state:
		return True

	workflow = frappe.db.get_value("Workflow", {"document_type": doc.doctype, "is_active": 1}, "name")
	if not workflow:
		return True

	doc_status = frappe.db.get_value(
		"Workflow Document State", {"parent": workflow, "state": state}, "doc_status"
	)
	return cint(doc_status) == 1


def paying_batch(overtime_request: str) -> str | None:
	"""The Bulk Overtime already paying this request, if there is one. A
	cancelled batch does not count: it has released whatever it held."""
	if not frappe.db.exists("DocType", "Bulk Overtime"):
		return None

	rows = frappe.db.sql(
		"""
		select entry.parent
		from `tabBulk Overtime Entry` entry
		join `tabBulk Overtime` bo on bo.name = entry.parent
		where entry.parenttype = 'Bulk Overtime'
			and bo.docstatus < 2
			and entry.overtime_request = %s
		order by bo.creation desc
		limit 1
		""",
		overtime_request,
	)
	return rows[0][0] if rows else None
