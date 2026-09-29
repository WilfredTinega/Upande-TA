# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Bulk Overtime — pays approved Overtime Requests against attendance.

The chain is::

    Overtime Request (approved)  ->  Bulk Overtime  ->  Overtime Slip  ->  Additional Salary

**Get Overtime** lists the approved requests with hours still to pay in the
period and takes the ones HR picks — "Select All" for the lot. Each is expanded
into one row per employee per date (a request may cover a day, a week or a
month), then checked against that day's attendance. On a day, month or date
range request the hours are per day, and each day pays the lower of what was
requested and what the biometric shows was worked. On a **Week** request the
hours are the week's total: the days are paid in date order against that total,
so a short day is made up by a long one (see
``upande_ta.upande_ta.overtime_engine``).
Hours are never typed in: they come from the attendance, and all HR decides is
who is paid — a row that should not be paid is removed from the table.

A request made as an **Hours Budget** names nobody. **Get Overtime** lists
the employees of the budgeted Department and/or Unit/Division whose
attendance shows overtime in the period, and HR ticks the ones to pay; each
row asks for what was worked, and days with no overtime are not listed. The
batch cannot be submitted while it would take a budget past its hours or its
Max Employees — HR removes people until it fits. See
:meth:`BulkOvertime.check_budgets`. Because someone has to choose, a budget
request is never turned into a batch automatically.

A request with nothing left to add — every day of it already in another batch —
is not offered, since a day is paid once. The Overtime Request form can also
start a batch over its own period. Either way the period governs: a request is
always clipped to this document's dates.

Approval runs through the **Bulk Overtime Approval** workflow, and approving is
what submits the batch — so a rejection stays at docstatus 0 and cuts no slips.

On submit this document creates one Overtime Slip per employee, one line per
date, and nothing else. **It computes no money.** The Overtime Slip prices
itself when it is submitted — upande_payroll's rule where that app is
installed, HRMS's own otherwise — and creates the Additional Salary that
payroll picks up.

Nothing here is a policy constant. The **Overtime Type** named on the approved
request carries the rate — its standard, weekend and public-holiday multipliers
and its Maximum Overtime Hours Allowed. The shift length that overtime starts
after is the Shift Type's own, falling back to Standard Working Hours in HR
Settings when an attendance has no shift.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, flt, get_link_to_form, getdate, today

from upande_ta.upande_ta import overtime_engine as engine
from upande_ta.upande_ta.doctype.overtime_request.overtime_request import HOURS_BUDGET, WEEK, budget_scope

#: Isolates one employee's Overtime Slip from the next, so every failure can be
#: collected and reported together before the submit is rolled back.
SAVEPOINT = "bulk_overtime_slip"

#: Names listed individually in a message before it collapses to a count.
MAX_NAMES_IN_MESSAGE = 20

#: Statuses :func:`engine.settle` pays nothing for, whatever the week allows.
UNPAYABLE = (engine.NO_ATTENDANCE, engine.NO_CLOCK_OUT, engine.NO_SHIFT)

#: How far back a partly paid request is still looked at for days left to pay:
#: a week paid up to the day it was approved leaves its last days behind, and
#: attendance for them can arrive a little late. Past this, it is done.
PICK_UP_WINDOW_DAYS = 31


