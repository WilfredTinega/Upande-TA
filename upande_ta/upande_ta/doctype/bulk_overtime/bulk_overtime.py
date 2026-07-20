# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import flt

WORKING_HOURS_PER_MONTH = 199.33

# Overtime Type -> (multiplier, salary component) used when generating Overtime Slips.
NORMAL_OT_TYPE = "Overtime 1.5"
HOLIDAY_OT_TYPE = "Overtime 2.0"
OT_TYPES = {
	NORMAL_OT_TYPE: {"multiplier": 1.5, "component": "Overtime 1.5"},
	HOLIDAY_OT_TYPE: {"multiplier": 2.0, "component": "Overtime 2.0"},
}


class BulkOvertime(Document):

	def validate(self):
		if self.from_date and self.to_date:
			if frappe.utils.getdate(self.from_date) > frappe.utils.getdate(self.to_date):
				frappe.throw("From Date cannot be after To Date.")

			if frappe.utils.getdate(self.to_date) > frappe.utils.getdate(frappe.utils.today()):
				frappe.throw("To Date cannot be a future date. Today is <b>%s</b>." % frappe.utils.today())

			existing = frappe.db.get_value("Bulk Overtime", filters={
				"company": self.company,
				"name": ("!=", self.name or ""),
				"docstatus": ("!=", 2),
				"from_date": ("<=", self.to_date),
				"to_date": (">=", self.from_date),
			}, fieldname=["name", "bulk_overtime_title", "from_date", "to_date"], as_dict=True)

			if existing:
				new_from = frappe.utils.getdate(self.from_date)
				ex_from = frappe.utils.getdate(existing.from_date)

				if new_from < ex_from:
					before_date = frappe.utils.add_days(existing.from_date, -1)
					frappe.throw(
						"Dates overlap with <a href='/app/bulk-overtime/%s'>%s</a> "
						"(%s to %s). To Date must be on or before <b>%s</b>." % (
							existing.name,
							existing.bulk_overtime_title or existing.name,
							existing.from_date,
							existing.to_date,
							before_date,
						)
					)
				else:
					after_date = frappe.utils.add_days(existing.to_date, 1)
					frappe.throw(
						"Dates overlap with <a href='/app/bulk-overtime/%s'>%s</a> "
						"(%s to %s). From Date must be on or after <b>%s</b>." % (
							existing.name,
							existing.bulk_overtime_title or existing.name,
							existing.from_date,
							existing.to_date,
							after_date,
						)
					)

		# Manual rows must carry hours; biometric rows are validated from attendance.
		for row in self.bulk_overtime_entries:
			if (row.verification_type or "Biometric") == "Manual":
				if flt(row.normal_hours) <= 0 and flt(row.holiday_hours) <= 0:
					frappe.throw(
						"Row #%s (%s): Manual entries must have Normal or Holiday hours." % (
							row.idx, row.employee_name or row.employee
						)
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
		# Re-validate biometric rows against attendance across the whole period so the
		# figures are current at submission; manual rows are left exactly as entered.
		if self.get("auto_validate_worked_hours"):
			self.revalidate_biometric_hours()
		self.create_overtime_slips()

	def on_cancel(self):
		self.cancel_overtime_slips()

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

	def revalidate_biometric_hours(self):
		"""Recompute Normal/Holiday hours from attendance for Biometric rows only."""
		bio_rows = [
			r for r in self.bulk_overtime_entries
			if (r.verification_type or "Biometric") != "Manual"
		]
		if not bio_rows:
			return
		hours_map = self.get_overtime_hours([r.employee for r in bio_rows])
		for row in bio_rows:
			ot = hours_map.get(row.employee, {"normal": 0, "holiday": 0})
			row.normal_hours = ot["normal"]
			row.holiday_hours = ot["holiday"]

	def get_overtime_hours(self, employee_list):
		"""Overtime hours per employee across the whole from_date..to_date range.

		Rest-day work counts fully as holiday-rate overtime: if the day is a weekly
		off, a public holiday, or an approved leave day for the employee AND they have
		Present attendance, the entire working_hours is holiday overtime. On an
		ordinary working day, overtime is the hours worked beyond the shift length."""
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
					MOD(TIMESTAMPDIFF(MINUTE, st.start_time, st.end_time) + 1440, 1440) / 60.0,
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
				END AS is_holiday,
				CASE
					WHEN EXISTS (
						SELECT 1 FROM `tabLeave Application` la
						WHERE la.employee = a.employee
						AND la.status = 'Approved'
						AND la.docstatus = 1
						AND a.attendance_date BETWEEN la.from_date AND la.to_date
					) THEN 1
					ELSE 0
				END AS is_leave
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
			# Guard against bad/negative working_hours in attendance data.
			wh = max(row.working_hours or 0, 0)
			shift_hrs = row.shift_hours if row.shift_hours and row.shift_hours > 0 else 8

			# Worked on a rest day (weekly off / public holiday / approved leave) -> all
			# hours are overtime at the holiday rate; otherwise only hours beyond shift.
			is_rest_day = row.is_holiday or row.is_leave

			result[(emp, date_key)] = round(ot, 2)

			if is_rest_day:
				result[emp]["holiday"] = result[emp]["holiday"] + wh
			else:
				result[emp]["normal"] = result[emp]["normal"] + max(wh - shift_hrs, 0)

	@frappe.whitelist()
	def sync_attendance_data(self):
		"""Refresh hours_done on every child row from Attendance records."""
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

	def create_overtime_slips(self):
		"""Create + submit one Overtime Slip per eligible employee. Each slip creates
		the Additional Salary (see the Overtime Slip override)."""
		ensure_overtime_setup()

		created = 0
		errors = []

		for row in self.bulk_overtime_entries:
			if row.row_status == "Rejected":
				continue

			normal_hours = flt(row.normal_hours)
			holiday_hours = flt(row.holiday_hours)
			if normal_hours <= 0 and holiday_hours <= 0:
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

			slip = frappe.get_doc({
				"doctype": "Overtime Slip",
				"employee": row.employee,
				"company": self.company,
				"posting_date": frappe.utils.today(),
				"start_date": self.from_date,
				"end_date": self.to_date,
				"custom_bulk_overtime": self.name,
			})

			details = []
			if normal_hours > 0:
				details.append((NORMAL_OT_TYPE, normal_hours))
			if holiday_hours > 0:
				details.append((HOLIDAY_OT_TYPE, holiday_hours))

			for ot_type, hrs in details:
				cfg = OT_TYPES[ot_type]
				slip.append("overtime_details", {
					"date": self.to_date,
					"overtime_type": ot_type,
					"overtime_duration": hrs,
					"standard_working_hours": 8,
					"custom_salary_component": cfg["component"],
					"custom_amount": round(hourly_rate * cfg["multiplier"] * float(hrs), 2),
				})

			slip.insert(ignore_permissions=True)
			slip.submit()
			created = created + 1

		if errors:
			frappe.msgprint(
				"Could not create Overtime Slips for the following employees:<br>"
				+ "<br>".join(errors),
				title="Warnings",
				indicator="orange",
			)

		if created:
			frappe.msgprint(
				"%s Overtime Slip(s) created." % created,
				indicator="green",
			)

	def cancel_overtime_slips(self):
		cancelled = 0

		# New flow: cancel linked Overtime Slips (each cancels its Additional Salary).
		# Guard on the real column so cancellation never breaks on a site where the
		# custom field/column was not created (e.g. submitted before this feature).
		if "custom_bulk_overtime" in frappe.db.get_table_columns("Overtime Slip"):
			for name in frappe.get_all(
				"Overtime Slip",
				filters={"custom_bulk_overtime": self.name, "docstatus": 1},
				pluck="name",
			):
				frappe.get_doc("Overtime Slip", name).cancel()
				cancelled += 1

		# Legacy flow: Additional Salary created directly by older submissions.
		for name in frappe.get_all(
			"Additional Salary",
			filters={"ref_doctype": "Bulk Overtime", "ref_docname": self.name, "docstatus": 1},
			pluck="name",
		):
			frappe.get_doc("Additional Salary", name).cancel()
			cancelled += 1

		if cancelled:
			frappe.msgprint(
				"%s linked record(s) cancelled." % cancelled,
				indicator="orange",
			)


def ensure_overtime_setup():
	"""Idempotently create the custom fields and Overtime Types that link
	Bulk Overtime -> Overtime Slip -> Additional Salary."""
	# The overtime feature depends on hrms doctypes (Overtime Details / Slip /
	# Type) that are absent on this bench — their sources were lost alongside the
	# Holiday List Assignment backport. Skip setup instead of failing the whole
	# migrate; this becomes a no-op until those doctypes are restored.
	required_doctypes = ("Overtime Details", "Overtime Slip", "Overtime Type")
	missing = [dt for dt in required_doctypes if not frappe.db.exists("DocType", dt)]
	if missing:
		frappe.logger().info(
			"ensure_overtime_setup skipped; missing hrms doctypes: %s" % ", ".join(missing)
		)
		return

	create_custom_fields(
		{
			"Overtime Details": [
				{
					"fieldname": "custom_salary_component",
					"label": "Salary Component (Bulk OT)",
					"fieldtype": "Link",
					"options": "Salary Component",
					"read_only": 1,
					"insert_after": "standard_working_hours",
				},
				{
					"fieldname": "custom_amount",
					"label": "Amount (Bulk OT)",
					"fieldtype": "Currency",
					"read_only": 1,
					"insert_after": "custom_salary_component",
				},
			],
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

	for ot_type, cfg in OT_TYPES.items():
		if frappe.db.exists("Overtime Type", ot_type):
			continue
		if not frappe.db.exists("Salary Component", cfg["component"]):
			continue
		doc = frappe.new_doc("Overtime Type")
		doc.name = ot_type
		doc.overtime_salary_component = cfg["component"]
		doc.standard_multiplier = cfg["multiplier"]
		doc.applicable_for_weekend = 0
		doc.overtime_calculation_method = "Fixed Hourly Rate"
		doc.hourly_rate = 0
		doc.insert(ignore_permissions=True)
