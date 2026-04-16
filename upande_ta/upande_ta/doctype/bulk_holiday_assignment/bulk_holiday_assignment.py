# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class BulkHolidayAssignment(Document):

    def validate(self):
        if not self.employees:
            frappe.throw(_("Please add employees before saving."))
        self.check_duplicate_employees()
        self.validate_from_date()

    def check_duplicate_employees(self):
        employees = []
        for row in self.employees:
            if row.employee in employees:
                frappe.throw(
                    _("Employee {0} is added more than once.").format(
                        frappe.bold(row.employee)
                    )
                )
            employees.append(row.employee)

    def validate_from_date(self):
        if not self.from_date or not self.holiday_list:
            return
        holiday_list_start, holiday_list_end = frappe.db.get_value(
            "Holiday List", self.holiday_list, ["from_date", "to_date"]
        )
        from_date = getdate(self.from_date)
        if (from_date < holiday_list_start) or (from_date > holiday_list_end):
            frappe.throw(
                _("Assignment start date {0} is outside the Holiday List period ({1} to {2}).").format(
                    frappe.bold(str(self.from_date)),
                    frappe.bold(str(holiday_list_start)),
                    frappe.bold(str(holiday_list_end)),
                )
            )

    def on_submit(self):
        self.assign_holiday_list()

    def on_cancel(self):
        self.remove_holiday_list()

    def assign_holiday_list(self):
        success = 0
        failed = []
        has_hla = frappe.db.exists("DocType", "Holiday List Assignment")

        for row in self.employees:
            try:
                frappe.db.set_value(
                    "Employee", row.employee, "holiday_list", self.holiday_list,
                    update_modified=False
                )

                if has_hla:
                    existing = frappe.db.exists("Holiday List Assignment", {
                        "assigned_to": row.employee,
                        "from_date": self.from_date,
                        "docstatus": 1
                    })
                    if not existing:
                        hla = frappe.get_doc({
                            "doctype": "Holiday List Assignment",
                            "applicable_for": "Employee",
                            "assigned_to": row.employee,
                            "holiday_list": self.holiday_list,
                            "from_date": self.from_date,
                        })
                        hla.insert(ignore_permissions=True)
                        hla.submit()

                success = success + 1
            except Exception:
                failed.append(row.employee)
                frappe.log_error(
                    title="Bulk Holiday Assignment Error",
                    message="Failed to assign holiday list %s to employee %s"
                    % (self.holiday_list, row.employee),
                )

        if success:
            msg = _("Holiday List {0} assigned to {1} employee(s).").format(
                frappe.bold(self.holiday_list), success
            )
            if has_hla:
                msg = msg + " " + _("Holiday List Assignment records created.")
            frappe.msgprint(msg, alert=True, indicator="green")

        if failed:
            frappe.msgprint(
                _("Failed to assign holiday list to: {0}").format(
                    ", ".join(failed)
                ),
                indicator="red",
            )

    def remove_holiday_list(self):
        success = 0
        has_hla = frappe.db.exists("DocType", "Holiday List Assignment")

        for row in self.employees:
            current = frappe.db.get_value(
                "Employee", row.employee, "holiday_list"
            )
            if current == self.holiday_list:
                frappe.db.set_value(
                    "Employee", row.employee, "holiday_list", "",
                    update_modified=False
                )

            if has_hla:
                hla_name = frappe.db.exists("Holiday List Assignment", {
                    "assigned_to": row.employee,
                    "holiday_list": self.holiday_list,
                    "from_date": self.from_date,
                    "docstatus": 1
                })
                if hla_name:
                    hla = frappe.get_doc("Holiday List Assignment", hla_name)
                    hla.cancel()

            success = success + 1

        if success:
            msg = _("Holiday List removed from {0} employee(s).").format(success)
            if has_hla:
                msg = msg + " " + _("Holiday List Assignment records cancelled.")
            frappe.msgprint(msg, alert=True, indicator="orange")

    @frappe.whitelist()
    def get_employees(self):
        filters = [
            ["Employee", "status", "=", "Active"],
            ["Employee", "company", "=", self.company],
        ]

        if self.employee:
            filters.append(["Employee", "name", "=", self.employee])
        if self.department:
            filters.append(["Employee", "department", "=", self.department])
        if self.branch:
            filters.append(["Employee", "branch", "=", self.branch])
        if self.designation:
            filters.append(["Employee", "designation", "=", self.designation])
        if self.employment_type:
            filters.append(
                ["Employee", "employment_type", "=", self.employment_type]
            )

        employees = frappe.get_all(
            "Employee",
            filters=filters,
            fields=[
                "name as employee",
                "employee_name",
                "department",
                "branch",
                "designation",
                "holiday_list as current_holiday_list",
            ],
            order_by="employee_name asc",
        )

        return employees