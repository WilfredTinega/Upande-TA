// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Gate Pass", {
	setup(frm) {
		frm.set_query("employee", () => {
			const filters = { status: "Active" };
			if (frm.doc.company) {
				filters.company = frm.doc.company;
			}
			return { filters };
		});
	},

	onload(frm) {
		// Default to the logged-in user's own Employee record, but leave the
		// field editable so HR / HOD can raise a pass on someone's behalf.
		if (frm.is_new() && !frm.doc.employee) {
			frappe.db
				.get_value("Employee", { user_id: frappe.session.user, status: "Active" }, "name")
				.then((r) => {
					if (r && r.message && r.message.name) {
						frm.set_value("employee", r.message.name);
					}
				});
		}
	},

	refresh(frm) {
		frm.trigger("add_print_action");
		frm.trigger("add_return_button");
	},

	employee(frm) {
		if (!frm.doc.employee) {
			frm.set_value({ supervisor: null, hod: null });
			return;
		}

		// Routing logic lives server-side so the controller and the form
		// cannot drift apart.
		frappe.call({
			method: "upande_ta.upande_ta.doctype.gate_pass.gate_pass.get_approvers",
			args: { employee: frm.doc.employee },
			callback(r) {
				if (!r.message) return;
				if (r.message.supervisor) frm.set_value("supervisor", r.message.supervisor);
				if (r.message.hod) frm.set_value("hod", r.message.hod);
			},
		});
	},

	returning_same_day(frm) {
		if (!frm.doc.returning_same_day) {
			frm.set_value("expected_time_in", null);
		}
	},

	add_print_action(frm) {
		if (frm.doc.docstatus !== 1) return;

		frm.page.set_primary_action(__("Print"), () => {
			frappe.utils.print(
				frm.doc.doctype,
				frm.doc.name,
				"Employee Gate Pass",
				frm.doc.letter_head,
				frm.doc.language || frappe.boot.lang
			);
		});
	},

	add_return_button(frm) {
		const may_record =
			frappe.user_roles.includes("Gate Security") ||
			frappe.user_roles.includes("HR User") ||
			frappe.user_roles.includes("HR Manager") ||
			frappe.user_roles.includes("Group HR Manager") ||
			frappe.user_roles.includes("System Manager");

		if (frm.doc.docstatus !== 1 || frm.doc.returned || !may_record) return;
		if (frm.doc.workflow_state !== "Approved") return;

		frm.add_custom_button(__("Record Return"), () => {
			frappe.prompt(
				[
					{
						fieldname: "actual_time_out",
						fieldtype: "Time",
						label: __("Actual Time Out"),
						default: frm.doc.actual_time_out || frm.doc.time_out,
						reqd: 1,
					},
					{
						fieldname: "actual_time_in",
						fieldtype: "Time",
						label: __("Actual Time In"),
						default: frappe.datetime.now_time(),
						reqd: 1,
					},
				],
				(values) => {
					frappe
						.xcall("frappe.client.set_value", {
							doctype: frm.doc.doctype,
							name: frm.doc.name,
							fieldname: {
								actual_time_out: values.actual_time_out,
								actual_time_in: values.actual_time_in,
							},
						})
						.then(() => frm.reload_doc());
				},
				__("Record Return"),
				__("Save")
			);
		});
	},
});
