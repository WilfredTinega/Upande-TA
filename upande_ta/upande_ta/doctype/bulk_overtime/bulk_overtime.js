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

	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Get Employees"), () => {
				frm.events.get_employees(frm);
			}).toggleClass("btn-primary", !(frm.doc.bulk_overtime_entries || []).length);
		}
	},

	get_employees(frm) {
		const mandatory = ["company", "from_date", "to_date"];
		const missing = mandatory.filter(f => !frm.doc[f]);

		if (missing.length) {
			frappe.msgprint({
				title: __("Missing Fields"),
				indicator: "red",
				message: __("Please fill in: ") + missing.map(f => __(frappe.unscrub(f))).join(", "),
			});
			return;
		}

		return frappe.call({
			doc: frm.doc,
			method: "fill_employee_details",
			freeze: true,
			freeze_message: __("Fetching Employees…"),
		}).then(r => {
			if (r.docs?.[0]?.bulk_overtime_entries) {
				frm.dirty();
				frm.save();
			}
			frm.refresh();
			frm.scroll_to_field("bulk_overtime_entries");
		});
	},

	company(frm) { frm.events.clear_entries(frm); },
	branch(frm) { frm.events.clear_entries(frm); },
	department(frm) { frm.events.clear_entries(frm); },
	designation(frm) { frm.events.clear_entries(frm); },
	grade(frm) { frm.events.clear_entries(frm); },
	from_date(frm) { frm.events.clear_entries(frm); },
	to_date(frm) { frm.events.clear_entries(frm); },

	clear_entries(frm) {
		frm.clear_table("bulk_overtime_entries");
		frm.set_value("number_of_employees", 0);
		frm.refresh_field("bulk_overtime_entries");
	},
});
