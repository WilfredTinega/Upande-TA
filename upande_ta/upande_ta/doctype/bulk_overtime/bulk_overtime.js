// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

const PAYROLL_PERIOD_METHOD = "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.get_payroll_period";

const bo_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));

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
		// rows only come from the Get Overtime picker
		frm.set_df_property("bulk_overtime_entries", "cannot_add_rows", 1);
		frm.toggle_display("get_from_overtime_request", frm.doc.docstatus === 0);

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
		// a batch started from an Overtime Request carries that request's own
		// dates, and the payroll default must not race in and overwrite them
		if (frm._dates_pinned) return;
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

	/** The one way rows get here: pick the approved requests to pay. Sweeping
	 * the whole period blindly is what "Select All" in the dialog does, and
	 * this way the cost is paid once, with the list in front of you. */
	/** Worked Hours is what the biometric read, so it is read-only until asked
	 * for — a scanner that missed a clock-out is the case this is for. */
	edit_worked_hours(frm) {
		frm._worked_hours_unlocked = !frm._worked_hours_unlocked;
		const grid = frm.fields_dict.bulk_overtime_entries.grid;
		grid.update_docfield_property("working_hours", "read_only", frm._worked_hours_unlocked ? 0 : 1);
		grid.refresh();
		frappe.show_alert({
			message: frm._worked_hours_unlocked
				? __("Worked Hours can now be edited on a row. Give a reason for each one you change.")
				: __("Worked Hours locked again."),
			indicator: frm._worked_hours_unlocked ? "orange" : "green",
		});
	},

	get_from_overtime_request(frm) {
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
			method: "get_approved_requests",
			freeze: true,
			freeze_message: __("Looking for approved requests..."),
		}).then((r) => {
			const requests = r.message || [];
			if (!requests.length) {
				frappe.msgprint({
					title: __("Nothing to Pick"),
					indicator: "orange",
					message: __(
						"No approved Overtime Request has anything left to add here — either none overlaps this period, or every day they cover is already in another Bulk Overtime.",
					),
				});
				return;
			}
			frm.events.show_request_dialog(frm, requests);
		});
	},

	show_request_dialog(frm, requests) {
		const line = (request) => {
			const span =
				request.overtime_date === request.to_date
					? frappe.datetime.str_to_user(request.overtime_date)
					: __("{0} to {1}", [
							frappe.datetime.str_to_user(request.overtime_date),
							frappe.datetime.str_to_user(request.to_date),
					  ]);
			// days_in_period is what this batch would actually pay, which is
			// not the whole request when the two only partly overlap
			const covered =
				request.days_in_period === 1
					? __("1 day in this period")
					: __("{0} days in this period", [request.days_in_period]);
			// a day is paid once. A request with nothing left is not offered at
			// all, so anything labelled here is only partly taken — say where
			// the rest went, or the missing rows look like a bug
			const taken = (request.taken_by || [])
				.map((t) =>
					t.this_batch
						? __("{0} row(s) already in this batch", [t.entries])
						: __("{0} row(s) already in {1}{2}", [
								t.entries,
								t.batch,
								t.submitted ? __(", submitted") : "",
						  ]),
				)
				.join(" · ");

			return `
				<label class="checkbox" style="display:block; padding:8px 0; border-bottom:1px solid var(--border-color);">
					<input type="checkbox" class="ot-request" data-name="${bo_esc(request.name)}">
					<b>${bo_esc(request.name)}</b>
					<span class="text-muted">${bo_esc(request.request_for || "")}</span>
					<span class="indicator-pill green" style="margin-left:6px;">${__("{0} row(s) to add", [
						request.rows_available,
					])}</span>
					${taken ? `<span class="indicator-pill orange" style="margin-left:4px;">${bo_esc(taken)}</span>` : ""}
					<div class="text-muted small" style="margin-left:22px;">
						${bo_esc(span)} · ${covered}<br>
						${__("{0} employee(s)", [request.employees])} ·
						${__("{0} h/day in total", [request.hours_per_day])}
						${request.reason ? ` · ${bo_esc(request.reason)}` : ""}
					</div>
				</label>`;
		};

		const dialog = new frappe.ui.Dialog({
			title: __("Get Overtime"),
			size: "large",
			fields: [
				{
					fieldtype: "Check",
					fieldname: "select_all",
					label: __("Select All"),
					change() {
						dialog.$wrapper
							.find("input.ot-request")
							.prop("checked", dialog.get_value("select_all") ? true : false);
					},
				},
				{ fieldtype: "HTML", fieldname: "requests" },
			],
			primary_action_label: __("Get Selected"),
			primary_action() {
				const picked = dialog.$wrapper
					.find("input.ot-request:checked")
					.map((_i, input) => $(input).data("name"))
					.get();
				if (!picked.length) {
					frappe.msgprint(__("Tick at least one Overtime Request."));
					return;
				}
				dialog.hide();
				frm.events.fetch_overtime(frm, picked);
			},
		});

		dialog.get_field("requests").$wrapper.html(requests.map(line).join(""));
		dialog.show();
	},

	/** The fetch itself. Reached from the picker, and from Create > Bulk
	 * Overtime on an Overtime Request. Frappe calls a field handler as
	 * (frm, doctype, name), so no button may point here directly: its second
	 * argument would arrive as the doctype name. */
	fetch_overtime(frm, overtime_requests) {
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
			args: { overtime_requests: overtime_requests || null },
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
					? result.picked
						? __("The Overtime Request(s) you picked cover no days inside this period.")
						: __("No approved Overtime Requests in this period.")
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
	// one reason field covers both kinds of change, so it is only cleared
	// once neither is in force
	manual_override(frm, cdt, cdn) {
		frm.events.clear_reason_if_untouched(frm, cdt, cdn);
	},

	manual_working_hours(frm, cdt, cdn) {
		frm.events.clear_reason_if_untouched(frm, cdt, cdn);
	},

	/** Typing a figure is the same statement as ticking the box, so the box
	 * follows the typing rather than having to be found first. */
	working_hours(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (frm._worked_hours_unlocked && !row.manual_working_hours) {
			frappe.model.set_value(cdt, cdn, "manual_working_hours", 1);
		}
	},
});

frappe.ui.form.on("Bulk Overtime", {
	clear_reason_if_untouched(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.manual_override && !row.manual_working_hours) {
			frappe.model.set_value(cdt, cdn, "override_reason", "");
		}
	},
});