class BulkOvertime(Document):
	# ──────────────────────────────────────────────────────────────────────
	# Validation
	# ──────────────────────────────────────────────────────────────────────

	def validate(self):
		self.validate_dates()
		self.refresh_entries()
		self.set_totals()
		self.set_title()
		self.set_week()
		self.check_budgets(throw=False)
		self.check_requested_hours(throw=False)

	def before_submit(self):
		if not any(flt(row.approved_hours) > 0 for row in self.bulk_overtime_entries):
			self.throw_nothing_to_pay()
		self.validate_budget_period_over()
		self.check_budgets(throw=True)
		self.check_requested_hours(throw=True)
		for row in self.bulk_overtime_entries:
			if flt(row.approved_hours) > 0 and not row.overtime_type:
				frappe.throw(
					_("Row #{0}: the Overtime Request it came from has no Overtime Type.").format(row.idx)
				)

	def throw_nothing_to_pay(self):
		"""Refuse a batch that pays nothing, naming each row and why it is 0."""
		lines = []
		for row in self.bulk_overtime_entries[:MAX_NAMES_IN_MESSAGE]:
			who = "{0}, {1}".format(
				row.employee_name or row.employee, frappe.format(row.overtime_date, "Date")
			)
			worked, shift = flt(row.working_hours) - flt(row.early_in_hours), flt(row.shift_hours)
			if row.status in (engine.NO_ATTENDANCE, engine.NO_CLOCK_OUT, engine.NO_SHIFT):
				why = _(row.status)
			elif row.day_type == engine.WORKING_DAY and worked <= shift:
				why = _("{0}h worked on a {1}h shift is no overtime").format(
					frappe.format(worked, "Float"), frappe.format(shift, "Float")
				)
			else:
				why = _(row.status or "0")
			lines.append("{0}: {1}".format(frappe.bold(who), why))
		if len(self.bulk_overtime_entries) > len(lines):
			lines.append(_("... and {0} more").format(len(self.bulk_overtime_entries) - len(lines)))
		frappe.throw(
			_("There are no approved hours to pay:<br>{0}").format("<br>".join(lines)),
			title=_("Nothing to Pay"),
		)

	def validate_dates(self, chosen=None):
		if not (self.from_date and self.to_date):
			return
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date."))
		# a budget's batch runs to the end of the budget, and waits for it at submit
		if getdate(self.to_date) > getdate(today()) and not self.pays_a_budget(chosen):
			frappe.throw(_("To Date cannot be in the future: overtime is paid once it has been worked."))

	def pays_a_budget(self, chosen=None) -> bool:
		requests = set(chosen or []) | {
			row.overtime_request for row in self.bulk_overtime_entries if row.overtime_request
		}
		if not requests:
			return False
		return bool(
			frappe.db.exists(
				"Overtime Request", {"name": ["in", list(requests)], "request_mode": HOURS_BUDGET}
			)
		)

	def validate_budget_period_over(self):
		if self.to_date and getdate(self.to_date) > getdate(today()) and self.pays_a_budget():
			frappe.throw(
				_("The budget runs to {0}. Submit this batch once the period is over.").format(
					frappe.bold(frappe.format(self.to_date, "Date"))
				),
				title=_("Not Yet"),
			)

	def set_totals(self):
		rows = self.bulk_overtime_entries
		self.number_of_employees = len({row.employee for row in rows})
		self.total_requested_hours = sum(flt(row.requested_hours) for row in rows)
		self.total_biometric_hours = sum(flt(row.biometric_hours) for row in rows)
		self.total_approved_hours = sum(flt(row.approved_hours) for row in rows)

	def set_title(self):
		if self.from_date and self.to_date:
			self.bulk_overtime_title = "{0}: {1} to {2}".format(
				self.custom_farm or self.company,
				frappe.format(self.from_date, "Date"),
				frappe.format(self.to_date, "Date"),
			)

	def set_week(self):
		"""The ISO week(s) the period falls in — "Week 36, 2026", or
		"Week 35 – 36, 2026" across two — as requests are picked by week."""
		self.week = None
		if not (self.from_date and self.to_date):
			return
		start_year, start_week, _day = getdate(self.from_date).isocalendar()
		end_year, end_week, _day = getdate(self.to_date).isocalendar()
		if (start_year, start_week) == (end_year, end_week):
			self.week = f"Week {start_week:02d}, {start_year}"
		elif start_year == end_year:
			self.week = f"Week {start_week:02d} – {end_week:02d}, {start_year}"
		else:
			self.week = f"Week {start_week:02d}, {start_year} – Week {end_week:02d}, {end_year}"

	# ──────────────────────────────────────────────────────────────────────
	# Get Overtime
	# ──────────────────────────────────────────────────────────────────────

	@frappe.whitelist()
	def get_overtime(self, overtime_requests=None, budget_employees=None):
		"""Rebuild the table from the approved requests in the period.

		``budget_employees`` is who HR ticked from :meth:`get_budget_candidates`:
		an Hours Budget names nobody, so its rows are only the people chosen.
		Left out, everyone the budget covers who worked overtime is taken —
		which is what the checks behind the request form's buttons count.

		``overtime_requests`` is what **Get Overtime** sends when HR picks the
		requests from the list, and the batch's own period is taken from them:
		it spans the requests picked and stops at today. Rows are still clipped
		to that period, so the batch can never pay outside its own dates.
		"""
		chosen = _request_names(overtime_requests)

		if not self.company:
			frappe.throw(_("Set the Company first."))
		self.set_period_from_requests(chosen)
		if not (self.from_date and self.to_date):
			frappe.throw(_("Pick the Overtime Requests to pay — the dates come from them."))
		self.validate_dates(chosen)

		found = frappe._dict(requests=0, days_without_attendance=0, days_on_leave=0)
		requests = self._approved_request_rows(found, chosen=chosen)
		if budget_employees is not None:
			picked = set(_request_names(budget_employees))
			requests = [r for r in requests if not r.get("budget") or r.employee in picked]
		claimed = self._claimed_elsewhere(requests)
		blocked = self._employees_with_overlapping_slips({r.employee for r in requests})

		self.set("bulk_overtime_entries", [])
		left_out = []
		for request in requests:
			key = (request.employee, str(request.overtime_date))
			if key in claimed:
				left_out.append(
					_("{0} on {1}: already in {2}").format(
						request.employee_name, frappe.format(request.overtime_date, "Date"), claimed[key]
					)
				)
				continue
			if request.employee in blocked:
				left_out.append(
					_("{0}: already has Overtime Slip {1} in this period").format(
						request.employee_name, blocked[request.employee]
					)
				)
				continue

			self.append(
				"bulk_overtime_entries",
				{
					"employee": request.employee,
					"employee_name": request.employee_name,
					"overtime_date": request.overtime_date,
					"requested_hours": request.requested_hours,
					"overtime_type": request.overtime_type,
					"overtime_request": request.request,
					"overtime_budget": request.get("budget"),
					"budget_scope": request.get("budget_scope"),
				},
			)

		self.refresh_entries()
		self.drop_budget_rows_without_overtime()
		self.adopt_request_unit(chosen)
		self.set_totals()
		self.set_title()
		self.set_week()

		result = {
			"rows": len(self.bulk_overtime_entries),
			"left_out": sorted(set(left_out)),
			"approved_requests": found.requests,
			"days_without_attendance": found.days_without_attendance,
			"days_on_leave": found.days_on_leave,
			"picked": len(chosen),
		}
		if not result["rows"] and found.requests:
			result["why"] = self._why_nothing_to_pay(chosen)
		return result

	@frappe.whitelist()
	def get_budget_candidates(self, overtime_requests=None):
		"""Who HR can choose from for the Hours Budget requests picked: the
		employees each budget covers whose attendance shows overtime in the
		period, one line per person with their days and hours, beside what is
		left of the budget. Nothing here is saved."""
		chosen = _request_names(overtime_requests)
		if not self.company:
			frappe.throw(_("Set the Company first."))
		self.set_period_from_requests(chosen)
		if not (self.from_date and self.to_date):
			return {"employees": [], "budgets": []}

		rows = self._budget_request_rows(chosen=chosen)
		claimed = self._claimed_elsewhere(rows)
		blocked = self._employees_with_overlapping_slips({row.employee for row in rows})
		rows = [
			row
			for row in rows
			if (row.employee, str(row.overtime_date)) not in claimed and row.employee not in blocked
		]
		# measured on a copy: this document's own table is the form's, and the
		# desk syncs it back from whatever a whitelisted method leaves in it
		probe = frappe.get_doc(
			{
				"doctype": "Bulk Overtime",
				"company": self.company,
				"custom_farm": self.custom_farm,
				"from_date": self.from_date,
				"to_date": self.to_date,
			}
		)
		entries = _budget_overtime_entries(probe, rows)

		people = {}
		for entry in entries:
			person = people.setdefault(
				entry.employee,
				frappe._dict(
					employee=entry.employee,
					employee_name=entry.employee_name,
					budget_scope=entry.budget_scope,
					days=0,
					hours=0.0,
				),
			)
			person.days += 1
			person.hours = round(person.hours + flt(entry.approved_hours), 2)

		records = {
			e.name: e
			for e in frappe.get_all(
				"Employee",
				filters={"name": ["in", list(people) or [""]]},
				fields=["name", "department"]
				+ (["custom_farm"] if "custom_farm" in frappe.db.get_table_columns("Employee") else []),
			)
		}
		for person in people.values():
			record = records.get(person.employee) or {}
			person.department = record.get("department")
			person.custom_farm = record.get("custom_farm")

		lines = {row.budget for row in rows}
		budgets = frappe.get_all(
			"Overtime Request Budget",
			filters={"name": ["in", list(lines) or [""]]},
			fields=[
				"name",
				"parent",
				"custom_farm",
				"department",
				"budget_hours",
				"hours_left",
				"max_employees",
			],
		)
		for line in budgets:
			line.scope = budget_scope(line)
		return {
			"employees": sorted(people.values(), key=lambda p: (p.budget_scope or "", p.employee_name or "")),
			"budgets": budgets,
		}

	def _request_scope(self):
		"""The company/unit the batch pays for, as a join and a filter both the
		picker and the fetch run through — so what the picker offers is exactly
		what the fetch would take."""
		farm_join = farm_filter = ""
		values = {"company": self.company, "from_date": self.from_date, "to_date": self.to_date}
		if self.custom_farm and "custom_farm" in frappe.db.get_table_columns("Employee"):
			farm_join = "join `tabEmployee` emp on emp.name = row.employee"
			farm_filter = "and emp.custom_farm = %(custom_farm)s"
			values["custom_farm"] = self.custom_farm
		return farm_join, farm_filter, values

	@frappe.whitelist()
	def get_approved_requests(self):
		"""The approved requests this batch can pay from, for the picker.

		Deliberately *not* bounded by this document's dates: the dates come
		from the requests that are picked, not the other way round, so there is
		nothing to type in twice.

		A request is offered while it has days worked but in no batch yet — a
		day is paid once, so a request already in a batch is offered again
		only for what that batch left behind, such as the last days of a week
		paid before the week was out. One that has not started yet is not
		offered either, since overtime is paid once it has been worked.
		"""
		if not self.company:
			frappe.throw(_("Set the Company first."))

		farm_join, farm_filter, _scope = self._request_scope()
		values = {
			"company": self.company,
			"today": today(),
			"window_start": add_days(today(), -PICK_UP_WINDOW_DAYS),
		}
		if "custom_farm" in _scope:
			values["custom_farm"] = _scope["custom_farm"]

		requests = frappe.db.sql(
			f"""
			select req.name, req.request_for, req.week, req.week_year, req.overtime_date,
				coalesce(req.to_date, req.overtime_date) as to_date,
				req.overtime_type, req.reason, req.default_requested_hours,
				count(distinct row.employee) as employees,
				sum(row.requested_hours) as hours_per_day,
				exists (
					select 1
					from `tabBulk Overtime Entry` entry
					join `tabBulk Overtime` bo on bo.name = entry.parent
					where entry.parenttype = 'Bulk Overtime'
						and bo.docstatus < 2
						and entry.overtime_request = req.name
				) as in_a_batch
			from `tabOvertime Request` req
			join `tabOvertime Request Employee` row
				on row.parent = req.name and row.parenttype = 'Overtime Request'
			{farm_join}
			where req.docstatus = 1
				and req.company = %(company)s
				and req.overtime_date <= %(today)s
				{farm_filter}
			group by req.name
			having not in_a_batch or max(coalesce(req.to_date, req.overtime_date)) >= %(window_start)s
			order by req.overtime_date desc, req.name
			""",
			values,
			as_dict=True,
		)

		requests += self._approved_budget_requests(farm_join, farm_filter, values)
		requests.sort(key=lambda r: (getdate(r.overtime_date), r.name), reverse=True)

		cutoff = getdate(today())
		offered = []
		for request in requests:
			request.departments = [d for d in (request.departments or "").split("||") if d]
			request.units = [u for u in (request.get("units") or "").split("||") if u]
			# what a batch would actually cover: overtime is paid once worked
			request.days = date_diff(request.to_date, request.overtime_date) + 1
			request.payable_to = min(getdate(request.to_date), cutoff)
			request.payable_from = getdate(request.overtime_date)
			request.payable_days = date_diff(request.payable_to, request.overtime_date) + 1

			if request.in_a_batch:
				left = sorted(
					{getdate(row.overtime_date) for row in outstanding_days(request.name, self.name)}
				)
				if not left:
					continue
				request.payable_from, request.payable_days = left[0], len(left)
			offered.append(request)
		return offered

	def _approved_budget_requests(self, farm_join, farm_filter, values) -> list:
		"""The Hours Budget requests for the picker, shaped like the named ones.
		A budget with no hours left is not offered: nothing more can be paid
		from it."""
		if not frappe.db.exists("DocType", "Overtime Request Budget"):
			return []
		line_farm = "and ifnull(line.custom_farm, '') in ('', %(custom_farm)s)" if farm_filter else ""
		return frappe.db.sql(
			f"""
			select req.name, req.request_for, req.week, req.week_year, req.request_mode, req.overtime_date,
				coalesce(req.to_date, req.overtime_date) as to_date,
				req.overtime_type, req.reason, req.default_requested_hours,
				0 as employees,
				sum(line.budget_hours) as hours_per_day,
				group_concat(distinct line.department order by line.department separator '||') as departments,
				group_concat(distinct line.custom_farm order by line.custom_farm separator '||') as units,
				sum(line.budget_hours - ifnull(line.hours_used, 0)) as budget_left,
				exists (
					select 1
					from `tabBulk Overtime Entry` entry
					join `tabBulk Overtime` bo on bo.name = entry.parent
					where entry.parenttype = 'Bulk Overtime'
						and bo.docstatus < 2
						and entry.overtime_request = req.name
				) as in_a_batch
			from `tabOvertime Request` req
			join `tabOvertime Request Budget` line
				on line.parent = req.name and line.parenttype = 'Overtime Request'
			where req.docstatus = 1
				and req.company = %(company)s
				and req.overtime_date <= %(today)s
				{line_farm}
			group by req.name
			having budget_left > 0
				and (not in_a_batch or max(coalesce(req.to_date, req.overtime_date)) >= %(window_start)s)
			""",
			values,
			as_dict=True,
		)

	def set_period_from_requests(self, chosen):
		"""Take the batch's own dates from the requests being paid.

		The period is not something to type in beside the requests that
		already carry it: it spans them, and stops at today, because overtime
		is paid against attendance that has already been recorded.

		An Hours Budget is the exception: the batch runs over the budget's own
		period, whole. It is chosen from and checked against as one, and
		since HRMS allows one Overtime Slip per employee per period, paying
		part of it would leave the rest unpayable — so such a batch can be
		saved early but not submitted until the period is over.
		"""
		if not chosen:
			return

		spans = frappe.get_all(
			"Overtime Request",
			filters={"name": ["in", list(chosen)]},
			fields=["name", "overtime_date", "to_date", "request_mode"],
		)
		budgeted = [span for span in spans if span.request_mode == HOURS_BUDGET and span.overtime_date]
		# a request another batch has already paid part of starts from its
		# first day left to pay, so this batch's slips cannot overlap that
		# batch's — HRMS allows one Overtime Slip per employee per period
		partly_paid = _requests_in_other_batches([span.name for span in spans], self.name)
		starts, ends = [], []
		for span in spans:
			if not span.overtime_date:
				continue
			start = getdate(span.overtime_date)
			if span.name in partly_paid:
				left = [getdate(row.overtime_date) for row in outstanding_days(span.name, self.name)]
				if not left:
					continue
				start = min(left)
			starts.append(start)
			ends.append(getdate(span.to_date or span.overtime_date))
		if not starts:
			if partly_paid:
				frappe.throw(
					_("Every day of {0} worked so far is already in a Bulk Overtime.").format(
						frappe.bold(", ".join(sorted(partly_paid)))
					),
					title=_("Already Paid"),
				)
			return

		start, end = min(starts), min(max(ends), getdate(today()))
		if budgeted:
			start = min([start] + [getdate(span.overtime_date) for span in budgeted])
			end = max([end] + [getdate(span.to_date or span.overtime_date) for span in budgeted])
		if start > end:
			frappe.throw(
				_(
					"{0} covers a period that has not been worked yet. Overtime is paid against the attendance, so there is nothing to pay until then."
				).format(frappe.bold(", ".join(span.name for span in spans))),
				title=_("Not Yet"),
			)
		self.from_date, self.to_date = start, end

	def adopt_request_unit(self, chosen):
		"""Take the Unit/Division from the requests this batch was fetched from.

		Picking the requests by hand says which unit the batch is for, so the
		field should not have to be filled twice — and the title reads off it.
		Only when this batch has none of its own and the requests agree on one:
		a batch spanning two units belongs to neither. Run after the rows are
		built, because Unit/Division also filters the sweep, and adopting it
		must not quietly drop rows from the very fetch that set it.
		"""
		if self.custom_farm or not chosen:
			return
		if "custom_farm" not in frappe.db.get_table_columns("Overtime Request"):
			return

		units = {
			unit
			for unit in frappe.get_all(
				"Overtime Request", filters={"name": ["in", list(chosen)]}, pluck="custom_farm"
			)
			if unit
		}
		if len(units) == 1:
			self.custom_farm = units.pop()

	def _approved_request_rows(self, found=None, chosen=None):
		"""One row per employee per date, from every approved request that
		overlaps this period.

		A request covers a range — a day, a week, a month — expanded one date
		at a time and clipped to this document's own period. Each date asks
		for the request's hours per day, except on a Week request with Hours by
		Date: there each date asks for its own share of the weekly total, and
		a date whose share is 0 (a rest day) is not asked for at all. A range
		longer than a day is a
		standing permission rather than a promise about each day, so its dates
		with no attendance at all are dropped; a single-day request keeps its
		row either way, because that day was asked for by name and a missing
		clock-in is what HR needs to see.
		"""
		farm_join, farm_filter, values = self._request_scope()
		chosen_filter = ""
		if chosen:
			chosen_filter = "and req.name in %(chosen)s"
			values["chosen"] = tuple(chosen)

		requests = frappe.db.sql(
			f"""
			select req.name as request, req.overtime_date,
				coalesce(req.to_date, req.overtime_date) as request_to_date,
				req.overtime_type, row.employee, row.employee_name, row.requested_hours
			from `tabOvertime Request` req
			join `tabOvertime Request Employee` row
				on row.parent = req.name and row.parenttype = 'Overtime Request'
			{farm_join}
			where req.docstatus = 1
				and req.company = %(company)s
				and req.overtime_date <= %(to_date)s
				and coalesce(req.to_date, req.overtime_date) >= %(from_date)s
				{farm_filter}
				{chosen_filter}
			order by row.employee_name, req.overtime_date
			""",
			values,
			as_dict=True,
		)

		if found is not None:
			found.requests = len({row.request for row in requests})

		rows = self._named_request_rows(requests, found)
		named = {(row.employee, getdate(row.overtime_date)) for row in rows}
		# a name on a request is a firmer instruction than a budget, so a day
		# someone is named for is never also taken from a pool
		rows += [
			row
			for row in self._budget_request_rows(found, chosen)
			if (row.employee, getdate(row.overtime_date)) not in named
		]
		rows.sort(key=lambda r: (r.employee_name or "", r.overtime_date))
		return rows

	def _named_request_rows(self, requests, found=None) -> list:
		if not requests:
			return []

		by_date = _hours_by_date({row.request for row in requests})

		# the same lookup refresh_entries uses, so a date is dropped only when
		# it would have shown up as "No Attendance" anyway
		employees = {r.employee for r in requests}
		attended = set(self._attendance_by_day(employees))
		on_leave = self._leave_days(employees) - attended
		swaps = self.swapped_rest_days(employees) if by_date else {}
		period_start, period_end = getdate(self.from_date), getdate(self.to_date)

		rows = []
		for request in requests:
			start = max(getdate(request.overtime_date), period_start)
			end = min(getdate(request.request_to_date), period_end)
			is_range = getdate(request.request_to_date) > getdate(request.overtime_date)

			shares = by_date.get(request.request)

			date = start
			while date <= end:
				hours = request.requested_hours
				if shares is not None:
					hours = shares.get((request.employee, date), 0)
					# a rest day has no share of the week, unless it was worked
					# in place of a working day: then it draws on the week's total
					if not flt(hours) and (request.employee, date) not in swaps:
						date = add_days(date, 1)
						continue

				if (request.employee, date) in on_leave:
					# someone on leave was never going to work the overtime,
					# so the day is neither paid nor counted as missing
					if found is not None:
						found.days_on_leave += 1
				elif is_range and (request.employee, date) not in attended:
					if found is not None:
						found.days_without_attendance += 1
				else:
					rows.append(
						frappe._dict(
							request=request.request,
							overtime_date=date,
							overtime_type=request.overtime_type,
							employee=request.employee,
							employee_name=request.employee_name,
							requested_hours=hours,
						)
					)
				date = add_days(date, 1)
		return rows

	def _budget_lines(self, chosen=None) -> list:
		"""Budget lines of the approved Hours Budget requests overlapping this
		period. A batch for one Unit/Division takes that unit's lines and the
		ones naming only a Department — the unit then narrows who is in them."""
		if not frappe.db.exists("DocType", "Overtime Request Budget"):
			return []
		values = {"company": self.company, "from_date": self.from_date, "to_date": self.to_date}
		filters = ""
		if chosen:
			filters += " and req.name in %(chosen)s"
			values["chosen"] = tuple(chosen)
		if self.custom_farm:
			filters += " and ifnull(line.custom_farm, '') in ('', %(custom_farm)s)"
			values["custom_farm"] = self.custom_farm
		return frappe.db.sql(
			f"""
			select req.name as request, req.overtime_date,
				coalesce(req.to_date, req.overtime_date) as request_to_date,
				req.overtime_type, line.name as budget, line.custom_farm, line.department
			from `tabOvertime Request` req
			join `tabOvertime Request Budget` line
				on line.parent = req.name and line.parenttype = 'Overtime Request'
			where req.docstatus = 1
				and req.company = %(company)s
				and req.overtime_date <= %(to_date)s
				and coalesce(req.to_date, req.overtime_date) >= %(from_date)s
				{filters}
			order by req.overtime_date, line.idx
			""",
			values,
			as_dict=True,
		)

	def _budget_employees(self, lines) -> dict:
		"""``{request: {employee: (line, employee record)}}`` — the Active
		employees each budget line covers. Someone two lines of one request
		both cover is counted against the more specific one, Unit/Division and
		Department together before either alone."""
		if not lines:
			return {}
		has_farm = "custom_farm" in frappe.db.get_table_columns("Employee")
		fields = ["name", "employee_name", "department"] + (["custom_farm"] if has_farm else [])
		scope = []
		departments = {line.department for line in lines if line.department}
		farms = {line.custom_farm for line in lines if line.custom_farm}
		if departments:
			scope.append(["department", "in", list(departments)])
		if farms and has_farm:
			scope.append(["custom_farm", "in", list(farms)])
		if not scope:
			return {}
		filters = {"company": self.company, "status": "Active"}
		if self.custom_farm and has_farm:
			filters["custom_farm"] = self.custom_farm
		employees = frappe.get_all("Employee", filters=filters, or_filters=scope, fields=fields)

		by_request = {}
		specific_first = sorted(lines, key=lambda line: not (line.department and line.custom_farm))
		for line in specific_first:
			covered = by_request.setdefault(line.request, {})
			for employee in employees:
				if employee.name in covered:
					continue
				if line.department and employee.department != line.department:
					continue
				if line.custom_farm and employee.get("custom_farm") != line.custom_farm:
					continue
				covered[employee.name] = (line, employee)
		return by_request

	def _budget_request_rows(self, found=None, chosen=None) -> list:
		"""One row per employee per day with Present attendance, for everyone a
		budget covers. Nobody is named, so each asks for nothing yet —
		``refresh_entries`` sets that to the overtime they worked, and
		:meth:`drop_budget_rows_without_overtime` takes out the days with none."""
		lines = self._budget_lines(chosen)
		if found is not None:
			found.requests += len({line.request for line in lines})
		covered = self._budget_employees(lines)
		if not covered:
			return []

		everyone = {employee for request in covered.values() for employee in request}
		if not everyone:
			return []
		attended = sorted(self._attendance_by_day(everyone))
		period_start, period_end = getdate(self.from_date), getdate(self.to_date)
		spans = {
			line.request: (
				max(getdate(line.overtime_date), period_start),
				min(getdate(line.request_to_date), period_end),
				line.overtime_type,
			)
			for line in lines
		}

		rows, taken = [], set()
		for request, members in covered.items():
			start, end, overtime_type = spans[request]
			for employee, date in attended:
				if employee not in members or not (start <= date <= end) or (employee, date) in taken:
					continue
				taken.add((employee, date))
				line, record = members[employee]
				rows.append(
					frappe._dict(
						request=request,
						overtime_date=date,
						overtime_type=overtime_type,
						employee=employee,
						employee_name=record.employee_name,
						requested_hours=0,
						budget=line.budget,
						budget_scope=budget_scope(line),
					)
				)
		return rows

	def drop_budget_rows_without_overtime(self):
		"""A budget covers a whole department, most of whom go home on time on
		any given day; only the days someone worked overtime are listed."""
		kept = [row for row in self.bulk_overtime_entries if _worth_listing(row)]
		if len(kept) == len(self.bulk_overtime_entries):
			return
		self.set("bulk_overtime_entries", [])
		for idx, row in enumerate(kept, start=1):
			row.idx = idx
			self.append("bulk_overtime_entries", row)

	# ──────────────────────────────────────────────────────────────────────
	# Hours Budget
	# ──────────────────────────────────────────────────────────────────────

	def budget_usage(self) -> list:
		"""Per budget line this batch draws on: the hours and people it takes,
		beside what submitted batches have already taken from it."""
		lines = {row.overtime_budget for row in self.bulk_overtime_entries if row.overtime_budget}
		if not lines:
			return []

		budgets = {
			line.name: line
			for line in frappe.get_all(
				"Overtime Request Budget",
				filters={"name": ["in", list(lines)]},
				fields=["name", "parent", "custom_farm", "department", "budget_hours", "max_employees"],
			)
		}
		taken = frappe.db.sql(
			"""
			select entry.overtime_budget, entry.employee, entry.approved_hours
			from `tabBulk Overtime Entry` entry
			join `tabBulk Overtime` bo on bo.name = entry.parent
			where entry.parenttype = 'Bulk Overtime'
				and bo.docstatus = 1
				and bo.name != %(name)s
				and entry.overtime_budget in %(lines)s
				and entry.approved_hours > 0
			""",
			{"name": self.name or "", "lines": tuple(lines)},
			as_dict=True,
		)

		usage = {}
		for name, line in budgets.items():
			usage[name] = frappe._dict(
				line=line, scope=budget_scope(line), elsewhere=0.0, here=0.0, people=set()
			)
		for row in taken:
			if row.overtime_budget in usage:
				usage[row.overtime_budget].elsewhere += flt(row.approved_hours)
				usage[row.overtime_budget].people.add(row.employee)
		for row in self.bulk_overtime_entries:
			if row.overtime_budget in usage and flt(row.approved_hours) > 0:
				usage[row.overtime_budget].here += flt(row.approved_hours)
				usage[row.overtime_budget].people.add(row.employee)
		return list(usage.values())

	def check_requested_hours(self, throw: bool):
		"""Nobody is paid past what their Overtime Request asked for.

		Counted across every live batch, not just this one: a Week request
		against the employee's weekly total, any other request against its
		hours per day on each date. The settling above already keeps inside
		these; this is what makes it impossible to submit a batch that does
		not — two batches saved side by side, say. On save it only warns.
		"""
		overruns = self.requested_overruns()
		if not overruns:
			return
		lines = []
		for over in overruns[:MAX_NAMES_IN_MESSAGE]:
			where = get_link_to_form("Overtime Request", over.request)
			if over.date:
				where += ", " + frappe.format(over.date, "Date")
			lines.append(
				_("{0} ({1}): {2} h of {3} h requested, {4} h over.").format(
					frappe.bold(over.employee_name or over.employee),
					where,
					frappe.format(over.paid, "Float"),
					frappe.format(over.requested, "Float"),
					frappe.format(round(over.paid - over.requested, 2), "Float"),
				)
			)
		if len(overruns) > MAX_NAMES_IN_MESSAGE:
			lines.append(_("and {0} more.").format(len(overruns) - MAX_NAMES_IN_MESSAGE))
		message = "<br>".join(lines)
		if throw:
			frappe.throw(message, title=_("More Than Requested"))
		frappe.msgprint(message, title=_("More Than Requested"), indicator="orange")

	def requested_overruns(self) -> list:
		"""Each employee, request (and, off a Week request, date) whose approved
		hours in all live batches together pass the hours requested."""
		rows = [
			row
			for row in self.bulk_overtime_entries
			if row.overtime_request and not row.get("overtime_budget") and flt(row.approved_hours) > 0
		]
		if not rows:
			return []

		requests = frappe.get_all(
			"Overtime Request",
			filters={"name": ["in", list({row.overtime_request for row in rows})]},
			fields=["name", "request_for", "request_mode"],
		)
		weekly = {r.name for r in requests if r.request_for == WEEK and r.request_mode != HOURS_BUDGET}
		named = {r.name for r in requests if r.request_mode != HOURS_BUDGET}
		if not named:
			return []

		requested = {}
		for line in frappe.get_all(
			"Overtime Request Employee",
			filters={"parenttype": "Overtime Request", "parent": ["in", list(named)]},
			fields=["parent", "employee", "requested_hours"],
		):
			key = (line.parent, line.employee)
			requested[key] = requested.get(key, 0.0) + flt(line.requested_hours)

		def key_of(request, employee, date):
			return (request, employee, None if request in weekly else getdate(date))

		paid, names = {}, {}
		for row in rows:
			if row.overtime_request not in named:
				continue
			key = key_of(row.overtime_request, row.employee, row.overtime_date)
			paid[key] = paid.get(key, 0.0) + flt(row.approved_hours)
			names[row.employee] = row.employee_name

		for other in frappe.db.sql(
			"""
			select entry.overtime_request, entry.employee, entry.overtime_date,
				sum(entry.approved_hours) as hours
			from `tabBulk Overtime Entry` entry
			join `tabBulk Overtime` batch on batch.name = entry.parent
			where entry.parenttype = 'Bulk Overtime'
				and batch.docstatus < 2
				and batch.name != %(batch)s
				and entry.overtime_request in %(requests)s
				and coalesce(entry.overtime_budget, '') = ''
			group by entry.overtime_request, entry.employee, entry.overtime_date
			""",
			{"batch": self.name or "", "requests": tuple(named)},
			as_dict=True,
		):
			key = key_of(other.overtime_request, other.employee, other.overtime_date)
			if key in paid:
				paid[key] += flt(other.hours)

		overruns = []
		for (request, employee, date), hours in paid.items():
			allowed = round(requested.get((request, employee), 0.0), 2)
			hours = round(hours, 2)
			if hours > allowed + 0.001:
				overruns.append(
					frappe._dict(
						request=request,
						employee=employee,
						employee_name=names.get(employee),
						date=date,
						paid=hours,
						requested=allowed,
					)
				)
		return sorted(
			overruns, key=lambda o: (o.employee_name or o.employee, o.date or getdate(self.from_date))
		)

	def check_budgets(self, throw: bool):
		"""A budget is the most that may be paid from it, in hours and in
		people. On save this only warns, so people can be removed until it
		fits; the submit refuses."""
		problems = []
		for use in self.budget_usage():
			budget = flt(use.line.budget_hours)
			total = use.elsewhere + use.here
			if total > budget + 0.001:
				problems.append(
					_("{0} ({1}): {2} h of {3} h budgeted, {4} h over.").format(
						frappe.bold(use.scope),
						get_link_to_form("Overtime Request", use.line.parent),
						frappe.format(total, "Float"),
						frappe.format(budget, "Float"),
						frappe.bold(frappe.format(total - budget, "Float")),
					)
					+ (
						" "
						+ _("{0} h already paid by other batches.").format(
							frappe.format(use.elsewhere, "Float")
						)
						if use.elsewhere
						else ""
					)
				)
			limit = cint(use.line.max_employees)
			if limit and len(use.people) > limit:
				problems.append(
					_("{0} ({1}): {2} employees, the budget allows {3}.").format(
						frappe.bold(use.scope),
						get_link_to_form("Overtime Request", use.line.parent),
						len(use.people),
						limit,
					)
				)
		if not problems:
			return

		message = _(
			"This batch goes past its overtime budget:<br>{0}<br>Remove people from the table until it fits."
		).format("<br>".join(problems))
		if throw:
			frappe.throw(message, title=_("Over Budget"))
		frappe.msgprint(message, title=_("Over Budget"), indicator="orange")

	def update_budget_usage(self):
		"""Write each budget line's Hours Used from the submitted batches, so
		the request shows what is left. Read from the database rather than
		counted up and down, so a cancel and a resubmit cannot drift it."""
		lines = {row.overtime_budget for row in self.bulk_overtime_entries if row.overtime_budget}
		if not lines:
			return
		used = dict(
			frappe.db.sql(
				"""
				select entry.overtime_budget, sum(entry.approved_hours)
				from `tabBulk Overtime Entry` entry
				join `tabBulk Overtime` bo on bo.name = entry.parent
				where entry.parenttype = 'Bulk Overtime'
					and bo.docstatus = 1
					and entry.overtime_budget in %(lines)s
				group by entry.overtime_budget
				""",
				{"lines": tuple(lines)},
			)
		)
		for line in lines:
			hours = flt(used.get(line))
			budget = flt(frappe.db.get_value("Overtime Request Budget", line, "budget_hours"))
			frappe.db.set_value(
				"Overtime Request Budget",
				line,
				{"hours_used": hours, "hours_left": max(budget - hours, 0)},
				update_modified=False,
			)

	def _why_nothing_to_pay(self, chosen=None) -> dict:
		"""What the attendance actually says, when it says nothing payable.

		"No attendance" is rarely the whole truth and never the useful part of
		it: the days are usually there and marked Absent or On Leave, and the
		biometric behind them may not have been worked into attendance yet.
		Saying which sends people to the right place instead of to this form.

		Only asked for when a fetch comes back empty, so its cost is paid on
		the one path where it is worth paying.
		"""
		# the employees this fetch was actually about, so the figures match
		# what was asked for rather than everything in the period
		scope = {"company": self.company, "from_date": self.from_date, "to_date": self.to_date}
		chosen_filter = ""
		if chosen:
			chosen_filter = "and req.name in %(chosen)s"
			scope["chosen"] = tuple(chosen)

		employees = [
			row.employee
			for row in frappe.db.sql(
				f"""
				select distinct row.employee
				from `tabOvertime Request` req
				join `tabOvertime Request Employee` row
					on row.parent = req.name and row.parenttype = 'Overtime Request'
				where req.docstatus = 1
					and req.company = %(company)s
					and req.overtime_date <= %(to_date)s
					and coalesce(req.to_date, req.overtime_date) >= %(from_date)s
					{chosen_filter}
				""",
				scope,
				as_dict=True,
			)
		]
		budgeted = self._budget_employees(self._budget_lines(chosen))
		employees += sorted({e for members in budgeted.values() for e in members} - set(employees))
		if not employees:
			return {}

		values = {"employees": tuple(employees), "from_date": self.from_date, "to_date": self.to_date}
		statuses = frappe.db.sql(
			"""
			select status, count(*) as days
			from `tabAttendance`
			where docstatus = 1 and employee in %(employees)s
				and attendance_date between %(from_date)s and %(to_date)s
				and status != 'On Leave'
			group by status
			order by days desc
			""",
			values,
			as_dict=True,
		)
		checkins = frappe.db.sql(
			"""
			select count(*) from `tabEmployee Checkin`
			where employee in %(employees)s
				and time >= %(from_date)s and time < date_add(%(to_date)s, interval 1 day)
			""",
			values,
		)[0][0]

		return {
			"employees": len(employees),
			"statuses": [{"status": row.status, "days": row.days} for row in statuses],
			"checkins": cint(checkins),
		}

	def _claimed_elsewhere(self, requests) -> dict:
		"""(employee, date) pairs already in another open or submitted Bulk
		Overtime, mapped to that document's link — a day is paid once."""
		if not requests:
			return {}
		rows = frappe.db.sql(
			"""
			select entry.employee, entry.overtime_date, bo.name
			from `tabBulk Overtime Entry` entry
			join `tabBulk Overtime` bo on bo.name = entry.parent
			where entry.parenttype = 'Bulk Overtime'
				and bo.docstatus < 2
				and bo.name != %(name)s
				and entry.overtime_date between %(from_date)s and %(to_date)s
				and entry.employee in %(employees)s
			""",
			{
				"name": self.name or "",
				"from_date": self.from_date,
				"to_date": self.to_date,
				"employees": tuple({r.employee for r in requests}),
			},
			as_dict=True,
		)
		return {
			(row.employee, str(row.overtime_date)): get_link_to_form("Bulk Overtime", row.name)
			for row in rows
		}

	def _employees_with_overlapping_slips(self, employees) -> dict:
		"""HRMS allows one Overtime Slip per employee per period, so an employee
		who already has one overlapping these dates cannot be paid here."""
		if not employees:
			return {}
		slips = frappe.get_all(
			"Overtime Slip",
			filters={
				"employee": ["in", list(employees)],
				"docstatus": ["<", 2],
				"start_date": ["<=", self.to_date],
				"end_date": [">=", self.from_date],
			},
			fields=["name", "employee", "custom_bulk_overtime"],
		)
		return {
			slip.employee: get_link_to_form("Overtime Slip", slip.name)
			for slip in slips
			if not self.name or slip.custom_bulk_overtime != self.name
		}

	# ──────────────────────────────────────────────────────────────────────
	# Attendance -> hours
	# ──────────────────────────────────────────────────────────────────────

	def refresh_entries(self):
		"""Re-read attendance for every row and recompute its hours, day type and
		status. Runs on every save, so the figures are the attendance as it
		stands when the document is submitted."""
		rows = self.bulk_overtime_entries
		if not rows:
			return

		attendance = self._attendance_by_day({row.employee for row in rows})
		shifts = {a.shift for a in attendance.values() if a.shift}
		shift_hours = _shift_lengths(shifts)
		shift_types = _shift_types(shifts)
		punches = _punches_by_day(
			[
				(row.employee, getdate(row.overtime_date), attendance.get((row.employee, getdate(row.overtime_date))))
				for row in rows
			],
			shift_types,
			self.from_date,
			self.to_date,
		)
		default_hours = flt(frappe.db.get_single_value("HR Settings", "standard_working_hours"))
		caps = _daily_caps({row.overtime_type for row in rows if row.overtime_type})
		day_types = _DayTypes(self.from_date, self.to_date)
		swaps = self.swapped_rest_days({row.employee for row in rows})

		for row in rows:
			date = getdate(row.overtime_date)
			record = attendance.get((row.employee, date))

			row.day_type = day_types.get(row.employee, date)
			if (row.employee, date) in swaps:
				# their off was taken on another day of the week
				row.day_type = engine.WORKING_DAY
			row.attendance = record.name if record else None
			row.shift = record.shift if record else None

			# Worked is the attendance's own figure. Hours entered by hand once
			# lived here; they are the overtime now (below), so a row still
			# carrying that old mark goes back to the attendance.
			row.manual_working_hours = 0
			worked = flt(record.working_hours) if record else 0
			shift_type = shift_types.get(row.shift)
			day_punches = punches.get((row.employee, date)) or []
			first_in = record.in_time if record else None
			if record and _labels_decide(shift_type) and len(day_punches) >= 2:
				# The shift reads IN/OUT labels strictly, and readers mislabel
				# scans: IN, IN or OUT, OUT leaves HRMS with no hours at all.
				# The first punch is the arrival and the last the departure,
				# whatever they are labelled.
				first_in = day_punches[0]
				worked = round((day_punches[-1] - first_in).total_seconds() / 3600, 4)
			row.working_hours = worked
			row.shift_hours = shift_hours.get(row.shift) or default_hours

			# Hours are never typed in: a row once marked as edited by hand
			# goes back to what the attendance says.
			row.manual_biometric_hours = row.manual_override = 0
			row.early_in_hours = 0
			if record and row.day_type == engine.WORKING_DAY and shift_type and shift_type.start_time is not None:
				row.early_in_hours = min(
					engine.hours_before_shift(first_in, _at(date, shift_type.start_time)),
					worked,
				)
			cap = caps.get(row.overtime_type) or 0
			row.biometric_hours = engine.biometric_overtime(
				worked,
				row.shift_hours,
				row.day_type,
				maximum_hours=cap,
				early_hours=row.early_in_hours,
			)
			if row.overtime_budget:
				# nobody named this person, so they ask for what they worked;
				# the budget, not the row, is what holds the total down
				row.requested_hours = row.biometric_hours
			row.approved_hours, row.status = engine.settle(
				row.requested_hours,
				row.biometric_hours,
				has_attendance=bool(record),
				has_hours=worked > 0,
				# a rest day pays every hour worked, so it needs no shift length
				has_shift=bool(row.shift_hours) or row.day_type != engine.WORKING_DAY,
			)

		self.settle_weeks()

	def settle_weeks(self):
		"""Pay each Week request against the employee's weekly total.

		The per-day settle above caps every day at its even share, so 3 hours
		on Monday and none on Tuesday would pay only one share. A Week request
		approves hours for the week, so its days are settled again as one:
		in date order, each up to its biometric overtime, until the week's
		total — less what other batches already paid — is used up.
		"""
		rows = [
			row
			for row in self.bulk_overtime_entries
			if row.overtime_request and not row.get("overtime_budget")
		]
		weeks = self.weekly_allowances(rows)
		if not weeks:
			return

		grouped = {}
		for row in rows:
			key = (row.overtime_request, row.employee)
			if key in weeks and row.status not in UNPAYABLE:
				grouped.setdefault(key, []).append(row)

		for key, days in grouped.items():
			days.sort(key=lambda r: getdate(r.overtime_date))
			week = weeks[key]
			settled = engine.settle_week(
				week.requested - week.elsewhere,
				[flt(r.requested_hours) for r in days],
				[flt(r.biometric_hours) for r in days],
			)
			for row, (hours, status) in zip(days, settled, strict=True):
				row.approved_hours, row.status = hours, status

	def weekly_allowances(self, rows) -> dict:
		"""``{(request, employee): {requested, elsewhere}}`` for the rows that
		come from a Week request: the week's total and what other live batches
		have already paid of it."""
		requests = {row.overtime_request for row in rows}
		if not requests:
			return {}
		week_requests = frappe.get_all(
			"Overtime Request",
			filters={
				"name": ["in", list(requests)],
				"request_for": WEEK,
				"request_mode": ["!=", HOURS_BUDGET],
			},
			pluck="name",
		)
		if not week_requests:
			return {}

		weeks = {}
		for line in frappe.get_all(
			"Overtime Request Employee",
			filters={"parenttype": "Overtime Request", "parent": ["in", week_requests]},
			fields=["parent", "employee", "requested_hours"],
		):
			week = weeks.setdefault((line.parent, line.employee), frappe._dict(requested=0.0, elsewhere=0.0))
			week.requested += flt(line.requested_hours)

		for paid in frappe.db.sql(
			"""
			select entry.overtime_request, entry.employee, sum(entry.approved_hours) as hours
			from `tabBulk Overtime Entry` entry
			join `tabBulk Overtime` batch on batch.name = entry.parent
			where entry.parenttype = 'Bulk Overtime'
				and batch.docstatus < 2
				and batch.name != %(batch)s
				and entry.overtime_request in %(requests)s
			group by entry.overtime_request, entry.employee
			""",
			{"batch": self.name or "", "requests": tuple(week_requests)},
			as_dict=True,
		):
			week = weeks.get((paid.overtime_request, paid.employee))
			if week:
				week.elsewhere += flt(paid.hours)
		return weeks

	def _leave_days(self, employees, from_date=None, to_date=None) -> set:
		"""(employee, date) pairs of full days on leave in this period: marked
		On Leave, or covered by an approved Leave Application the attendance
		has not caught up with yet. A half day still leaves time to work."""
		if not employees:
			return set()
		from_date, to_date = from_date or self.from_date, to_date or self.to_date
		days = {
			(row.employee, getdate(row.attendance_date))
			for row in frappe.get_all(
				"Attendance",
				filters={
					"employee": ["in", list(employees)],
					"attendance_date": ["between", [from_date, to_date]],
					"docstatus": 1,
					"status": "On Leave",
				},
				fields=["employee", "attendance_date"],
			)
		}
		if not frappe.db.exists("DocType", "Leave Application"):
			return days

		period_start, period_end = getdate(from_date), getdate(to_date)
		for leave in frappe.get_all(
			"Leave Application",
			filters={
				"employee": ["in", list(employees)],
				"docstatus": 1,
				"status": "Approved",
				"from_date": ["<=", to_date],
				"to_date": [">=", from_date],
			},
			fields=["employee", "from_date", "to_date", "half_day", "half_day_date"],
		):
			half = getdate(leave.half_day_date or leave.from_date) if leave.half_day else None
			date = max(getdate(leave.from_date), period_start)
			while date <= min(getdate(leave.to_date), period_end):
				if date != half:
					days.add((leave.employee, date))
				date = add_days(date, 1)
		return days

	def swapped_rest_days(self, employees) -> dict:
		"""``{(employee, rest day): day off}`` for rest days worked in exchange
		for a working day off in the same Monday-to-Sunday week.

		Someone who works their rest day and stays home on a working day of
		that week has moved their off, not given up a day: the rest day is a
		working day for them, and pays only what lies beyond the shift. A
		working day counts as taken off when it has no Present, Half Day or
		Work From Home attendance and no leave, falls within their employment
		and is before today. The pairing is
		:func:`engine.swapped_rest_days`.
		"""
		if not employees or not (self.from_date and self.to_date):
			return {}
		start = add_days(getdate(self.from_date), -getdate(self.from_date).weekday())
		end = add_days(getdate(self.to_date), 6 - getdate(self.to_date).weekday())

		worked = {
			(a.employee, getdate(a.attendance_date))
			for a in frappe.get_all(
				"Attendance",
				filters={
					"employee": ["in", list(employees)],
					"attendance_date": ["between", [start, end]],
					"docstatus": 1,
					"status": ["in", ["Present", "Half Day", "Work From Home"]],
				},
				fields=["employee", "attendance_date", "working_hours", "status"],
			)
			if a.status != "Present" or flt(a.working_hours) > 0
		}
		on_leave = self._leave_days(employees, start, end)
		employment = {
			e.name: e
			for e in frappe.get_all(
				"Employee",
				filters={"name": ["in", list(employees)]},
				fields=["name", "date_of_joining", "relieving_date"],
			)
		}
		day_types = _DayTypes(start, end)
		# today's attendance is not in yet, so it is never a day off
		last = min(end, add_days(getdate(today()), -1))

		swaps = {}
		for employee in employees:
			joined = employment.get(employee) and employment[employee].date_of_joining
			relieved = employment.get(employee) and employment[employee].relieving_date
			monday = start
			while monday <= end:
				week = []
				for offset in range(7):
					date = add_days(monday, offset)
					due = (
						date <= last
						and (not joined or date >= getdate(joined))
						and (not relieved or date <= getdate(relieved))
						and (employee, date) not in on_leave
					)
					at_work = (employee, date) in worked
					week.append((date, day_types.get(employee, date), at_work, due and not at_work))
				for rest_day, day_off in engine.swapped_rest_days(week).items():
					swaps[(employee, rest_day)] = day_off
				monday = add_days(monday, 7)
		return swaps

	def _attendance_by_day(self, employees) -> dict:
		records = frappe.get_all(
			"Attendance",
			filters={
				"employee": ["in", list(employees)],
				"attendance_date": ["between", [self.from_date, self.to_date]],
				"docstatus": 1,
				"status": "Present",
			},
			fields=["name", "employee", "attendance_date", "working_hours", "shift", "in_time"],
		)
		return {(r.employee, getdate(r.attendance_date)): r for r in records}

	# ──────────────────────────────────────────────────────────────────────
	# Verify against the raw check-ins
	# ──────────────────────────────────────────────────────────────────────

	@frappe.whitelist()
	def verify_checkins(self):
		"""Each row beside the punches behind it: first and last check-in of
		the shift, the hours between them, the shift's own length and what lies
		beyond it — so the hours paid can be checked against the scanner rather
		than against the attendance alone.

		First and last punch are taken whatever their IN/OUT label, since
		readers mislabel scans. A shift's punches are the ones linked to its
		attendance; failing that, those inside the shift's check-in window.
		"""
		rows = self.bulk_overtime_entries
		if not rows:
			return []

		shifts = _shift_types({row.shift for row in rows if row.shift})
		default_hours = flt(frappe.db.get_single_value("HR Settings", "standard_working_hours"))

		employees = list({row.employee for row in rows})
		punches_by_day = _punches_by_day(
			[
				(row.employee, getdate(row.overtime_date), frappe._dict(name=row.attendance, shift=row.shift))
				for row in rows
			],
			shifts,
			self.from_date,
			self.to_date,
		)

		weeks = self.weekly_allowances(
			[row for row in rows if row.overtime_request and not row.get("overtime_budget")]
		)

		swaps = self.swapped_rest_days(set(employees))

		result = []
		for row in rows:
			week = weeks.get((row.overtime_request, row.employee))
			date = getdate(row.overtime_date)
			shift = shifts.get(row.shift)
			punches = punches_by_day.get((row.employee, date)) or []

			first = punches[0] if punches else None
			last = punches[-1] if len(punches) > 1 else None
			punch_hours = round((last - first).total_seconds() / 3600, 2) if last else 0.0

			shift_hours = (
				flt(row.shift_hours)
				or (engine.shift_length_hours(shift.start_time, shift.end_time) if shift else None)
				or default_hours
			)
			if row.day_type == engine.WORKING_DAY:
				before = 0.0
				if first and shift and shift.start_time is not None:
					before = min(engine.hours_before_shift(first, _at(date, shift.start_time)), punch_hours)
				beyond = round(max(punch_hours - before - flt(shift_hours), 0), 2)
			else:
				beyond = punch_hours

			if not punches:
				check = "No Punches"
			elif not last:
				check = "One Punch"
			elif abs(beyond - flt(row.biometric_hours)) > 0.1:
				check = "Differs"
			else:
				check = "Agrees"

			result.append(
				{
					"idx": row.idx,
					"employee": row.employee,
					"employee_name": row.employee_name,
					"overtime_date": row.overtime_date,
					"day_type": row.day_type,
					"day_off": swaps.get((row.employee, getdate(row.overtime_date))),
					"shift": row.shift,
					"shift_start": shift.start_time if shift else None,
					"shift_end": shift.end_time if shift else None,
					"first_in": first,
					"last_out": last,
					"punches": len(punches),
					"punch_hours": punch_hours,
					"shift_hours": round(flt(shift_hours), 2),
					"beyond_shift": beyond,
					"worked_hours": flt(row.working_hours),
					"requested_hours": flt(row.requested_hours),
					"approved_hours": flt(row.approved_hours),
					"overtime_request": row.overtime_request,
					"week_requested_hours": round(week.requested, 2) if week else None,
					"week_paid_elsewhere": round(week.elsewhere, 2) if week else 0,
					"status": row.status,
					"check": check,
				}
			)
		return result

	# ──────────────────────────────────────────────────────────────────────
	# Submit / cancel
	# ──────────────────────────────────────────────────────────────────────

	def on_submit(self):
		self.create_overtime_slips()
		self.update_budget_usage()

	def create_overtime_slips(self):
		"""One Overtime Slip per employee, one line per date. Submitting a slip
		prices it and creates its Additional Salary. All or nothing: every
		failure is collected, then the whole submit is rolled back with the
		list, so there is never a half-paid batch to untangle."""
		ensure_overtime_setup()

		by_employee = {}
		for row in self.bulk_overtime_entries:
			if flt(row.approved_hours) > 0:
				by_employee.setdefault(row.employee, []).append(row)

		errors = []
		for employee, rows in by_employee.items():
			try:
				frappe.db.savepoint(SAVEPOINT)
				slip = frappe.get_doc(
					{
						"doctype": "Overtime Slip",
						"employee": employee,
						"company": self.company,
						"posting_date": self.to_date,
						"start_date": self.from_date,
						"end_date": self.to_date,
						"custom_bulk_overtime": self.name,
						# HRMS only totals the slip in its own fetch button, so a
						# slip built here would otherwise read 0 hours on the form.
						"total_overtime_duration": sum(flt(row.approved_hours) for row in rows),
						"overtime_details": [
							{
								"date": row.overtime_date,
								"overtime_type": row.overtime_type,
								"overtime_duration": flt(row.approved_hours),
								"standard_working_hours": flt(row.shift_hours),
								"reference_document": row.attendance,
							}
							for row in sorted(rows, key=lambda r: getdate(r.overtime_date))
						],
					}
				)
				slip.insert(ignore_permissions=True)
				slip.submit()
			except Exception as e:
				frappe.db.rollback(save_point=SAVEPOINT)
				errors.append("{0}: {1}".format(rows[0].employee_name or employee, _first_line(e)))
				continue

			for row in rows:
				row.db_set("overtime_slip", slip.name, update_modified=False)

		if errors:
			shown = errors[:MAX_NAMES_IN_MESSAGE]
			if len(errors) > len(shown):
				shown.append(_("... and {0} more").format(len(errors) - len(shown)))
			frappe.throw(
				_("No Overtime Slips were created. Fix these and submit again:<br>{0}").format(
					"<br>".join(shown)
				),
				title=_("Overtime Slips Failed"),
			)

		frappe.msgprint(
			_("{0} Overtime Slip(s) created.").format(len(by_employee)), indicator="green", alert=True
		)

	def on_cancel(self):
		"""Cancel this batch's Overtime Slips and the Additional Salary each one
		created. An Additional Salary already in a submitted Salary Slip refuses
		to cancel, which is right: that overtime has been paid."""
		slips = frappe.get_all(
			"Overtime Slip", filters={"custom_bulk_overtime": self.name, "docstatus": 1}, pluck="name"
		)
		for slip in slips:
			for additional_salary in frappe.get_all(
				"Additional Salary",
				filters={"ref_doctype": "Overtime Slip", "ref_docname": slip, "docstatus": 1},
				pluck="name",
			):
				frappe.get_doc("Additional Salary", additional_salary).cancel()
			frappe.get_doc("Overtime Slip", slip).cancel()

		self.update_budget_usage()
		if slips:
			frappe.msgprint(
				_("{0} Overtime Slip(s) cancelled.").format(len(slips)), indicator="orange", alert=True
			)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _daily_caps(overtime_types) -> dict:
	"""Maximum Overtime Hours Allowed per Overtime Type, as a per-day cap. 0 or
	blank on the type means no cap."""
	if not overtime_types:
		return {}
	rows = frappe.get_all(
		"Overtime Type",
		filters={"name": ["in", list(overtime_types)]},
		fields=["name", "maximum_overtime_hours_allowed"],
	)
	return {row.name: flt(row.maximum_overtime_hours_allowed) for row in rows}


