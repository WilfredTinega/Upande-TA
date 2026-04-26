# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, date_diff, getdate

WORKING_HOURS_PER_MONTH = 199.33


class BulkOvertime(Document):

	def validate(self):
		if self.from_date and self.to_date:
			if frappe.utils.getdate(self.from_date) > frappe.utils.getdate(self.to_date):
				frappe.throw("From Date cannot be after To Date.")

			existing = frappe.db.get_value("Bulk Overtime", filters={
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

	def before_save(self):
		if self.to_date:
			dt = frappe.utils.getdate(self.to_date)
			months = ["January", "February", "March", "April", "May", "June",
					  "July", "August", "September", "October", "November", "December"]
			self.bulk_overtime_title = "%s %s" % (months[dt.month - 1], dt.year)

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
		if self.get("branch"):
			filters["branch"] = self.branch
		if self.get("designation"):
			filters["designation"] = self.designation
		if self.get("grade"):
			filters["grade"] = self.grade

		employees = frappe.get_all(
			"Employee",
			filters=filters,
			fields=["name", "employee_name", "department"],
			order_by="employee_name asc",
		)

		if not employees:
			frappe.msgprint("No active employees found for the selected filters.")
			return

		emp_ids = [e.name for e in employees]
		hours_map = self.get_overtime_hours(emp_ids)

		# Rebuild the child table
		self.set("bulk_overtime_entries", [])
		from_date = getdate(self.from_date)
		total_days = date_diff(self.to_date, self.from_date) + 1

		for emp in employees:
			for day_offset in range(total_days):
				overtime_date = add_days(from_date, day_offset)
				self.append(
					"bulk_overtime_entries",
					{
						"employee": emp.name,
						"employee_name": emp.employee_name,
						"department": emp.department,
						"overtime_date": overtime_date,
						"overtime_type": None,
						"hours_requested": self.default_requested_hours or 0,
						"row_status": "Pending",
					},
				)

		self.number_of_employees = len(self.bulk_overtime_entries)

	def get_overtime_hours(self, employee_list):
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
			wh = row.working_hours or 0
			shift_hrs = row.shift_hours or 8

			if row.is_holiday:
				ot = wh
			else:
				ot = max(wh - shift_hrs, 0)

			if emp not in result:
				result[emp] = {"normal": 0, "holiday": 0}

			if row.is_holiday:
				result[emp]["holiday"] = result[emp]["holiday"] + ot
			else:
				result[emp]["normal"] = result[emp]["normal"] + ot

		return {
			emp: {
				"normal": round(vals["normal"], 2),
				"holiday": round(vals["holiday"], 2),
			}
			for emp, vals in result.items()
		}

	def create_additional_salaries(self):
		created = 0
		errors = []

		for row in self.bulk_overtime_entries:
			if row.row_status == "Rejected":
				continue

			if row.normal_hours <= 0 and row.holiday_hours <= 0:
				continue

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

			if row.normal_hours > 0:
				normal_amount = round(hourly_rate * 1.5 * float(row.normal_hours), 2)
				self.make_additional_salary(
					row.employee, "Overtime 1.5", normal_amount, row.name
				)
				created = created + 1

			if row.holiday_hours > 0:
				holiday_amount = round(hourly_rate * 2.0 * float(row.holiday_hours), 2)
				self.make_additional_salary(
					row.employee, "Overtime 2.0", holiday_amount, row.name
				)
				created = created + 1

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

	def make_additional_salary(self, employee, salary_component, amount, ref_row):
		ad = frappe.get_doc({
			"doctype": "Additional Salary",
			"employee": employee,
			"company": frappe.defaults.get_global_default("company"),
			"salary_component": salary_component,
			"amount": amount,
			"payroll_date": self.to_date,
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
