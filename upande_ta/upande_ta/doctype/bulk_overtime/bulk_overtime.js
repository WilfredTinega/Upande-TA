// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

const bo_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));

/** An inline bar on the form's own dashboard, for work whose length cannot be
 * known in advance: it sweeps towards the end rather than pretending to
 * measure, and leaves the rest of the page usable — which a freeze does not.
 * Returns a stop(). */
const bo_progress = (frm, title, message) => {
	let percent = 8;
	let stopped = false;
	frm.dashboard.show_progress(title, percent, message);
	const timer = setInterval(() => {
		percent = Math.min(92, percent + (92 - percent) / 6);
		frm.dashboard.show_progress(title, percent, message);
	}, 400);
	// stop() is called on both the happy path and in finally(), and
	// hide_progress throws on a bar it has already removed
	return () => {
		if (stopped) return;
		stopped = true;
		clearInterval(timer);
		frm.dashboard.show_progress(title, 100, message);
		setTimeout(() => {
			try {
				frm.dashboard.hide_progress(title);
			} catch (e) {
				// the form was refreshed or routed away from under it
			}
		}, 400);
	};
};

/** The button must not be clickable twice while its own fetch is running —
 * nothing is blocking the page any more. */
const bo_busy = (frm, fieldname, busy) => {
	const field = frm.fields_dict[fieldname];
	if (field && field.$input) field.$input.prop("disabled", busy);
};

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

	clear_entries(frm) {
		if (frm.doc.docstatus !== 0 || !(frm.doc.bulk_overtime_entries || []).length) return;
		frm.clear_table("bulk_overtime_entries");
		frm.refresh_field("bulk_overtime_entries");
	},

	/** The one way rows get here: pick the approved requests to pay. Sweeping
	 * the whole period blindly is what "Select All" in the dialog does, and
	 * this way the cost is paid once, with the list in front of you. */
	/** Worked Hours is what the biometric read, so it stays read-only until
	 * asked for — a scanner that missed a clock-out is the case this is for.
	 * The lock itself is the child field's own read_only_depends_on, which is
	 * evaluated against this document; the grid only needs redrawing. */
	edit_worked_hours(frm) {
		frm.refresh_field("bulk_overtime_entries");
		if (frm.doc.edit_worked_hours) {
			frappe.show_alert({
				message: __("Worked Hours can now be typed straight into the table. Say why in Reason for Manual Changes."),
				indicator: "orange",
			});
		}
	},

	get_from_overtime_request(frm) {
		if (!frm.doc.company) {
			frappe.msgprint({
				title: __("Missing Details"),
				indicator: "red",
				message: __("Set the <b>Company</b> first."),
			});
			return;
		}

		const stop = bo_progress(frm, __("Get Overtime"), __("Looking for approved requests..."));
		bo_busy(frm, "get_from_overtime_request", true);
		frm.call({
			doc: frm.doc,
			method: "get_approved_requests",
		})
		.then((r) => {
			stop();
			const requests = r.message || [];
			if (!requests.length) {
				frappe.msgprint({
					title: __("Nothing to Pick"),
					indicator: "orange",
					message: __(
						"No approved Overtime Request is waiting to be paid for this company — either there are none, or every one of them is already in a Bulk Overtime.",
					),
				});
				return;
			}
			frm.events.show_request_dialog(frm, requests);
		})
		.finally(() => {
			stop();
			bo_busy(frm, "get_from_overtime_request", false);
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
			const covered =
				request.payable_days === 1 ? __("1 day to pay") : __("{0} days to pay", [request.payable_days]);
			// the request runs past today when it is still being worked, and
			// only the part already worked can be paid
			const short = request.payable_days < request.days;

			return `
				<label class="checkbox" style="display:block; padding:8px 0; border-bottom:1px solid var(--border-color);">
					<input type="checkbox" class="ot-request" data-name="${bo_esc(request.name)}">
					<b>${bo_esc(request.name)}</b>
					<span class="text-muted">${bo_esc(request.request_for || "")}</span>
					${
						short
							? `<span class="indicator-pill orange" style="margin-left:6px;">${__(
									"payable to {0} so far",
									[frappe.datetime.str_to_user(request.payable_to)],
							  )}</span>`
							: ""
					}
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
		if (!frm.doc.company) {
			frappe.msgprint({
				title: __("Missing Details"),
				indicator: "red",
				message: __("Set the <b>Company</b> first."),
			});
			return;
		}

		const stop = bo_progress(frm, __("Get Overtime"), __("Checking requests against attendance..."));
		bo_busy(frm, "get_from_overtime_request", true);
		frm.call({
			doc: frm.doc,
			method: "get_overtime",
			args: { overtime_requests: overtime_requests || null },
		})
		.then((r) => {
			stop();
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
		})
		.finally(() => {
			stop();
			bo_busy(frm, "get_from_overtime_request", false);
		});
	},
});

frappe.ui.form.on("Bulk Overtime Entry", {
	/** Typing a figure is the whole statement: the row's marker follows the
	 * typing rather than having to be ticked first. It is what keeps the
	 * figure when the batch is fetched again. */
	working_hours(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (frm.doc.edit_worked_hours && !row.manual_working_hours) {
			frappe.model.set_value(cdt, cdn, "manual_working_hours", 1);
		}
	},
});