def _worth_listing(row) -> bool:
	"""Whether a row stays in the table. A budget row with no overtime is just
	someone who went home on time; nobody named them, and nothing can be
	typed in for them, so there is nothing for HR to act on."""
	return not row.overtime_budget or flt(row.approved_hours) > 0


def _hours_by_date(requests) -> dict:
	"""Per-date hours of the requests that carry them (Week requests), as
	``{request: {(employee, date): hours}}``. A request with none is absent,
	and pays its hours per day."""
	if not requests or not frappe.db.exists("DocType", "Overtime Request Date"):
		return {}
	rows = frappe.get_all(
		"Overtime Request Date",
		filters={"parenttype": "Overtime Request", "parent": ["in", list(requests)]},
		fields=["parent", "employee", "overtime_date", "requested_hours"],
	)
	shares = {}
	for row in rows:
		shares.setdefault(row.parent, {})[(row.employee, getdate(row.overtime_date))] = flt(
			row.requested_hours
		)
	return shares


def _shift_window(date, shift):
	"""Where a shift's punches can fall: from the check-in allowance before
	its start to the check-out allowance after its end, running into the next
	day for a night shift. The calendar day when there is no shift."""
	import datetime

	day = datetime.datetime.combine(getdate(date), datetime.time())
	if not shift or shift.start_time is None or shift.end_time is None:
		return day, day + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)

	def as_delta(value):
		if isinstance(value, datetime.timedelta):
			return value
		hours = engine._hours(value) or 0
		return datetime.timedelta(hours=hours)

	start = day + as_delta(shift.start_time)
	end = day + as_delta(shift.end_time)
	if end <= start:
		end += datetime.timedelta(days=1)
	before = datetime.timedelta(minutes=cint(shift.begin_check_in_before_shift_start_time) or 60)
	after = datetime.timedelta(minutes=cint(shift.allow_check_out_after_shift_end_time) or 60)
	# overtime runs past the shift's end, so the window stays open until the
	# next shift could begin rather than closing at the check-out allowance
	return start - before, max(end + after, start - before + datetime.timedelta(hours=20))


