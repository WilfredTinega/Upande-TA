// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Bulk Overtime", {
	// ── Lifecycle ──────────────────────────────────────────────────────────────

	setup(frm) {
		// Restrict Department dropdown to the selected company
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company },
		}));

		// Restrict Branch dropdown to the selected company
		frm.set_query("branch", () => ({
			filters: { company: frm.doc.company },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			// "Get Employees" button – highlighted when the table is empty
			frm.add_custom_button(__("Get Employees"), () => {
				frm.events.get_employees(frm);
			}).toggleClass(
				"btn-primary",
				!(frm.doc.bulk_overtime_entries || []).length,
			);
		}
	},

	// ── Employee fetching ──────────────────────────────────────────────────────

	get_employees(frm) {
		const mandatory = ["company", "from_date", "to_date"];
		const missing = mandatory.filter((f) => !frm.doc[f]);

		if (missing.length) {
			frappe.msgprint({
				title: __("Missing Fields"),
				indicator: "red",
				message:
					__("Please fill in: ") +
					missing.map((f) => __(frappe.unscrub(f))).join(", "),
			});
			return;
		}

		return frappe
			.call({
				doc: frm.doc,
				method: "fill_employee_details",
				freeze: true,
				freeze_message: __("Fetching Employees…"),
			})
			.then((r) => {
				if (r.docs?.[0]?.bulk_overtime_entries) {
					frm.dirty();
					frm.save();
				}
				frm.refresh();
				frm.scroll_to_field("bulk_overtime_entries");
			});
	},

	// ── Clear table when any filter changes ───────────────────────────────────

	company(frm) {
		frm.events.clear_entries(frm);
	},

	branch(frm) {
		frm.events.clear_entries(frm);
	},

	department(frm) {
		frm.events.clear_entries(frm);
	},

	designation(frm) {
		frm.events.clear_entries(frm);
	},

	grade(frm) {
		frm.events.clear_entries(frm);
	},

	from_date(frm) {
		frm.events.clear_entries(frm);
	},

	to_date(frm) {
		frm.events.clear_entries(frm);
	},

	clear_entries(frm) {
		frm.clear_table("bulk_overtime_entries");
		frm.set_value("number_of_employees", 0);
		frm.refresh_field("bulk_overtime_entries");
	},
});
