// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Bulk Holiday Assignment", {
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
            if (frm.doc.branch) filters.branch = frm.doc.branch;
            if (frm.doc.designation) filters.designation = frm.doc.designation;
            if (frm.doc.employment_type) filters.employment_type = frm.doc.employment_type;
            return { filters: filters };
        });
        frm.set_query("holiday_list", function () {
            return {};
        });
    },

    clear_filters: function (frm) {
        frm.set_value("department", "");
        frm.set_value("branch", "");
        frm.set_value("designation", "");
        frm.set_value("employment_type", "");
        frm.set_value("employee", "");
    },

    company: function (frm) {
        frm.set_value("department", "");
        frm.set_value("branch", "");
        frm.set_value("designation", "");
        frm.set_value("employment_type", "");
        frm.set_value("employee", "");
        frm.trigger("fetch_employees");
    },

    department: function (frm) {
        frm.trigger("fetch_employees");
    },

    branch: function (frm) {
        frm.trigger("fetch_employees");
    },

    designation: function (frm) {
        frm.trigger("fetch_employees");
    },

    employment_type: function (frm) {
        frm.trigger("fetch_employees");
    },

    employee: function (frm) {
        frm.trigger("fetch_employees");
    },

    holiday_list: function (frm) {
        if (frm.doc.holiday_list) {
            frappe.db.get_value("Holiday List", frm.doc.holiday_list, ["from_date", "to_date"])
                .then(function (r) {
                    if (r && r.message) {
                        frm.set_value("holiday_list_start", r.message.from_date);
                        frm.set_value("holiday_list_end", r.message.to_date);
                        if (!frm.doc.from_date) {
                            frm.set_value("from_date", r.message.from_date);
                        }
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
        frm.trigger("fetch_employees");
    },

    fetch_employees: function (frm) {
        if (!frm.doc.company || !frm.doc.holiday_list || frm.doc.docstatus !== 0) {
            return;
        }

        frm.call({
            method: "get_employees",
            doc: frm.doc,
            freeze: true,
            freeze_message: __("Fetching Employees..."),
            callback: function (r) {
                frm.clear_table("employees");
                if (r.message && r.message.length) {
                    r.message.forEach(function (emp) {
                        var row = frm.add_child("employees");
                        row.employee = emp.employee;
                        row.employee_name = emp.employee_name;
                        row.department = emp.department;
                        row.branch = emp.branch;
                        row.designation = emp.designation;
                        row.current_holiday_list = emp.current_holiday_list;
                        row.assigned_off_day = frm.doc.holiday_list;
                    });
                    frm.refresh_field("employees");
                    frappe.show_alert({
                        message: __("{0} employee(s) found.", [r.message.length]),
                        indicator: "green"
                    });
                } else {
                    frm.refresh_field("employees");
                    frappe.show_alert({
                        message: __("No employees found matching filters."),
                        indicator: "orange"
                    });
                }
            },
        });
    },
});