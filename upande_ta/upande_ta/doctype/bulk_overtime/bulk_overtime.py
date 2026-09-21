# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Bulk Overtime — pays approved Overtime Requests against attendance.

The chain is::

    Overtime Request (approved)  ->  Bulk Overtime  ->  Overtime Slip  ->  Additional Salary

**Get Overtime** pulls every approved request overlapping the period and
expands it into one row per employee per date — a request may cover a day, a
week or a month, and its requested hours are per day — then checks each row
against that day's attendance. The hours paid
are the lower of what was requested and what the biometric shows was worked
(see ``upande_ta.upande_ta.overtime_engine``). HR may override a row by hand,
with a reason, up to the requested hours — for a missing clock-out, say.

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
from frappe.utils import add_days, cint, flt, get_link_to_form, getdate, today

from upande_ta.upande_ta import overtime_engine as engine

#: Isolates one employee's Overtime Slip from the next, so every failure can be
#: collected and reported together before the submit is rolled back.
SAVEPOINT = "bulk_overtime_slip"

#: Names listed individually in a message before it collapses to a count.
MAX_NAMES_IN_MESSAGE = 20


class BulkOvertime(Document):
	# ──────────────────────────────────────────────────────────────────────
	# Validation
	# ──────────────────────────────────────────────────────────────────────

	def validate(self):
		self.validate_dates()
		self.refresh_entries()
		self.set_totals()
		self.set_title()

	def before_submit(self):
		if not any(flt(row.approved_hours) > 0 for row in self.bulk_overtime_entries):
			frappe.throw(_("There are no approved hours to pay."))
		for row in self.bulk_overtime_entries:
			if flt(row.approved_hours) > 0 and not row.overtime_type:
				frappe.throw(
					_("Row #{0}: the Overtime Request it came from has no Overtime Type.").format(row.idx)
				)

	def validate_dates(self):
		if not (self.from_date and self.to_date):
			return
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date."))
		if getdate(self.to_date) > getdate(today()):
			frappe.throw(_("To Date cannot be in the future: overtime is paid once it has been worked."))

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

	# ──────────────────────────────────────────────────────────────────────
	# Get Overtime
	# ──────────────────────────────────────────────────────────────────────

	@frappe.whitelist()
	def get_overtime(self):
		"""Rebuild the table from the approved requests in the period. Rows HR
		has overridden by hand keep their hours and reason."""
		if not (self.company and self.from_date and self.to_date):
			frappe.throw(_("Set the Company, From Date and To Date first."))
		self.validate_dates()

		manual = {
			(row.employee, str(getdate(row.overtime_date))): row
			for row in self.bulk_overtime_entries
			if row.manual_override
		}

		found = frappe._dict(requests=0, days_without_attendance=0)
		requests = self._approved_request_rows(found)
		claimed = self._claimed_elsewhere(requests)
		blocked = self._employees_with_overlapping_slips({r.employee for r in requests})

		self.set("bulk_overtime_entries", [])
		left_out = []
		for request in requests:
			key = (request.employee, str(request.overtime_date))
			if key in claimed:
				left_out.append(_("{0} on {1}: already in {2}").format(
					request.employee_name, frappe.format(request.overtime_date, "Date"), claimed[key]
				))
				continue
			if request.employee in blocked:
				left_out.append(_("{0}: already has Overtime Slip {1} in this period").format(
					request.employee_name, blocked[request.employee]
				))
				continue

			row = self.append(
				"bulk_overtime_entries",
				{
					"employee": request.employee,
					"employee_name": request.employee_name,
					"overtime_date": request.overtime_date,
					"requested_hours": request.requested_hours,
					"overtime_type": request.overtime_type,
					"overtime_request": request.request,
				},
			)
			if key in manual:
				row.manual_override = 1
				row.approved_hours = manual[key].approved_hours
				row.override_reason = manual[key].override_reason

		self.refresh_entries()
		self.set_totals()
		self.set_title()

		return {
			"rows": len(self.bulk_overtime_entries),
			"left_out": sorted(set(left_out)),
			"approved_requests": found.requests,
			"days_without_attendance": found.days_without_attendance,
		}

	def _approved_request_rows(self, found=None):
		"""One row per employee per date, from every approved request that
		overlaps this period.

		A request covers a range — a day, a week, a month — and its requested
		hours are *per day*, so a range is expanded one date at a time and
		clipped to this document's own period. A range longer than a day is a
		standing permission rather than a promise about each day, so its dates
		with no attendance at all are dropped; a single-day request keeps its
		row either way, because that day was asked for by name and a missing
		clock-in is what HR needs to see.
		"""
		farm_join = farm_filter = ""
		values = {"company": self.company, "from_date": self.from_date, "to_date": self.to_date}
		if self.custom_farm and "custom_farm" in frappe.db.get_table_columns("Employee"):
			farm_join = "join `tabEmployee` emp on emp.name = row.employee"
			farm_filter = "and emp.custom_farm = %(custom_farm)s"
			values["custom_farm"] = self.custom_farm

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
			order by row.employee_name, req.overtime_date
			""",
			values,
			as_dict=True,
		)

		if found is not None:
			found.requests = len({row.request for row in requests})

		if not requests:
			return []

		# the same lookup refresh_entries uses, so a date is dropped only when
		# it would have shown up as "No Attendance" anyway
		attended = set(self._attendance_by_day({r.employee for r in requests}))
		period_start, period_end = getdate(self.from_date), getdate(self.to_date)

		rows = []
		for request in requests:
			start = max(getdate(request.overtime_date), period_start)
			end = min(getdate(request.request_to_date), period_end)
			is_range = getdate(request.request_to_date) > getdate(request.overtime_date)

			date = start
			while date <= end:
				if is_range and (request.employee, date) not in attended:
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
							requested_hours=request.requested_hours,
						)
					)
				date = add_days(date, 1)

		rows.sort(key=lambda r: (r.employee_name or "", r.overtime_date))
		return rows

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
			(row.employee, str(row.overtime_date)): get_link_to_form("Bulk Overtime", row.name) for row in rows
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
		shift_hours = _shift_lengths({a.shift for a in attendance.values() if a.shift})
		default_hours = flt(frappe.db.get_single_value("HR Settings", "standard_working_hours"))
		caps = _daily_caps({row.overtime_type for row in rows if row.overtime_type})
		day_types = _DayTypes(self.from_date, self.to_date)

		for row in rows:
			date = getdate(row.overtime_date)
			record = attendance.get((row.employee, date))

			row.day_type = day_types.get(row.employee, date)
			row.attendance = record.name if record else None
			row.shift = record.shift if record else None
			row.working_hours = flt(record.working_hours) if record else 0
			row.shift_hours = (shift_hours.get(row.shift) or default_hours) if record else 0

			row.biometric_hours = engine.biometric_overtime(
				row.working_hours,
				row.shift_hours,
				row.day_type,
				maximum_hours=caps.get(row.overtime_type) or 0,
			)
			approved, row.status = engine.settle(
				row.requested_hours,
				row.biometric_hours,
				has_attendance=bool(record),
				has_hours=bool(record and flt(record.working_hours) > 0),
				# a rest day pays every hour worked, so it needs no shift length
				has_shift=bool(row.shift_hours) or row.day_type != engine.WORKING_DAY,
			)

			if row.manual_override:
				if not (row.override_reason or "").strip():
					frappe.throw(_("Row #{0}: give a reason for the manual override.").format(row.idx))
				if flt(row.approved_hours) > flt(row.requested_hours):
					frappe.throw(
						_("Row #{0}: {1} cannot be paid more than the {2} hours requested.").format(
							row.idx, frappe.bold(row.employee_name or row.employee), flt(row.requested_hours)
						)
					)
			else:
				row.approved_hours = approved

	def _attendance_by_day(self, employees) -> dict:
		records = frappe.get_all(
			"Attendance",
			filters={
				"employee": ["in", list(employees)],
				"attendance_date": ["between", [self.from_date, self.to_date]],
				"docstatus": 1,
				"status": "Present",
			},
			fields=["name", "employee", "attendance_date", "working_hours", "shift"],
		)
		return {(r.employee, getdate(r.attendance_date)): r for r in records}

	# ──────────────────────────────────────────────────────────────────────
	# Submit / cancel
	# ──────────────────────────────────────────────────────────────────────

	def on_submit(self):
		self.create_overtime_slips()

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
				_("No Overtime Slips were created. Fix these and submit again:<br>{0}").format("<br>".join(shown)),
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