def _request_names(value) -> list:
	"""The Overtime Requests a fetch was narrowed to.

	The desk sends a JSON array; a direct API call may pass a real list or a
	single name. Anything else is nothing to narrow by — and must not raise,
	since a fetch that cannot read its argument should still be able to sweep
	the period rather than fail the whole form.
	"""
	if not value:
		return []
	if isinstance(value, str):
		value = value.strip()
		if value.startswith("["):
			try:
				value = frappe.parse_json(value)
			except Exception:
				return []
		else:
			value = [value]
	if not isinstance(value, list | tuple | set):
		return []
	return [name for name in value if isinstance(name, str) and name.strip()]


def _at(date, time_of_day):
	"""``date`` at a Shift Type's ``start_time`` (a timedelta or time)."""
	import datetime

	return datetime.datetime.combine(getdate(date), datetime.time()) + datetime.timedelta(
		hours=engine._hours(time_of_day) or 0
	)


def _shift_types(shifts) -> dict:
	if not shifts:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Shift Type",
			filters={"name": ["in", list(shifts)]},
			fields=[
				"name",
				"start_time",
				"end_time",
				"begin_check_in_before_shift_start_time",
				"allow_check_out_after_shift_end_time",
				"determine_check_in_and_check_out",
			],
		)
	}


