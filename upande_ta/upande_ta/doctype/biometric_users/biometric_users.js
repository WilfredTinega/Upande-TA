// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Biometric Users", {
    refresh: function(frm) {
        frm.add_custom_button("Bulk Add",    () => open_bulk_dialog(frm, "Add User"),    "Bulk Actions");
        frm.add_custom_button("Bulk Update", () => open_bulk_dialog(frm, "Update User"), "Bulk Actions");
        frm.add_custom_button("Bulk Delete", () => open_bulk_dialog(frm, "Delete User"), "Bulk Actions");
    }
});

frappe.ui.form.on("Device Users", {
    add: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        let d = new frappe.ui.Dialog({
            title: "Add User to Device",
            fields: [
                {
                    fieldname: "user_id",
                    fieldtype: "Data",
                    label: "User ID (PIN)",
                    reqd: 1,
                    default: row.user_id
                },
                {
                    fieldname: "employee_name",
                    fieldtype: "Data",
                    label: "Employee Name",
                    reqd: 1,
                    default: row.employee_name
                },
                {
                    fieldname: "privilege",
                    fieldtype: "Select",
                    label: "Privilege",
                    options: "0\n14",
                    default: row.privilege || "0"
                }
            ],
            primary_action_label: "Add to Device",
            primary_action(values) {
                frappe.call({
                    method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.send_device_command",
                    args: {
                        doc_name:     frm.doc.name,
                        row_name:     row.name,
                        command_type: "Add User",
                        override:     values
                    },
                    callback: function(r) {
                        if (!r.exc) {
                            d.hide();
                            frm.refresh_field("device_users");
                            frappe.show_alert({
                                message: `PIN ${values.user_id} (${values.employee_name}) add command sent to device`,
                                indicator: "green"
                            }, 5);
                        }
                    }
                });
            }
        });
        d.show();
    },

    update: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        let d = new frappe.ui.Dialog({
            title: "Update User on Device",
            fields: [
                {
                    fieldname: "user_id",
                    fieldtype: "Data",
                    label: "User ID (PIN)",
                    reqd: 1,
                    default: row.user_id
                },
                {
                    fieldname: "employee_name",
                    fieldtype: "Data",
                    label: "Employee Name",
                    reqd: 1,
                    default: row.employee_name
                },
                {
                    fieldname: "privilege",
                    fieldtype: "Select",
                    label: "Privilege",
                    options: "0\n14",
                    default: row.privilege || "0"
                }
            ],
            primary_action_label: "Update on Device",
            primary_action(values) {
                frappe.call({
                    method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.send_device_command",
                    args: {
                        doc_name:     frm.doc.name,
                        row_name:     row.name,
                        command_type: "Update User",
                        override:     values
                    },
                    callback: function(r) {
                        if (!r.exc) {
                            d.hide();
                            frm.refresh_field("device_users");
                            frappe.show_alert({
                                message: `PIN ${values.user_id} (${values.employee_name}) update command sent to device`,
                                indicator: "blue"
                            }, 5);
                        }
                    }
                });
            }
        });
        d.show();
    },

    delete: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        let d = new frappe.ui.Dialog({
            title: "Delete User from Device",
            fields: [
                {
                    fieldname: "info",
                    fieldtype: "HTML",
                    options: `<div style="padding: 8px 0; color: var(--color-text-secondary)">
                        You are about to delete <strong>${row.employee_name}</strong>
                        (PIN: ${row.user_id}) from this device.<br><br>
                        This cannot be undone. The user will need to be re-enrolled physically.
                    </div>`
                },
                {
                    fieldname: "user_id",
                    fieldtype: "Data",
                    label: "User ID (PIN)",
                    read_only: 1,
                    default: row.user_id
                },
                {
                    fieldname: "employee_name",
                    fieldtype: "Data",
                    label: "Employee Name",
                    read_only: 1,
                    default: row.employee_name
                }
            ],
            primary_action_label: "Delete from Device",
            primary_action(values) {
                frappe.call({
                    method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.send_device_command",
                    args: {
                        doc_name:     frm.doc.name,
                        row_name:     row.name,
                        command_type: "Delete User",
                        override:     values
                    },
                    callback: function(r) {
                        if (!r.exc) {
                            d.hide();
                            frm.reload_doc();
                            frappe.show_alert({
                                message: `PIN ${row.user_id} (${row.employee_name}) delete command sent to device`,
                                indicator: "red"
                            }, 5);
                        }
                    }
                });
            }
        });
        d.show();
    }
});


