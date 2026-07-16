// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

frappe.ui.form.on("Bulk Overtime", {
	setup(frm) {
		frm.set_query("department", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("branch", () => ({ filters: { company: frm.doc.company } }));
	},

	onload(frm) {
		if (frm.is_new()) {
			if (!frm.doc.to_date) {
				frm.set_value("to_date", frappe.datetime.get_today());
			}
			if (!frm.doc.from_date) {
				frm.set_value("from_date", frappe.datetime.add_days(frappe.datetime.get_today(), -30));
			}
		}
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
});
