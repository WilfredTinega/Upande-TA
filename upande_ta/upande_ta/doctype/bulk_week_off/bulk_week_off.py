# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class BulkWeekOff(Document):

    def validate(self):
        if not self.employees:
            frappe.throw(_("Please add employees before saving."))
        self.check_duplicate_employees()
        self.validate_from_date()
        self.validate_assigned_off_days()

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

    def validate_assigned_off_days(self):
        for row in self.employees:
            if not row.assigned_off_day:
                frappe.throw(
                    _("Row {0}: Assigned Off Day is required for employee {1}.").format(
                        row.idx, frappe.bold(row.employee)
                    )
                )
            if row.assigned_off_day == row.current_holiday_list:
                frappe.throw(
                    _("Row {0}: Assigned Off Day is the same as the current one for employee {1}. Remove the row or pick a different Holiday List.").format(
                        row.idx, frappe.bold(row.employee)
                    )
                )

    def on_submit(self):
        self.create_employee_transfers()

    def on_cancel(self):
        self.cancel_employee_transfers()

    def create_employee_transfers(self):
        created = 0
        submitted = 0
        deferred = []
        failed = []
        today = getdate()
        transfer_date = getdate(self.from_date)

        for row in self.employees:
            try:
                transfer = frappe.get_doc({
                    "doctype": "Employee Transfer",
                    "employee": row.employee,
                    "transfer_date": self.from_date,
                    "transfer_details": [
                        {
                            "property": _("Holiday List"),
                            "fieldname": "holiday_list",
                            "current": row.current_holiday_list or "",
                            "new": row.assigned_off_day,
                        }
                    ],
                })
                transfer.insert(ignore_permissions=True)
                created += 1
                row.db_set("employee_transfer", transfer.name, update_modified=False)

                # HRMS blocks submit when transfer_date > today (before_submit).
                # Submit immediately when allowed; otherwise leave as Draft.
                # The scheduled job submit_due_employee_transfers picks up
                # drafts on/after their transfer_date.
                if transfer_date <= today:
                    transfer.submit()
                    submitted += 1
                else:
                    deferred.append(row.employee)
            except Exception:
                failed.append(row.employee)
                frappe.log_error(
                    title="Bulk Week Off: Employee Transfer creation failed",
                    message=frappe.get_traceback(),
                )

        if created:
            frappe.msgprint(
                _("Created {0} Employee Transfer(s); submitted {1}.").format(
                    created, submitted
                ),
                alert=True,
                indicator="green",
            )
        if deferred:
            frappe.msgprint(
                _("Left as Draft because the transfer date is in the future: {0}").format(
                    ", ".join(deferred)
                ),
                indicator="orange",
            )
        if failed:
            frappe.msgprint(
                _("Failed to create Employee Transfer for: {0}").format(
                    ", ".join(failed)
                ),
                indicator="red",
            )

    def cancel_employee_transfers(self):
        cancelled = 0
        skipped = []
        for row in self.employees:
            transfer_name = getattr(row, "employee_transfer", None)
            if transfer_name and frappe.db.exists("Employee Transfer", transfer_name):
                transfer_names = [transfer_name]
            else:
                # Fallback for rows created before the link field existed.
                transfer_names = frappe.get_all(
                    "Employee Transfer",
                    filters={
                        "employee": row.employee,
                        "transfer_date": self.from_date,
                        "docstatus": ["<", 2],
                    },
                    pluck="name",
                )

            for name in transfer_names:
                try:
                    transfer = frappe.get_doc("Employee Transfer", name)
                    if transfer.docstatus == 1:
                        transfer.cancel()
                        cancelled += 1
                    elif transfer.docstatus == 0:
                        transfer.delete(ignore_permissions=True)
                        cancelled += 1
                except Exception:
                    skipped.append(name)
                    frappe.log_error(
                        title="Bulk Week Off: Employee Transfer cancel failed",
                        message=frappe.get_traceback(),
                    )

        if cancelled:
            frappe.msgprint(
                _("Cancelled/removed {0} Employee Transfer(s).").format(cancelled),
                alert=True,
                indicator="orange",
            )
        if skipped:
            frappe.msgprint(
                _("Could not cancel: {0}").format(", ".join(skipped)),
                indicator="red",
            )

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
        if self.designation:
            filters.append(["Employee", "designation", "=", self.designation])

        employees = frappe.get_all(
            "Employee",
            filters=filters,
            fields=[
                "name as employee",
                "employee_name",
                "department",
                "designation",
                "holiday_list as current_holiday_list",
            ],
            order_by="employee_name asc",
        )

        return employees


def submit_due_employee_transfers():
    """Scheduled daily: submit Bulk Week Off-originated Employee Transfer drafts
    whose transfer_date has arrived. HRMS blocks submit when transfer_date > today,
    so future-dated rows are inserted as Draft and picked up here on/after their date.
    """
    today = getdate()
    rows = frappe.db.sql(
        """
        SELECT d.employee_transfer, d.parent
        FROM `tabBulk Week Off Detail` d
        JOIN `tabBulk Week Off` p ON p.name = d.parent
        JOIN `tabEmployee Transfer` et ON et.name = d.employee_transfer
        WHERE p.docstatus = 1
          AND d.employee_transfer IS NOT NULL
          AND d.employee_transfer != ''
          AND et.docstatus = 0
          AND et.transfer_date <= %s
        """,
        (today,),
        as_dict=True,
    )

    submitted = 0
    failed = 0
    for r in rows:
        try:
            transfer = frappe.get_doc("Employee Transfer", r.employee_transfer)
            transfer.submit()
            submitted += 1
        except Exception:
            failed += 1
            frappe.log_error(
                title="Bulk Week Off: scheduled Employee Transfer submit failed",
                message="Transfer: %s\nParent: %s\n%s"
                % (r.employee_transfer, r.parent, frappe.get_traceback()),
            )

    if submitted or failed:
        frappe.logger().info(
            "submit_due_employee_transfers: submitted=%d failed=%d"
            % (submitted, failed)
        )
    return {"submitted": submitted, "failed": failed}
