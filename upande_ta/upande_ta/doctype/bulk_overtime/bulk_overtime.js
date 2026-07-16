// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

frappe.ui.form.on("Bulk Overtime", {
	setup(frm) {
		// Restrict Department dropdown to the selected company
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company },
		}));

		// Restrict Shift Approver to users with approver-related roles
		frm.events.setup_shift_approver_query(frm);

		// Restrict Employee in child table to selected department/group
		frm.set_query("employee", "bulk_overtime_entries", () => ({
			filters: {
				company: frm.doc.company,
				...(frm.doc.department && { department: frm.doc.department }),
				...(frm.doc.designation && { designation: frm.doc.designation }),
				status: "Active"
			}
		}));
	},

	setup_shift_approver_query(frm) {
		const default_approver_roles = [
			"Expense Approver",
			"Leave Approver",
			"Wiki Approver",
			"General Manager",
			"HOD",
			"HR Manager",
			"System Manager",
		];

		// Use the default approver roles (field removed)
		const roles_to_use = default_approver_roles;

		frappe.db.get_list("Has Role", {
			filters: { role: ["in", roles_to_use] },
			fields: ["parent"],
			limit_page_length: 0,
		}).then((users) => {
			const approver_users = [...new Set(users.map((user) => user.parent))];

			const name_filter = approver_users.length
				? approver_users
				: ["__no_match__"];

			frm.set_query("shift_approver", () => ({
				filters: {
					enabled: 1,
					user_type: "System User",
					name: ["in", name_filter],
				},
			}));
		}).catch(() => {
			frm.set_query("shift_approver", () => ({
				filters: {
					enabled: 1,
					user_type: "System User",
					name: ["in", ["__no_match__"]],
				},
			}));
			frappe.show_alert({
				message: __("Could not load approver list. Shift Approver field has been restricted."),
				indicator: "orange",
			}, 5);
		});
	},

	// Employees are fetched automatically whenever a scoping filter changes (once the
	// mandatory Company + dates are set) — no manual "Get Employees" button.
	company(frm) { frm.events.auto_fetch(frm); },
	branch(frm) { frm.events.auto_fetch(frm); },
	department(frm) { frm.events.auto_fetch(frm); },
	designation(frm) { frm.events.auto_fetch(frm); },
	grade(frm) { frm.events.auto_fetch(frm); },
	from_date(frm) { frm.events.auto_fetch(frm); },
	to_date(frm) { frm.events.auto_fetch(frm); },

	auto_fetch(frm) {
		if (frm.doc.docstatus !== 0) return;

		// Need company + date range before we can compute overtime.
		if (!frm.doc.company || !frm.doc.from_date || !frm.doc.to_date) {
			frm.events.clear_entries(frm);
			return;
		}

		// Debounce rapid successive filter changes into a single fetch.
		if (frm._ot_fetch_timer) clearTimeout(frm._ot_fetch_timer);
		frm._ot_fetch_timer = setTimeout(() => {
			frappe.call({
				doc: frm.doc,
				method: "fill_employee_details",
				freeze: true,
				freeze_message: __("Fetching Employees…"),
			}).then(() => {
				frm.refresh_field("bulk_overtime_entries");
				frm.refresh_field("number_of_employees");
			});
		}, 300);
	},

	clear_entries(frm) {
		frm.clear_table("bulk_overtime_entries");
		frm.set_value("number_of_employees", 0);
		frm.refresh_field("bulk_overtime_entries");
	},

	apply_default_requested_hours(frm, show_message = true) {
		const entries = frm.doc.bulk_overtime_entries || [];
		if (!entries.length) {
			if (show_message) frappe.msgprint(__("No employees in the table. Click Get Employees first."));
			return;
		}

		const value = flt(frm.doc.default_requested_hours);
		if (!value) {
			if (show_message) {
				frappe.msgprint(__("Set Default Requested Hours first."));
			}
			return;
		}

		entries.forEach((row) => {
			row.hours_requested = value;
		});

		frm.refresh_field("bulk_overtime_entries");
		frm.dirty();

		if (show_message) {
			frappe.show_alert(
				{
					message: __("Applied {0} requested hours to {1} employees.", [value, entries.length]),
					indicator: "green",
				},
				5
			);
		}
	},
});