def _labels_decide(shift_type) -> bool:
	"""True when HRMS reads this shift's hours from the IN/OUT labels alone,
	so a mislabelled punch costs the day its hours. Alternating shifts already
	take the first punch as IN and the last as OUT."""
	return bool(shift_type) and (
		shift_type.determine_check_in_and_check_out == "Strictly based on Log Type in Employee Checkin"
	)


def _punches_by_day(days, shift_types, from_date, to_date) -> dict:
	"""``{(employee, date): [punch times, oldest first]}`` for each
	``(employee, date, attendance)`` in ``days``, whatever their IN/OUT label.

	A day's punches are the ones linked to its attendance; failing that, those
	inside the shift's check-in window (the calendar day without a shift).
	"""
	employees = list({employee for employee, _date, _attendance in days})
	if not employees:
		return {}
	by_attendance, by_employee = {}, {}
	for checkin in frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": ["in", employees],
			"time": ["between", [add_days(from_date, -1), add_days(to_date, 2)]],
		},
		fields=["employee", "time", "attendance"],
		order_by="time asc",
	):
		if checkin.attendance:
			by_attendance.setdefault(checkin.attendance, []).append(checkin.time)
		by_employee.setdefault(checkin.employee, []).append(checkin.time)

	found = {}
	for employee, date, attendance in days:
		punches = by_attendance.get(attendance.name) if attendance and attendance.name else None
		if not punches:
			shift = shift_types.get(attendance.shift) if attendance and attendance.shift else None
			start, end = _shift_window(date, shift)
			punches = [t for t in by_employee.get(employee, []) if start <= t <= end]
		found[(employee, date)] = punches
	return found


