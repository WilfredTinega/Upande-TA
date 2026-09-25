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

/** Why a fetch came back empty, in the attendance's own words.
 *
 * "No attendance" is rarely the whole truth: the days are usually there and
 * marked Absent or On Leave, and the biometric behind them may not have been
 * worked into attendance yet. Saying which sends people to the right place. */
const bo_nothing_to_pay = (result) => {
	const why = result.why || {};
	const lines = [
		__("Found <b>{0}</b> approved Overtime Request(s) covering <b>{1}</b> employee-day(s), none of them payable.", [
			result.approved_requests,
			result.days_without_attendance,
		]),
		__("Overtime is paid against attendance marked <b>Present</b>, with hours on it."),
	];

	const statuses = why.statuses || [];
	if (statuses.length) {
		lines.push(
			__("The attendance in this period says: {0}.", [
				statuses.map((s) => `<b>${bo_esc(s.status)}</b> ${s.days}`).join(", "),
			]),
		);
	} else if (why.employees) {
		lines.push(__("There is no attendance at all for these <b>{0}</b> employee(s) in this period.", [why.employees]));
	}

	if (why.checkins) {
		lines.push(
			__(
				"There are <b>{0}</b> biometric check-in(s) in this period, so the attendance for these days may not have been processed yet.",
				[why.checkins],
			),
		);
	}
	return lines.join("<br><br>");
};

const CHECK_INDICATOR = {
	Agrees: "green",
	Differs: "orange",
	"Entered by Hand": "blue",
	"One Punch": "red",
	"No Punches": "red",
};

/** "06:55", or "02:10 +1" when the punch falls on the next day. */
const bo_clock = (value, date) => {
	if (!value) return "";
	const [day, time] = String(value).split(" ");
	const clock = (time || "").slice(0, 5);
	return day === date ? clock : `${clock} +1`;
};

const bo_hours = (value) => (value ? format_number(value, null, 2) : "0");

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

	verify_check_ins(frm) {
		frm.events.verify_checkins(frm);
	},

	verify_checkins(frm) {
		const stop = bo_progress(frm, __("Verify Check-ins"), __("Reading check-ins..."));
		frm.call({ doc: frm.doc, method: "verify_checkins" })
			.then((r) => {
				stop();
				frm.events.show_verification(frm, r.message || []);
			})
			.finally(stop);
	},

	show_verification(frm, rows) {
		const counts = {};
		rows.forEach((row) => (counts[row.check] = (counts[row.check] || 0) + 1));
		const summary = Object.keys(CHECK_INDICATOR)
			.filter((check) => counts[check])
			.map(
				(check) =>
					`<span class="indicator-pill ${CHECK_INDICATOR[check]}" style="margin-right:6px;">${__(
						check,
					)}: ${counts[check]}</span>`,
			)
			.join("");

		const body = rows
			.map((row) => {
				const date = row.overtime_date;
				const shift =
					row.shift_start && row.shift_end
						? `${String(row.shift_start).slice(0, 5)}–${String(row.shift_end).slice(0, 5)}`
						: "";
				return `
					<tr data-check="${bo_esc(row.check)}">
						<td>${row.idx}</td>
						<td><b>${bo_esc(row.employee_name || row.employee)}</b><div class="text-muted small">${bo_esc(
							row.employee,
						)}</div></td>
						<td>${frappe.datetime.str_to_user(date)}<div class="text-muted small">${bo_esc(
							__(row.day_type || ""),
						)}</div></td>
						<td>${bo_esc(shift)}<div class="text-muted small">${bo_hours(row.shift_hours)} h</div></td>
						<td>${bo_esc(bo_clock(row.first_in, date))}</td>
						<td>${bo_esc(bo_clock(row.last_out, date))}</td>
						<td class="text-right">${row.punches}</td>
						<td class="text-right">${bo_hours(row.punch_hours)}</td>
						<td class="text-right"><b>${bo_hours(row.beyond_shift)}</b></td>
						<td class="text-right">${bo_hours(row.requested_hours)}</td>
						<td class="text-right"><b>${bo_hours(row.approved_hours)}</b></td>
						<td><span class="indicator-pill ${CHECK_INDICATOR[row.check] || "gray"}">${__(
							row.check,
						)}</span></td>
					</tr>`;
			})
			.join("");

		const dialog = new frappe.ui.Dialog({
			title: __("Verify Check-ins"),
			size: "extra-large",
			fields: [
				{ fieldtype: "HTML", fieldname: "summary" },
				{ fieldtype: "HTML", fieldname: "table" },
			],
		});
		dialog.get_field("summary").$wrapper.html(`<div style="margin-bottom:8px;">${summary}</div>`);
		dialog.get_field("table").$wrapper.html(`
			<div style="max-height:65vh; overflow:auto;">
				<table class="table table-bordered table-sm" style="font-size:12px; margin:0;">
					<thead style="position:sticky; top:0; background:var(--card-bg); z-index:1;">
						<tr>
							<th>#</th>
							<th>${__("Employee")}</th>
							<th>${__("Date")}</th>
							<th>${__("Shift")}</th>
							<th>${__("First In")}</th>
							<th>${__("Last Out")}</th>
							<th class="text-right">${__("Punches")}</th>
							<th class="text-right">${__("Hours Worked")}</th>
							<th class="text-right">${__("Beyond Shift")}</th>
							<th class="text-right">${__("Requested")}</th>
							<th class="text-right">${__("Approved")}</th>
							<th>${__("Check")}</th>
						</tr>
					</thead>
					<tbody>${body}</tbody>
				</table>
			</div>`);
		dialog.show();
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
	/** Worked is the attendance and always read-only. Biometric — the
	 * overtime the punches support — opens for typing when this is ticked,
	 * for a day the scanner failed. The lock is the child field's own
	 * read_only_depends_on, evaluated against this document; the grid only
	 * needs redrawing. */
	edit_worked_hours(frm) {
		frm.refresh_field("bulk_overtime_entries");
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
			// an earlier batch paid its first days; this one takes the rest
			const resumed = request.payable_from && request.payable_from !== request.overtime_date;

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
					${
						resumed
							? `<span class="indicator-pill blue" style="margin-left:6px;">${__(
									"remaining from {0}",
									[frappe.datetime.str_to_user(request.payable_from)],
							  )}</span>`
							: ""
					}
					<div class="text-muted small" style="margin-left:22px;">
						${bo_esc(span)} · ${covered}<br>
						${__("{0} employee(s)", [request.employees])} ·
						${
							request.request_for === "Week"
								? __("{0} h for the week in total", [request.hours_per_day])
								: __("{0} h/day in total", [request.hours_per_day])
						}
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
					: bo_nothing_to_pay(result);
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
	biometric_hours(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (frm.doc.edit_worked_hours && !row.manual_biometric_hours) {
			frappe.model.set_value(cdt, cdn, "manual_biometric_hours", 1);
		}
	},
});
