# Copyright (c) 2026, Upande LTD and contributors

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate


def create_linked_holiday_list_assignment(
    employee, current_holiday_list, new_holiday_list, from_date
):
    """Create and submit a Holiday List Assignment putting ``employee`` on
    ``new_holiday_list`` effective ``from_date``.

    A Holiday List Assignment is HRMS' date-effective record of which Holiday
    List an employee uses on a given date. The Monthly Attendance Sheet resolves
    weekly offs per date from these assignments, so creating one from the
    transfer date is what makes the new weekly off show up going forward while
    the previous one stays visible for earlier dates (the "change log").

    We also backfill a prior assignment for ``current_holiday_list`` when the
    employee has none covering the day before ``from_date`` — otherwise the
    resolver would fall back to the company/default list for pre-transfer dates
    and the old weekly off would not render.

    Returns the new (forward) Holiday List Assignment name, or None on failure
    (which is logged, not raised — a missing HLA must not fail the transfer).
    """
    from upande_ta.upande_ta.doctype.holiday_list_assignment.holiday_list_assignment import (
        DuplicateAssignment,
    )
    from upande_ta.upande_ta.holiday_list import get_assigned_holiday_list

    if not (employee and new_holiday_list):
        return None

    from_date = getdate(from_date)

    if current_holiday_list and current_holiday_list != new_holiday_list:
        try:
            existing = get_assigned_holiday_list(
                employee, as_on=add_days(from_date, -1)
            )
            if existing != current_holiday_list:
                cur_start = getdate(
                    frappe.db.get_value(
                        "Holiday List", current_holiday_list, "from_date"
                    )
                )
                if cur_start and cur_start < from_date:
                    prior = frappe.get_doc({
                        "doctype": "Holiday List Assignment",
                        "applicable_for": "Employee",
                        "assigned_to": employee,
                        "holiday_list": current_holiday_list,
                        "from_date": cur_start,
                    })
                    prior.insert(ignore_permissions=True)
                    prior.submit()
        except DuplicateAssignment:
            frappe.clear_last_message()
        except Exception:
            frappe.log_error(
                title="Bulk Week Off: prior Holiday List Assignment backfill failed",
                message=frappe.get_traceback(),
            )

    try:
        hla = frappe.get_doc({
            "doctype": "Holiday List Assignment",
            "applicable_for": "Employee",
            "assigned_to": employee,
            "holiday_list": new_holiday_list,
            "from_date": from_date,
        })
        hla.insert(ignore_permissions=True)
        hla.submit()
        return hla.name
    except DuplicateAssignment:
        frappe.clear_last_message()
        return frappe.db.exists(
            "Holiday List Assignment",
            {
                "assigned_to": employee,
                "from_date": from_date,
                "docstatus": 1,
            },
        )
    except Exception:
        frappe.log_error(
            title="Bulk Week Off: Holiday List Assignment creation failed",
            message=frappe.get_traceback(),
        )
        return None


def cancel_absents_on_effective_offdays(employee, since=None):
    """Cancel submitted ``Absent`` Attendance on any date that the employee's
    *date-effective* Holiday List Assignment marks as a holiday / weekly off —
    covering BOTH the historical (previous week off) and the new period.

    Reassigning a week off is otherwise not retroactive: auto-attendance had
    already marked those days Absent under whatever schedule was in force when
    it ran, and it never revisits a date that already has an Attendance record —
    so the stale Absent persists on what is now a rest day and still feeds the
    Monthly Attendance Sheet and payroll. Because Bulk Week Off also backfills
    the *prior* assignment, resolving the list in force on each Absent's own date
    lets us clean up the previous week-off days as well as the new ones.

    For each Absent we look up ``get_assigned_holiday_list(as_on=date)`` and drop
    the record when that date is an off/holiday there (``is_holiday``). Genuine
    absences on real working days are never touched. ``since`` bounds the scan
    (defaults to the employee's earliest assignment). Idempotent; failures are
    logged, never raised — clean-up must not fail the transfer. Returns count.
    """
    if not employee:
        return 0

    from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday
    from upande_ta.upande_ta.holiday_list import get_assigned_holiday_list

    today = getdate()
    if since is None:
        since = frappe.db.get_value(
            "Holiday List Assignment",
            {"assigned_to": employee, "applicable_for": "Employee", "docstatus": 1},
            "from_date",
            order_by="from_date asc",
        )
    since = getdate(since) if since else None

    cancelled = 0
    try:
        filters = {"employee": employee, "status": "Absent", "docstatus": 1}
        filters["attendance_date"] = (
            ["between", [since, today]] if since else ["<=", today]
        )
        att_rows = frappe.get_all(
            "Attendance", filters=filters, fields=["name", "attendance_date"]
        )
        for a in att_rows:
            try:
                eff = get_assigned_holiday_list(employee, as_on=a.attendance_date)
            except Exception:
                eff = None
            if not eff or not is_holiday(eff, a.attendance_date):
                continue
            try:
                doc = frappe.get_doc("Attendance", a.name)
                doc.flags.ignore_permissions = True
                doc.cancel()
                cancelled += 1
            except Exception:
                frappe.log_error(
                    title="Bulk Week Off: stale Absent cancel failed",
                    message="Attendance: %s\n%s" % (a.name, frappe.get_traceback()),
                )
    except Exception:
        frappe.log_error(
            title="Bulk Week Off: cancel_absents_on_effective_offdays failed",
            message=frappe.get_traceback(),
        )
    return cancelled


