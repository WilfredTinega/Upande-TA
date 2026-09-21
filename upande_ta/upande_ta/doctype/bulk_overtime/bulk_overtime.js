// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

const PAYROLL_PERIOD_METHOD = "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.get_payroll_period";

const STATUS_INDICATOR = {
	Matched: "green",
	"Capped at Request": "blue",
	"Worked Less": "orange",
	"No Clock-Out": "red",
	"No Attendance": "red",
};

frappe.ui.form.on("Bulk Overtime", {
	setup(frm) {
		frm.set_query("custom_farm", () => ({ filters: { company: frm.doc.company } }));

		frm.set_indicator_formatter("employee", (row) => STATUS_INDICATOR[row.status] || "gray");
	},

	onload(frm) {
		if (frm.is_new() && frm.doc.company && !frm.doc.from_date) frm.events.set_payroll_period(frm);
	},

	refresh(frm) {
		// rows only come from Get Overtime
		frm.set_df_property("bulk_overtime_entries", "cannot_add_rows", 1);
		frm.toggle_display("get_overtime", frm.doc.docstatus === 0);

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Overtime Slips"), () =>
				frappe.set_route("List", "Overtime Slip", { custom_bulk_overtime: frm.doc.name }),
			);
		}
	},

	company(frm) {
		frm.set_value("custom_farm", "");
		frm.events.set_payroll_period(frm);
		frm.events.clear_entries(frm);
	},

	custom_farm(frm) {
		frm.events.clear_entries(frm);
	},

	from_date(frm) {
		frm.events.clear_entries(frm);
	},

	to_date(frm) {
		frm.events.clear_entries(frm);
	},

	/** Default the period to the company's payroll dates (Biometric Setting → Attendance Filters). */
	set_payroll_period(frm) {
		if (!frm.doc.company || frm.doc.docstatus !== 0) return;
		frappe.call({ method: PAYROLL_PERIOD_METHOD, args: { company: frm.doc.company } }).then((r) => {
			const period = r.message || {};
			if (!period.start_date || !period.end_date) return;
			// overtime is paid once worked: never default past today
			const end = period.end_date > frappe.datetime.get_today() ? frappe.datetime.get_today() : period.end_date;
			frm.set_value({ from_date: period.start_date, to_date: end });
		});
	},

	clear_entries(frm) {
		if (frm.doc.docstatus !== 0 || !(frm.doc.bulk_overtime_entries || []).length) return;
		frm.clear_table("bulk_overtime_entries");
		frm.refresh_field("bulk_overtime_entries");
	},

	get_overtime(frm) {
		if (!frm.doc.company || !frm.doc.from_date || !frm.doc.to_date) {
			frappe.msgprint({
				title: __("Missing Details"),
				indicator: "red",
				message: __("Set the <b>Company</b>, <b>From Date</b> and <b>To Date</b> first."),
			});
			return;
		}

		frm.call({
			doc: frm.doc,
			method: "get_overtime",
			freeze: true,
			freeze_message: __("Checking requests against attendance..."),
		}).then((r) => {
			frm.dirty();
			frm.refresh_fields();
			const result = r.message || {};

			if (!result.rows && !(result.left_out || []).length) {
				// saying "no requests" when there are approved requests but no
				// attendance yet sends people looking in the wrong place
				const message = !result.approved_requests
					? __("No approved Overtime Requests in this period.")
					: __(
							"Found <b>{0}</b> approved Overtime Request(s), but no attendance on any of the <b>{1}</b> day(s) they cover. Overtime is paid against the biometric logs, so there is nothing to pay until the attendance for those days is in.",
							[result.approved_requests, result.days_without_attendance],
					  );
				frappe.msgprint({ title: __("Nothing to Pay"), indicator: "orange", message });
				return;
			}
			if ((result.left_out || []).length) {
				const shown = result.left_out.slice(0, 20);
				if (result.left_out.length > shown.length) {
					shown.push(__("... and {0} more", [result.left_out.length - shown.length]));
				}
				frappe.msgprint({
					title: __("Left Out"),
					indicator: "orange",
					message: __("{0} request row(s) were left out, because they are already being paid:", [
						result.left_out.length,
					]) + `<br><br>${shown.join("<br>")}`,
				});
				return;
			}
			frappe.show_alert({ message: __("{0} row(s) loaded.", [result.rows]), indicator: "green" });
		});
	},
});

frappe.ui.form.on("Bulk Overtime Entry", {
	manual_override(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.manual_override) {
			frappe.model.set_value(cdt, cdn, "override_reason", "");
		}
	},
});