def _shift_lengths(shifts) -> dict:
	if not shifts:
		return {}
	rows = frappe.get_all(
		"Shift Type", filters={"name": ["in", list(shifts)]}, fields=["name", "start_time", "end_time"]
	)
	return {row.name: engine.shift_length_hours(row.start_time, row.end_time) for row in rows}


class _DayTypes:
	"""Working Day / Rest Day / Public Holiday for an employee on a date, from
	the holiday list in force *on that date* — Holiday List Assignments, then
	the company's — not from Employee.holiday_list, which HRMS ignores."""

	def __init__(self, from_date, to_date):
		self.from_date, self.to_date = from_date, to_date
		self.holidays = {}  # holiday list -> {date: weekly_off}

	def get(self, employee, date) -> str:
		from hrms.utils.holiday_list import get_holiday_list_for_employee

		holiday_list = get_holiday_list_for_employee(employee, raise_exception=False, as_on=date)
		if not holiday_list:
			return engine.WORKING_DAY

		if holiday_list not in self.holidays:
			rows = frappe.get_all(
				"Holiday",
				filters={"parent": holiday_list, "holiday_date": ["between", [self.from_date, self.to_date]]},
				fields=["holiday_date", "weekly_off"],
			)
			self.holidays[holiday_list] = {getdate(r.holiday_date): cint(r.weekly_off) for r in rows}

		weekly_off = self.holidays[holiday_list].get(date)
		if weekly_off is None:
			return engine.WORKING_DAY
		return engine.REST_DAY if weekly_off else engine.PUBLIC_HOLIDAY