class BulkWeekOff(Document):

    @property
    def holiday_list_start(self):
        # Week Off Start tracks the Scheduled Transfer Date: the new week off
        # takes effect from the transfer date, so the assignment starts there —
        # not from the holiday list's own from_date.
        return self.from_date

    @property
    def holiday_list_end(self):
        return (
            frappe.get_value("Holiday List", self.holiday_list, "to_date")
            if self.holiday_list
            else None
        )

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
        from_date = getdate(self.from_date) if self.from_date else None
        for row in self.employees:
            if not row.assigned_off_day:
                frappe.throw(
                    _("Row {0}: Assigned Off Day is required for employee {1}.").format(
                        row.idx, frappe.bold(row.employee)
                    )
                )
            if row.assigned_off_day == row.current_holiday_list:
                continue
            if from_date:
                start, end = frappe.db.get_value(
                    "Holiday List", row.assigned_off_day, ["from_date", "to_date"]
                )
                if start and end and (from_date < getdate(start) or from_date > getdate(end)):
                    frappe.throw(
                        _("Row {0}: Transfer date {1} is outside the Assigned Off Day period ({2} to {3}) for employee {4}.").format(
                            row.idx,
                            frappe.bold(str(self.from_date)),
                            frappe.bold(str(start)),
                            frappe.bold(str(end)),
                            frappe.bold(row.employee),
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
        skipped = []
        absents_fixed = 0
        today = getdate()
        transfer_date = getdate(self.from_date)

        for row in self.employees:
            current = frappe.db.get_value("Employee", row.employee, "holiday_list") or ""

            if row.assigned_off_day == current:
                skipped.append(row.employee)
                continue
            try:
                transfer = frappe.get_doc({
                    "doctype": "Employee Transfer",
                    "employee": row.employee,
                    "transfer_date": self.from_date,
                    "transfer_details": [
                        {
                            "property": _("Holiday List"),
                            "fieldname": "holiday_list",
                            "current": current,
                            "new": row.assigned_off_day,
                        }
                    ],
                })
                transfer.insert(ignore_permissions=True)
                created += 1
                row.db_set("employee_transfer", transfer.name, update_modified=False)

                hla_name = create_linked_holiday_list_assignment(
                    row.employee,
                    current,
                    row.assigned_off_day,
                    self.from_date,
                )
                if hla_name:
                    row.db_set(
                        "holiday_list_assignment",
                        hla_name,
                        update_modified=False,
                    )
                    # Self-repair: drop any stale Absent that now falls on a
                    # rest day under the (historical or new) assignment.
                    absents_fixed += cancel_absents_on_effective_offdays(row.employee)

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
        if absents_fixed:
            frappe.msgprint(
                _("Cancelled {0} stale Absent record(s) that now fall on a rest day.").format(
                    absents_fixed
                ),
                alert=True,
                indicator="green",
            )
        if deferred:
            frappe.msgprint(
                _("Holiday List Assignment created now; Employee Transfer left as Draft and will auto-submit on its start date for: {0}").format(
                    ", ".join(deferred)
                ),
                indicator="orange",
            )
        if skipped:
            frappe.msgprint(
                _("Skipped {0} employee(s) already on the target off day: {1}").format(
                    len(skipped), ", ".join(skipped)
                ),
                indicator="blue",
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
            self._revert_holiday_list_assignment(row, skipped)

            transfer_name = getattr(row, "employee_transfer", None)
            if transfer_name and frappe.db.exists("Employee Transfer", transfer_name):
                transfer_names = [transfer_name]
            else:
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
                    revert_to = self._transfer_prior_holiday_list(transfer)
                    was_applied = transfer.docstatus == 1

                    if transfer.docstatus == 1:
                        transfer.flags.ignore_links = True
                        transfer.cancel()
                        cancelled += 1
                    elif transfer.docstatus == 0:
                        transfer.delete(ignore_permissions=True, force=True)
                        cancelled += 1
                    row.db_set("employee_transfer", None, update_modified=False)

                    if was_applied and revert_to is not None:
                        frappe.db.set_value(
                            "Employee", row.employee, "holiday_list",
                            revert_to or None, update_modified=False,
                        )
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

    def _transfer_prior_holiday_list(self, transfer):
        """The holiday_list value recorded as `current` on the transfer's
        holiday_list property change — the value to revert the Employee to when
        this transfer is cancelled. Returns "" when it was blank, or None if the
        transfer has no holiday_list change (nothing to revert)."""
        for d in (transfer.transfer_details or []):
            if d.fieldname == "holiday_list":
                return d.current or ""
        return None

    def _revert_holiday_list_assignment(self, row, skipped):
        """Cancel (if submitted) or delete (if Draft) the Holiday List Assignment
        this row created. Only the forward assignment is touched; any backfilled
        prior assignment reflects the employee's genuine earlier weekly off (and
        may have pre-existed), so it is deliberately left in place.

        The Bulk Week Off is still docstatus=1 while its on_cancel runs, so its
        child row still links to this assignment; ignore_links / force bypasses the
        back-link check that would otherwise block the cancel/delete."""
        hla_name = getattr(row, "holiday_list_assignment", None)
        if not hla_name or not frappe.db.exists("Holiday List Assignment", hla_name):
            return
        try:
            hla = frappe.get_doc("Holiday List Assignment", hla_name)
            if hla.docstatus == 1:
                hla.flags.ignore_links = True
                hla.cancel()
            elif hla.docstatus == 0:
                hla.delete(ignore_permissions=True, force=True)
            row.db_set("holiday_list_assignment", None, update_modified=False)
        except Exception:
            skipped.append(hla_name)
            frappe.log_error(
                title="Bulk Week Off: Holiday List Assignment cancel failed",
                message=frappe.get_traceback(),
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
    """Scheduled daily. Two jobs, both idempotent and committed per-row:

    1. Submit Bulk Week Off-originated Employee Transfer drafts whose
       transfer_date has arrived (HRMS blocks submit when transfer_date > today,
       so future-dated rows are inserted as Draft and picked up here).
    2. Create + link the Holiday List Assignment for any transfer that is already
       submitted but was left without one — e.g. a transient failure on an
       earlier run. Without this, such a row would be stranded forever because
       the old query only looked at draft (docstatus = 0) transfers.

    Each row is committed independently and rolled back on its own error, so a
    single failing row can no longer poison the shared transaction and abort
    every subsequent row (which is what made the job "keep failing").
    """
    today = getdate()
    rows = frappe.db.sql(
        """
        SELECT d.name AS detail, d.employee, d.current_holiday_list,
               d.assigned_off_day, d.employee_transfer, d.holiday_list_assignment,
               d.parent, et.transfer_date, et.docstatus AS et_docstatus
        FROM `tabBulk Week Off Detail` d
        JOIN `tabBulk Week Off` p ON p.name = d.parent
        JOIN `tabEmployee Transfer` et ON et.name = d.employee_transfer
        WHERE p.docstatus = 1
          AND d.employee_transfer IS NOT NULL
          AND d.employee_transfer != ''
          AND (
                (et.docstatus = 0 AND et.transfer_date <= %s)
                OR
                (et.docstatus = 1
                 AND (d.holiday_list_assignment IS NULL OR d.holiday_list_assignment = ''))
              )
        """,
        (today,),
        as_dict=True,
    )

    submitted = 0
    linked = 0
    failed = 0
    for r in rows:
        try:
            if r.et_docstatus == 0:
                transfer = frappe.get_doc("Employee Transfer", r.employee_transfer)
                transfer.submit()
                submitted += 1

            if not r.holiday_list_assignment:
                hla_name = create_linked_holiday_list_assignment(
                    r.employee,
                    r.current_holiday_list,
                    r.assigned_off_day,
                    r.transfer_date,
                )
                if hla_name:
                    frappe.db.set_value(
                        "Bulk Week Off Detail",
                        r.detail,
                        "holiday_list_assignment",
                        hla_name,
                        update_modified=False,
                    )
                    linked += 1

            # Now that the transfer/assignment is effective, clear any stale
            # Absent that lands on a rest day (historical or new period).
            cancel_absents_on_effective_offdays(r.employee)

            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            failed += 1
            frappe.log_error(
                title="Bulk Week Off: scheduled Employee Transfer submit failed",
                message="Transfer: %s\nParent: %s\n%s"
                % (r.employee_transfer, r.parent, frappe.get_traceback()),
            )

    if submitted or linked or failed:
        frappe.logger().info(
            "submit_due_employee_transfers: submitted=%d linked=%d failed=%d"
            % (submitted, linked, failed)
        )
    return {"submitted": submitted, "linked": linked, "failed": failed}
