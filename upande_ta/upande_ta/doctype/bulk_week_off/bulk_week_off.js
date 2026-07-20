// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Bulk Week Off", {
    setup: function (frm) {
        frm.set_query("department", function () {
            if (frm.doc.company) {
                return { filters: { company: frm.doc.company } };
            }
        });
        frm.set_query("employee", function () {
            var filters = { status: "Active" };
            if (frm.doc.company) filters.company = frm.doc.company;
            if (frm.doc.department) filters.department = frm.doc.department;
            if (frm.doc.designation) filters.designation = frm.doc.designation;
            return { filters: filters };
        });
        frm.set_query("holiday_list", function () {
            return {};
        });

        frm._fetch_employees_debounced = frappe.utils.debounce(function () {
            frm.events.fetch_employees(frm);
        }, 400);
    },

    clear_filters: function (frm) {
        frm.set_value("department", "");
        frm.set_value("designation", "");
        frm.set_value("employee", "");
    },

    company: function (frm) {
        frm.set_value("department", "");
        frm.set_value("designation", "");
        frm.set_value("employee", "");
        if (frm._fetch_employees_debounced) frm._fetch_employees_debounced();
    },

    department: function (frm) {
        if (frm._fetch_employees_debounced) frm._fetch_employees_debounced();
    },

    designation: function (frm) {
        if (frm._fetch_employees_debounced) frm._fetch_employees_debounced();
    },

    employee: function (frm) {
        if (frm._fetch_employees_debounced) frm._fetch_employees_debounced();
    },

    holiday_list: function (frm) {
        if (frm.doc.holiday_list) {
            frappe.db.get_value("Holiday List", frm.doc.holiday_list, ["from_date", "to_date"])
                .then(function (r) {
                    if (r && r.message) {
                        // Default the transfer date to the list start only when
                        // unset; Week Off Start then follows the transfer date.
                        if (!frm.doc.from_date) {
                            frm.set_value("from_date", r.message.from_date);
                        }
                        // Week Off End = the holiday list's end.
                        frm.set_value("holiday_list_end", r.message.to_date);
                        // Week Off Start = the (dynamic) transfer date.
                        frm.set_value("holiday_list_start", frm.doc.from_date);
                    }
                });
        } else {
            frm.set_value("holiday_list_start", "");
            frm.set_value("holiday_list_end", "");
            frm.set_value("from_date", "");
        }
        if (frm.doc.employees && frm.doc.employees.length) {
            frm.doc.employees.forEach(function (row) {
                frappe.model.set_value(row.doctype, row.name, "assigned_off_day", frm.doc.holiday_list);
            });
        }
        if (frm._fetch_employees_debounced) frm._fetch_employees_debounced();
    },

    from_date: function (frm) {
        // Keep Week Off Start in sync with the Scheduled Transfer Date.
        frm.set_value("holiday_list_start", frm.doc.from_date || "");
    },

    fetch_employees: function (frm) {
        if (!frm.doc.company || frm.doc.docstatus !== 0) {
            return;
        }

        frm.call({
            method: "get_employees",
            doc: frm.doc,
            freeze: true,
            freeze_message: __("Fetching Employees..."),
            callback: function (r) {
                frm.clear_table("employees");
                var count = (r.message && r.message.length) || 0;
                if (count) {
                    r.message.forEach(function (emp) {
                        var row = frm.add_child("employees");
                        row.employee = emp.employee;
                        row.employee_name = emp.employee_name;
                        row.department = emp.department;
                        row.designation = emp.designation;
                        row.current_holiday_list = emp.current_holiday_list;
                        row.assigned_off_day = frm.doc.holiday_list;
                    });
                }
                frm.refresh_field("employees");

                // Replace any prior fetch alert so multiple rapid fetches
                // don't pile up and obstruct the screen.
                if (frm._last_fetch_alert) {
                    frm._last_fetch_alert.remove();
                    frm._last_fetch_alert = null;
                }
                frappe.show_alert({
                    message: count
                        ? __("{0} employee(s) loaded.", [count])
                        : __("No employees match the current filters."),
                    indicator: count ? "green" : "orange",
                });
                frm._last_fetch_alert = $(".desk-alert").last();
            },
        });
    },
});