function _build_bulk_dialog(command_type, default_sn, default_location, on_success) {
    let dialog_title = {
        "Add User":    "Bulk Add Users to Device",
        "Update User": "Bulk Update Users on Device",
        "Delete User": "Bulk Delete Users from Device"
    }[command_type];

    let indicator = {
        "Add User":    "green",
        "Update User": "blue",
        "Delete User": "red"
    }[command_type];

    let d = new frappe.ui.Dialog({
        title: dialog_title,
        size: "extra-large",
        fields: [
            {
                fieldname: "device_sn",
                fieldtype: "Select",
                label: "Target Device",
                reqd: 1,
                change() {
                    let raw = d.get_value("device_sn");
                    if (raw) load_users(raw.split(" — ")[0].trim());
                }
            },
            {
                fieldname: "filter_section",
                fieldtype: "Section Break"
            },
            {
                fieldname: "filter_department",
                fieldtype: "Autocomplete",
                label: "Department",
                options: [],
                change() {
                    close_autocomplete("filter_department");
                    reload_with_filters();
                }
            },
            {
                fieldname: "col_break_filter_1",
                fieldtype: "Column Break"
            },
            {
                fieldname: "filter_designation",
                fieldtype: "Autocomplete",
                label: "Designation",
                options: [],
                change() {
                    close_autocomplete("filter_designation");
                    reload_with_filters();
                }
            },
            {
                fieldname: "col_break_filter_2",
                fieldtype: "Column Break"
            },
            {
                fieldname: "filter_employee",
                fieldtype: "Link",
                label: "Employee",
                options: "Employee",
                get_query() {
                    let f = { status: "Active" };
                    let dept = d.get_value("filter_department");
                    let desg = d.get_value("filter_designation");
                    if (dept) f.department  = dept;
                    if (desg) f.designation = desg;
                    return { filters: f };
                },
                change() { reload_with_filters(); }
            },
            {
                fieldname: "col_break_filter_3",
                fieldtype: "Column Break"
            },
            {
                fieldname: "clear_filters_html",
                fieldtype: "HTML",
                options: `<div style="display:flex;align-items:flex-end;height:100%;padding-bottom:4px">
                    <button type="button" id="clear-filters-btn"
                        title="Clear filters"
                        style="background:transparent;border:1px solid var(--color-border-tertiary);
                            border-radius:6px;width:48px;height:48px;cursor:pointer;
                            color:var(--color-text-secondary);font-size:24px;font-weight:600;
                            line-height:1;display:none;align-items:center;justify-content:center">
                        ✕
                    </button>
                </div>`
            },
            {
                fieldname: "table_section",
                fieldtype: "Section Break"
            },
            {
                fieldname: "user_table_html",
                fieldtype: "HTML",
                options: `<div id="bulk-user-table" style="margin-top:8px">
                    <p style="color:var(--color-text-secondary)">Loading...</p>
                </div>`
            }
        ],
        primary_action_label: `${command_type.split(" ")[0]} Selected`,
        primary_action() {
            let checked = get_checked_users();
            if (!checked.length) {
                frappe.msgprint("Select at least one user.");
                return;
            }

            let raw = d.get_value("device_sn") || "";
            let sn  = raw.split(" — ")[0].trim();
            let loc = raw.split(" — ")[1] || sn;

            let label = command_type === "Delete User"
                ? `Delete ${checked.length} user(s) from ${loc}?`
                : `${command_type.split(" ")[0]} ${checked.length} user(s) on ${loc}?`;

            frappe.confirm(label, () => {
                frappe.call({
                    method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.bulk_command",
                    args: {
                        device_sn:    sn,
                        users:        JSON.stringify(checked),
                        command_type: command_type
                    },
                    freeze: true,
                    freeze_message: `Queuing ${command_type} commands...`,
                    callback(r) {
                        if (!r.exc) {
                            d.hide();
                            if (on_success) on_success();
                            let msg = `${r.message.queued} command(s) queued successfully.`;
                            if (r.message.failed > 0) msg += ` ${r.message.failed} failed.`;
                            frappe.show_alert({ message: msg, indicator: indicator }, 8);
                        }
                    }
                });
            });
        }
    });

    frappe.call({
        method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.get_devices",
        callback(r) {
            if (r.message) {
                let options = r.message.map(dev =>
                    `${dev.device_sn} — ${dev.device_location || "No location"}`
                );
                d.set_df_property("device_sn", "options", options);
                if (default_sn && default_location) {
                    d.set_value("device_sn", `${default_sn} — ${default_location}`);
                    load_users(default_sn);
                }
                d.refresh_field("device_sn");
            }
        }
    });

    refresh_filter_options();

    function refresh_filter_options() {
        let department  = d.get_value("filter_department")  || null;
        let designation = d.get_value("filter_designation") || null;
        frappe.call({
            method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.get_active_filter_options",
            args: { department, designation },
            callback(r) {
                let opts = r.message || {};
                let valid_designations = opts.designations || [];
                set_autocomplete_options("filter_designation", valid_designations);
                set_autocomplete_options("filter_department",  opts.departments  || []);
                set_filter_label("filter_department",  "Department",  opts.department_count);
                set_filter_label("filter_designation", "Designation", opts.designation_count);
                set_filter_label("filter_employee",    "Employee",    opts.employee_count);

                // If a previously-set designation no longer fits the chosen department, clear it
                if (designation && !valid_designations.includes(designation)) {
                    d.set_value("filter_designation", "");
                }
            }
        });
    }

    function set_filter_label(fieldname, base_label, count) {
        if (count == null) return;
        let label = `${base_label} (${count})`;
        d.set_df_property(fieldname, "label", label);
    }

    function set_autocomplete_options(fieldname, values) {
        let field = d.get_field(fieldname);
        if (!field) return;
        field.df.options = values;
        if (typeof field.set_data === "function") {
            field.set_data(values);
        }
    }

    function close_autocomplete(fieldname) {
        let field = d.get_field(fieldname);
        if (field && field.awesomplete) {
            field.awesomplete.close();
        }
    }

    d.show();

    d.$wrapper.on("click", "#clear-filters-btn", () => {
        d.set_value("filter_employee",    "");
        d.set_value("filter_designation", "");
        d.set_value("filter_department",  "");
        reload_with_filters();
    });

    function reload_with_filters() {
        toggle_clear_btn();
        refresh_filter_options();
        validate_employee_against_cascade();
        let raw = d.get_value("device_sn");
        if (raw) load_users(raw.split(" — ")[0].trim());
    }

    function validate_employee_against_cascade() {
        let emp  = d.get_value("filter_employee");
        let dept = d.get_value("filter_department");
        let desg = d.get_value("filter_designation");
        if (!emp || (!dept && !desg)) return;

        let filters = { name: emp };
        if (dept) filters.department  = dept;
        if (desg) filters.designation = desg;

        frappe.db.get_list("Employee", { filters, limit: 1 }).then(rows => {
            if (!rows || !rows.length) {
                d.set_value("filter_employee", "");
            }
        });
    }

    function toggle_clear_btn() {
        let any = d.get_value("filter_employee")
               || d.get_value("filter_designation")
               || d.get_value("filter_department");
        d.$wrapper.find("#clear-filters-btn").css("display", any ? "flex" : "none");
    }

    function get_filter_args() {
        return {
            employee:    d.get_value("filter_employee")    || null,
            designation: d.get_value("filter_designation") || null,
            department:  d.get_value("filter_department")  || null
        };
    }

    function load_users(sn) {
        let container = d.$wrapper.find("#bulk-user-table");
        container.html(`<p style="color:var(--color-text-secondary)">Loading...</p>`);

        let filters = get_filter_args();

        frappe.call({
            method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.get_server_settings",
            callback(sr) {
                let is_delete = (sr.message || {}).is_delete || 0;
                let is_update = (sr.message || {}).is_update || 0;

                frappe.call({
                    method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.get_device_users",
                    args: { device_sn: sn },
                    callback(r) {
                        let device_users = r.message || [];

                        let has_filters = filters.employee || filters.designation || filters.department;

                        if (command_type === "Add User") {
                            frappe.call({
                                method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.get_employees",
                                args: Object.assign({ status: "Active" }, filters),
                                callback(er) {
                                    let employees   = er.message || [];
                                    let device_pins = new Set(device_users.map(u => u.user_id));
                                    let new_emps    = employees.filter(e => !device_pins.has(e.user_id));
                                    render_table(new_emps.map(e => ({
                                        user_id:       e.user_id,
                                        employee_name: e.full_name,
                                        privilege:     "0"
                                    })), command_type);
                                }
                            });

                        } else if (command_type === "Update User") {
                            frappe.call({
                                method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.get_employees",
                                args: Object.assign({ status: "Active" }, filters),
                                callback(er) {
                                    let employees   = er.message || [];
                                    let active_pins = new Set(employees.map(e => e.user_id));

                                    if (device_users.length) {
                                        let updatable = device_users.filter(u => active_pins.has(u.user_id));
                                        render_table(updatable, command_type);
                                    } else if (is_update || has_filters) {
                                        render_table(employees.map(e => ({
                                            user_id:       e.user_id,
                                            employee_name: e.full_name,
                                            privilege:     "0"
                                        })), command_type);
                                    } else {
                                        render_table([], command_type);
                                    }
                                }
                            });

                        } else if (command_type === "Delete User") {
                            frappe.call({
                                method: "upande_ta.upande_ta.doctype.biometric_users.biometric_users.get_employees",
                                args: Object.assign({ status: "Inactive" }, filters),
                                callback(er) {
                                    let inactive      = er.message || [];
                                    let inactive_pins = new Set(inactive.map(e => e.user_id));

                                    if (device_users.length) {
                                        let deletable = device_users.filter(u => inactive_pins.has(u.user_id));
                                        render_table(deletable, command_type);
                                    } else if (is_delete || has_filters) {
                                        render_table(inactive.map(e => ({
                                            user_id:       e.user_id,
                                            employee_name: e.full_name,
                                            privilege:     "0"
                                        })), command_type);
                                    } else {
                                        render_table([], command_type);
                                    }
                                }
                            });
                        }
                    }
                });
            }
        });
    }

    function render_table(users, action) {
        let container = d.$wrapper.find("#bulk-user-table");

        if (!users.length) {
            container.html(`<p style="color:var(--color-text-secondary);padding:8px 0">
                No users found for this action on the selected device.
            </p>`);
            return;
        }

        let show_privilege = action !== "Delete User";
        let privilege_col  = show_privilege ? `<th style="width:120px">Privilege</th>` : "";

        let rows = users.map((u, i) => {
            let privilege_cell = show_privilege ? `
                <td>
                    <select class="form-control form-control-sm privilege-sel"
                            data-idx="${i}" style="width:100px">
                        <option value="0"  ${u.privilege === "0"  ? "selected" : ""}>User</option>
                        <option value="14" ${u.privilege === "14" ? "selected" : ""}>Admin</option>
                    </select>
                </td>` : "";

            let status_badge = u.status ? `
                <span style="font-size:11px;padding:2px 6px;border-radius:4px;
                    background:var(--color-background-success);
                    color:var(--color-text-success)">
                    ${u.status}
                </span>` : "";

            return `
                <tr data-idx="${i}">
                    <td style="width:40px;text-align:center">
                        <input type="checkbox" class="user-check" data-idx="${i}">
                    </td>
                    <td style="width:130px;font-family:var(--font-mono);font-size:13px">
                        ${frappe.utils.escape_html(u.user_id || "")}
                    </td>
                    <td>
                        ${frappe.utils.escape_html(u.employee_name || "")}
                        ${status_badge}
                    </td>
                    ${privilege_cell}
                </tr>`;
        }).join("");

        container.html(`
            <div style="margin-bottom:8px;display:flex;gap:8px;align-items:center">
                <button class="btn btn-xs btn-default" id="select-all-btn">Select All</button>
                <button class="btn btn-xs btn-default" id="deselect-all-btn">Deselect All</button>
                <span style="font-size:12px;color:var(--color-text-secondary)"
                      id="selected-count">0 / ${users.length}</span>
            </div>
            <div style="max-height:400px;overflow-y:auto;
                border:1px solid var(--color-border-tertiary);border-radius:8px">
                <table class="table table-sm" style="margin:0">
                    <thead style="position:sticky;top:0;
                        background:var(--color-background-secondary)">
                        <tr>
                            <th style="width:40px"></th>
                            <th style="width:130px">PIN</th>
                            <th>Name</th>
                            ${privilege_col}
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `);

        container.find("#select-all-btn").on("click", () => {
            container.find(".user-check").prop("checked", true);
            update_count();
        });
        container.find("#deselect-all-btn").on("click", () => {
            container.find(".user-check").prop("checked", false);
            update_count();
        });
        container.find(".user-check").on("change", update_count);

        function update_count() {
            let n = container.find(".user-check:checked").length;
            container.find("#selected-count").text(`${n} / ${users.length}`);
        }

        window._bulk_users_data = users;
    }

    function get_checked_users() {
        let container = d.$wrapper.find("#bulk-user-table");
        let checked   = [];
        container.find(".user-check:checked").each(function() {
            let idx  = parseInt($(this).data("idx"));
            let user = window._bulk_users_data[idx];
            let priv = container.find(`.privilege-sel[data-idx="${idx}"]`).val()
                       || user.privilege || "0";
            checked.push({
                user_id:       user.user_id,
                employee_name: user.employee_name,
                privilege:     priv,
                row_name:      user.row_name || null
            });
        });
        return checked;
    }
}


function open_bulk_dialog(frm, command_type) {
    _build_bulk_dialog(
        command_type,
        frm.doc.device_sn,
        frm.doc.device_location || frm.doc.device_sn,
        () => frm.reload_doc()
    );
}