def _first_line(exception) -> str:
	from frappe.utils import strip_html

	text = " ".join(strip_html(str(exception) or "").split())
	return text[:200] if text else _("failed — see the Error Log")


def ensure_overtime_setup():
	"""The one link Bulk Overtime needs on Overtime Slip, so cancelling a batch
	finds its slips. Idempotent; also run on migrate."""
	create_custom_fields(
		{
			"Overtime Slip": [
				{
					"fieldname": "custom_bulk_overtime",
					"label": "Bulk Overtime",
					"fieldtype": "Link",
					"options": "Bulk Overtime",
					"read_only": 1,
					"insert_after": "department",
				},
			],
		},
		ignore_validate=True,
	)
	_drop_legacy_amount_fields()


#: Fields an earlier Bulk Overtime added to stash the pay it worked out itself.
#: Pricing now belongs to the Overtime Slip, so they are dead weight on the form.
_LEGACY_DETAIL_FIELDS = ("custom_salary_component", "custom_amount")


def _drop_legacy_amount_fields():
	"""Remove those fields, but only while no Overtime Details row has a value
	in them — on a site that paid overtime the old way they are the record of
	what was paid, and deleting them would take the evidence with them."""
	columns = frappe.db.get_table_columns("Overtime Details")
	present = [field for field in _LEGACY_DETAIL_FIELDS if field in columns]
	if not present:
		return

	# Per fieldtype: a Currency column holding 0 is empty, but `ifnull(col, '')`
	# renders it as "0.0" and would read as a value.
	conditions = []
	for field in present:
		fieldtype = frappe.db.get_value("Custom Field", f"Overtime Details-{field}", "fieldtype")
		if fieldtype in ("Currency", "Float", "Int", "Percent"):
			conditions.append(f"ifnull(`{field}`, 0) != 0")
		else:
			conditions.append(f"ifnull(`{field}`, '') != ''")

	if frappe.db.sql("select 1 from `tabOvertime Details` where {0} limit 1".format(" or ".join(conditions))):
		return

	for field in present:
		name = f"Overtime Details-{field}"
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)


