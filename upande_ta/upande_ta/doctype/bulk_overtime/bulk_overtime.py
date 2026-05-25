# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate

WORKING_HOURS_PER_MONTH = 199.33


class BulkOvertime(Document):

	def validate(self):
		if self.from_date and self.to_date:
			if frappe.utils.getdate(self.from_date) > frappe.utils.getdate(self.to_date):
				frappe.throw(_("From Date cannot be after To Date."))

		self.validate_overtime_entry_dates()

	def validate_overtime_entry_dates(self):
		if not (self.from_date and self.to_date):
			return

		from_d = getdate(self.from_date)
		to_d = getdate(self.to_date)

		for row in self.bulk_overtime_entries or []:
			if not row.overtime_date:
				continue

			ot_date = getdate(row.overtime_date)
			if ot_date < from_d or ot_date > to_d:
				frappe.throw(
					_(
						"Row {0}: Overtime Date {1} must be between {2} and {3}."
					).format(
						row.idx,
						frappe.format_date(row.overtime_date),
						frappe.format_date(self.from_date),
						frappe.format_date(self.to_date),
					),
					title=_("Date Out of Range"),
				)

	def before_save(self):
		if self.to_date:
			dt = frappe.utils.getdate(self.to_date)
			months = ["January", "February", "March", "April", "May", "June",
					  "July", "August", "September", "October", "November", "December"]
			self.bulk_overtime_title = "%s %s" % (months[dt.month - 1], dt.year)

		for row in self.bulk_overtime_entries:
			if row.overtime_date and not row.overtime_type:
				row.overtime_type = self.get_overtime_type(row.employee, row.overtime_date)

	def on_submit(self):
		self.create_additional_salaries()

	def on_cancel(self):
		self.cancel_additional_salaries()

	@frappe.whitelist()
	def fill_employee_details(self):
		self.bulk_overtime_entries = []

		filters = {"status": "Active"}
		if self.department:
			filters["department"] = self.department
		if self.get("designation"):
			filters["designation"] = self.designation

		employees = frappe.get_all(
			"Employee",
			filters=filters,
			fields=["name", "employee_name", "department"],
			order_by="employee_name asc",
		)

		if not employees:
			frappe.msgprint("No active employees found for the selected filters.")
			return

		# Rebuild the child table without auto-filling overtime dates
		self.set("bulk_overtime_entries", [])

		for emp in employees:
			self.append(
				"bulk_overtime_entries",
				{
					"employee": emp.name,
					"employee_name": emp.employee_name,
					"department": emp.department,
					"overtime_date": None,
					"overtime_type": None,
					"hours_requested": self.default_requested_hours or 0,
					"hours_done": 0,
					"row_status": "Pending",
				},
			)

		self.number_of_employees = len(employees)

	def get_overtime_hours(self, employee_list):
		"""Return a dict keyed by (employee, date_str) -> overtime hours.

		For holidays the full working_hours count as overtime.
		For normal days overtime = working_hours - shift_hours (min 0).
		"""
		if not employee_list:
			return {}

		placeholders = ", ".join(["%s"] * len(employee_list))

		data = frappe.db.sql(
			"""
			SELECT
				a.employee,
				a.attendance_date,
				a.working_hours,
				a.shift,
				COALESCE(
					TIMESTAMPDIFF(MINUTE, st.start_time, st.end_time) / 60.0,
					8
				) AS shift_hours,
				CASE
					WHEN EXISTS (
						SELECT 1 FROM `tabHoliday` h
						INNER JOIN `tabEmployee` e ON e.holiday_list = h.parent
						WHERE e.name = a.employee
						AND h.holiday_date = a.attendance_date
					) THEN 1
					ELSE 0
				END AS is_holiday
			FROM `tabAttendance` a
			LEFT JOIN `tabShift Type` st ON st.name = a.shift
			WHERE a.employee IN ({placeholders})
				AND a.attendance_date BETWEEN %s AND %s
				AND a.status = 'Present'
				AND a.docstatus = 1
			""".format(placeholders=placeholders),
			tuple(employee_list) + (self.from_date, self.to_date),
			as_dict=True,
		)

		result = {}
		for row in data:
			emp = row.employee
			date_key = str(row.attendance_date)
			wh = row.working_hours or 0
			shift_hrs = row.shift_hours or 8

			if row.is_holiday:
				ot = wh
			else:
				ot = max(wh - shift_hrs, 0)

			result[(emp, date_key)] = round(ot, 2)

		return result

	def is_verification_period_open(self):
		"""Allow verification only after the overtime period has ended."""
		if not self.to_date:
			return False
		return getdate() > getdate(self.to_date)

	def get_verification_available_from(self):
		return add_days(getdate(self.to_date), 1)

	def validate_verification_period(self):
		if self.is_verification_period_open():
			return
		frappe.throw(
			_(
				"Overtime verification is only available from {0} onwards (after the overtime period ending {1})."
			).format(
				frappe.format_date(self.get_verification_available_from()),
				frappe.format_date(self.to_date),
			),
			title=_("Verification Not Available"),
		)

	@frappe.whitelist()
	def sync_attendance_data(self):
		"""Refresh hours_done on every child row from Attendance records."""
		self.validate_verification_period()
		entries = self.bulk_overtime_entries or []
		if not entries:
			frappe.msgprint("No overtime entries to sync. Please fetch employees first.")
			return {
				"updated_rows": []
			}

		emp_ids = list({row.employee for row in entries if row.employee})
		hours_map = self.get_overtime_hours(emp_ids)

		synced = 0
		updated_rows = []
		for idx, row in enumerate(entries):
			if not row.employee or not row.overtime_date:
				continue

			date_key = str(getdate(row.overtime_date))
			hours = flt(hours_map.get((row.employee, date_key), 0), 1)
			row.hours_done = hours

			# Auto-correct overtime type based on the date
			row.overtime_type = self.get_overtime_type(row.employee, row.overtime_date)
			synced += 1
			updated_rows.append({
				"idx": idx,
				"hours_done": row.hours_done,
				"overtime_type": row.overtime_type,
			})

		frappe.msgprint(
			"%s entries synced with Attendance data." % synced,
			indicator="green",
		)

		return {
			"updated_rows": updated_rows
		}

	
	@frappe.whitelist()
	def get_overtime_type(self, employee, overtime_date):
		if not employee or not overtime_date:
			return None

		ot_date = getdate(overtime_date)
		if ot_date.weekday() >= 5:
			return "Holiday Overtime"

		holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
		if holiday_list and frappe.db.exists(
			"Holiday",
			{"parent": holiday_list, "holiday_date": ot_date},
		):
			return "Holiday Overtime"

		return "Normal Overtime"

	def create_additional_salaries(self):
		created = 0
		errors = []

		for row in self.bulk_overtime_entries:
			if row.row_status == "Rejected":
				continue

			# Use actual hours where available, otherwise fall back to requested hours.
			hours = flt(row.hours_done or row.hours_requested or 0)
			if hours <= 0:
				continue

			if not row.overtime_date:
				errors.append(
					_("{0} ({1}): Overtime Date is required to create Additional Salary").format(
						row.employee_name, row.employee
					)
				)
				continue

			if not row.overtime_type:
				row.overtime_type = "Normal Overtime"

			ssa = frappe.db.get_value(
				"Salary Structure Assignment",
				filters={"employee": row.employee, "docstatus": 1},
				fieldname=["base"],
				order_by="from_date desc",
				as_dict=True,
			)

			if not ssa or not ssa.base or ssa.base <= 0:
				errors.append("%s (%s): No active Salary Structure Assignment or base is 0" % (
					row.employee_name, row.employee
				))
				continue

			hourly_rate = ssa.base / WORKING_HOURS_PER_MONTH

			if "Holiday" in row.overtime_type:
				amount = round(hourly_rate * 2.0 * hours, 2)
				self.make_additional_salary(
					row.employee, "Overtime 2.0", amount, row.name, row.overtime_date
				)
			else:
				amount = round(hourly_rate * 1.5 * hours, 2)
				self.make_additional_salary(
					row.employee, "Overtime 1.5", amount, row.name, row.overtime_date
				)
			created += 1

		if errors:
			frappe.msgprint(
				"Could not create Additional Salary for the following employees:<br>"
				+ "<br>".join(errors),
				title="Warnings",
				indicator="orange",
			)

		if created:
			frappe.msgprint(
				"%s Additional Salary record(s) created." % created,
				indicator="green",
			)

	def make_additional_salary(self, employee, salary_component, amount, ref_row, payroll_date):
		ad = frappe.get_doc({
			"doctype": "Additional Salary",
			"employee": employee,
			"company": frappe.defaults.get_global_default("company"),
			"salary_component": salary_component,
			"amount": amount,
			"payroll_date": payroll_date,
			"ref_doctype": "Bulk Overtime",
			"ref_docname": self.name,
			"overwrite_salary_structure_amount": 0,
		})
		ad.insert(ignore_permissions=True)
		ad.submit()

	def cancel_additional_salaries(self):
		existing = frappe.get_all(
			"Additional Salary",
			filters={"ref_doctype": "Bulk Overtime", "ref_docname": self.name, "docstatus": 1},
			fields=["name"],
		)

		for row in existing:
			ad = frappe.get_doc("Additional Salary", row.name)
			ad.cancel()

		if existing:
			frappe.msgprint(
				"%s Additional Salary record(s) cancelled." % len(existing),
				indicator="orange",
			)