# ──────────────────────────────────────────────────────────────────────────
# Building a batch without being asked
# ──────────────────────────────────────────────────────────────────────────


def _requests_in_other_batches(requests, batch=None) -> set:
	"""Which of these requests an open or submitted Bulk Overtime other than
	``batch`` already pays some of."""
	if not requests:
		return set()
	return set(
		frappe.db.sql_list(
			"""
			select distinct entry.overtime_request
			from `tabBulk Overtime Entry` entry
			join `tabBulk Overtime` bo on bo.name = entry.parent
			where entry.parenttype = 'Bulk Overtime'
				and bo.docstatus < 2
				and bo.name != %(batch)s
				and entry.overtime_request in %(requests)s
			""",
			{"batch": batch or "", "requests": tuple(requests)},
		)
	)


def outstanding_days(overtime_request: str, batch: str | None = None) -> list:
	"""The (employee, date) rows of an approved request worked so far that no
	open or submitted Bulk Overtime other than ``batch`` holds yet.

	Read through the same fetch a batch runs, over the request's whole span up
	to today, so "left to pay" is exactly what a batch would take: a range's
	days without attendance and a week's rest days are not in it.
	"""
	request = frappe.db.get_value(
		"Overtime Request",
		overtime_request,
		["company", "custom_farm", "docstatus", "overtime_date", "to_date"],
		as_dict=True,
	)
	if not request or request.docstatus != 1 or not request.overtime_date:
		return []

	end = min(getdate(request.to_date or request.overtime_date), getdate(today()))
	if getdate(request.overtime_date) > end:
		return []

	probe = frappe.get_doc(
		{
			"doctype": "Bulk Overtime",
			"company": request.company,
			"custom_farm": request.custom_farm or "",
			"from_date": request.overtime_date,
			"to_date": end,
		}
	)
	probe.name = batch or ""
	rows = probe._approved_request_rows(chosen=[overtime_request])
	claimed = probe._claimed_elsewhere(rows)
	rows = [row for row in rows if (row.employee, str(row.overtime_date)) not in claimed]
	return _with_budget_overtime(probe, rows)


def _budget_overtime_entries(probe, rows) -> list:
	"""The budget rows among ``rows`` as entries measured against the
	attendance, keeping only the days with overtime. Uses ``probe``'s table,
	so it must be a document nothing else is about to save."""
	budget_rows = [row for row in rows if row.get("budget")]
	if not budget_rows:
		return []
	probe.set("bulk_overtime_entries", [])
	for row in budget_rows:
		probe.append(
			"bulk_overtime_entries",
			{
				"employee": row.employee,
				"employee_name": row.employee_name,
				"overtime_date": row.overtime_date,
				"overtime_type": row.overtime_type,
				"overtime_budget": row.budget,
				"budget_scope": row.budget_scope,
			},
		)
	probe.refresh_entries()
	return [entry for entry in probe.bulk_overtime_entries if _worth_listing(entry)]


def _with_budget_overtime(probe, rows) -> list:
	"""Budget rows only where the attendance shows overtime: a whole
	department's days at work are not days "left to pay"."""
	if not any(row.get("budget") for row in rows):
		return rows
	listed = {
		(entry.employee, getdate(entry.overtime_date)) for entry in _budget_overtime_entries(probe, rows)
	}
	return [
		row for row in rows if not row.get("budget") or (row.employee, getdate(row.overtime_date)) in listed
	]


def build_batch_for_request(overtime_request: str):
	"""An unsaved Bulk Overtime over what one approved request has left to
	pay, and what its fetch reported. ``get_overtime`` raises when the
	request has not been worked yet or every day of it is already paid."""
	request = frappe.db.get_value(
		"Overtime Request", overtime_request, ["company", "custom_farm"], as_dict=True
	)
	batch = frappe.get_doc(
		{
			"doctype": "Bulk Overtime",
			"company": request.company,
			"custom_farm": request.custom_farm or "",
		}
	)
	return batch, batch.get_overtime(overtime_requests=[overtime_request])


def batch_for_request(overtime_request: str, wait_until_worked: bool = False) -> str | None:
	"""Build and save the Bulk Overtime that pays what one approved request
	has left to pay.

	Returns the batch, or ``None`` when there is nothing to pay yet — which is
	the ordinary case at approval time, because a request is approved *before*
	the overtime is worked. Those are picked up later by :func:`create_pending_batches`,
	once the attendance for their days has arrived.

	``wait_until_worked`` is how the automatic runs call it: a request still
	running is left until its last day has passed, so a week becomes one batch
	rather than one a night. Anything a batch left behind — the last days of a
	week paid mid-week — is picked up by the next one. A day is paid once.
	"""
	request = frappe.db.get_value(
		"Overtime Request",
		overtime_request,
		["docstatus", "overtime_date", "to_date", "request_mode"],
		as_dict=True,
	)
	if not request or request.docstatus != 1:
		return None
	if request.request_mode == "Hours Budget":
		# someone has to choose who a budget pays, so it is never automatic
		return None
	if wait_until_worked and getdate(request.to_date or request.overtime_date) >= getdate(today()):
		return None
	if not outstanding_days(overtime_request):
		return None

	batch, result = build_batch_for_request(overtime_request)
	if not result["rows"]:
		return None

	batch.insert(ignore_permissions=True)
	return batch.name


def auto_create_batch(overtime_request: str):
	"""Called after a request is approved, and by the daily run. Never raises:
	an approval must not be undone because the attendance behind it has not
	arrived, or because a day of it is already being paid somewhere else."""
	try:
		name = batch_for_request(overtime_request, wait_until_worked=True)
	except Exception:
		frappe.log_error(
			title="Bulk Overtime: could not build a batch for {0}".format(overtime_request),
			message=frappe.get_traceback(),
		)
		return None

	# No commit here. The background job and the scheduled run each commit
	# their own work when they return, and committing inside would take the
	# caller's transaction with it — a test's rollback included.
	return name


def create_pending_batches(limit: int = 200):
	"""The daily run: approved requests whose last day has passed and which
	still have days worked that no batch has picked up.

	Overtime is approved in advance, so at approval time there is usually
	nothing to pay. This is what turns those into batches once the attendance
	is in, so nobody has to watch for it — including the days a batch made
	before the request was over left behind.
	"""
	requests = frappe.db.sql(
		"""
		select req.name
		from `tabOvertime Request` req
		where req.docstatus = 1
			and coalesce(req.to_date, req.overtime_date) < %(today)s
			and (
				coalesce(req.to_date, req.overtime_date) >= %(window_start)s
				or not exists (
					select 1
					from `tabBulk Overtime Entry` entry
					join `tabBulk Overtime` bo on bo.name = entry.parent
					where entry.parenttype = 'Bulk Overtime'
						and bo.docstatus < 2
						and entry.overtime_request = req.name
				)
			)
		order by req.overtime_date
		limit %(limit)s
		""",
		{
			"today": today(),
			"window_start": add_days(today(), -PICK_UP_WINDOW_DAYS),
			"limit": cint(limit),
		},
		as_dict=True,
	)

	built = []
	for request in requests:
		name = auto_create_batch(request.name)
		if name:
			built.append(name)
	return built
