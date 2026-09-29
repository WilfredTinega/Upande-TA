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

/** frm.call hands back a jQuery promise, and jQuery deferreds have no
 * .finally(): chained straight onto one it throws, so a failed call never
 * stops its bar or frees its button. Promise.resolve() makes it a real one. */
const bo_call = (frm, opts) => Promise.resolve(frm.call({ doc: frm.doc, ...opts }));

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
	const lines = [];
	if (result.days_without_attendance || !result.days_on_leave) {
		lines.push(
			__("Found <b>{0}</b> approved Overtime Request(s) covering <b>{1}</b> employee-day(s), none of them payable.", [
				result.approved_requests,
				result.days_without_attendance,
			]),
			__("Overtime is paid against attendance marked <b>Present</b>, with hours on it."),
		);
	}
	if (result.days_on_leave) {
		lines.push(
			__("<b>{0}</b> employee-day(s) on leave were left out: someone on leave is not paid overtime.", [
				result.days_on_leave,
			]),
		);
	}

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

		// Create > Bulk Overtime on an Hours Budget request lands here, to
		// choose who it pays
		const pending = frappe._bo_budget_request;
		if (pending && frm.is_new() && frm.doc.company) {
			frappe._bo_budget_request = null;
			frm.events.choose_budget_employees(frm, [pending]);
		}

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
		bo_call(frm, { method: "verify_checkins" })
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

		const totals = {};
		rows.forEach((row) => {
			const key = `${row.employee}|${row.overtime_request || ""}`;
			const t = (totals[key] ||= {
				employee: row.employee,
				employee_name: row.employee_name,
				overtime_request: row.overtime_request,
				week: row.week_requested_hours,
				elsewhere: flt(row.week_paid_elsewhere),
				requested: 0,
				beyond: 0,
				approved: 0,
				days: 0,
			});
			t.requested += flt(row.requested_hours);
			t.beyond += flt(row.beyond_shift);
			t.approved += flt(row.approved_hours);
			t.days += 1;
		});
		const weekly = Object.values(totals)
			.map((t) => {
				const requested = t.week === null || t.week === undefined ? t.requested : flt(t.week);
				const remaining = Math.max(requested - t.elsewhere - t.approved, 0);
				const pill =
					t.approved + t.elsewhere >= requested - 0.005
						? "green"
						: t.approved > 0
							? "orange"
							: "red";
				return `
					<tr>
						<td><b>${bo_esc(t.employee_name || t.employee)}</b><div class="text-muted small">${bo_esc(
							t.employee,
						)}</div></td>
						<td>${bo_esc(t.overtime_request || "")}</td>
						<td class="text-right">${t.days}</td>
						<td class="text-right">${bo_hours(requested)}</td>
						<td class="text-right">${bo_hours(t.elsewhere)}</td>
						<td class="text-right"><b>${bo_hours(t.beyond)}</b></td>
						<td class="text-right"><b>${bo_hours(t.approved)}</b></td>
						<td class="text-right"><span class="indicator-pill ${pill}">${bo_hours(
							remaining,
						)}</span></td>
					</tr>`;
			})
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
						)}</div>${
							row.day_off
								? `<span class="indicator-pill orange">${__("Rest day, off on {0}", [
										frappe.datetime.str_to_user(row.day_off),
									])}</span>`
								: ""
						}</td>
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
				{ fieldtype: "HTML", fieldname: "weekly" },
				{ fieldtype: "HTML", fieldname: "table" },
			],
		});
		dialog.get_field("summary").$wrapper.html(`<div style="margin-bottom:8px;">${summary}</div>`);
		dialog.get_field("weekly").$wrapper.html(`
			<div style="max-height:30vh; overflow:auto; margin-bottom:12px;">
				<table class="table table-bordered table-sm" style="font-size:12px; margin:0;">
					<thead style="position:sticky; top:0; background:var(--card-bg); z-index:1;">
						<tr>
							<th>${__("Employee")}</th>
							<th>${__("Overtime Request")}</th>
							<th class="text-right">${__("Days")}</th>
							<th class="text-right">${__("Requested")}</th>
							<th class="text-right">${__("Paid Before")}</th>
							<th class="text-right">${__("Beyond Shift")}</th>
							<th class="text-right">${__("Approved")}</th>
							<th class="text-right">${__("Remaining")}</th>
						</tr>
					</thead>
					<tbody>${weekly}</tbody>
				</table>
			</div>`);
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
		bo_call(frm, {
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
		// a budget is approved for units and departments, so they are what it
		// is; a named request is its people, and says so already. Either
		// can span many, and the first few say enough.
		const listed = (label, names) => {
			if (!(names || []).length) return "";
			const shown = names.slice(0, 3).map(bo_esc).join(", ");
			const more = names.length > 3 ? ` ${__("+{0} more", [names.length - 3])}` : "";
			return `${label}: ${shown}${more}<br>`;
		};
		const departments = (request) =>
			request.request_mode === "Hours Budget"
				? listed(__("Unit/Division"), request.units) + listed(__("Department"), request.departments)
				: "";
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
					<span class="text-muted">${bo_esc(
						request.request_for === "Week" && request.week
							? `${request.week}, ${request.week_year}`
							: request.request_for || "",
					)}</span>
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
						${departments(request)}
						${
							request.request_mode === "Hours Budget"
								? __("Hours Budget: {0} h, {1} h left", [
										request.hours_per_day,
										request.budget_left,
								  ])
								: `${__("{0} employee(s)", [request.employees])} · ${
										request.request_for === "Week"
											? __("{0} h for the week in total", [request.hours_per_day])
											: __("{0} h/day in total", [request.hours_per_day])
								  }`
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
				const budgeted = requests.some(
					(r) => r.request_mode === "Hours Budget" && picked.includes(r.name),
				);
				if (budgeted) frm.events.choose_budget_employees(frm, picked);
				else frm.events.fetch_overtime(frm, picked);
			},
		});

		dialog.get_field("requests").$wrapper.html(requests.map(line).join(""));
		dialog.show();
	},

	/** An Hours Budget names nobody, so HR ticks who it pays: everyone the
	 * budget covers whose attendance shows overtime, with their days and
	 * hours, against what is left of each budget. */
	choose_budget_employees(frm, overtime_requests) {
		const stop = bo_progress(frm, __("Get Overtime"), __("Finding who worked overtime..."));
		bo_busy(frm, "get_from_overtime_request", true);
		bo_call(frm, {
			method: "get_budget_candidates",
			args: { overtime_requests },
		})
		.then((r) => {
			stop();
			const { employees = [], budgets = [] } = r.message || {};
			if (!employees.length) {
				frappe.msgprint({
					title: __("Nobody to Choose"),
					indicator: "orange",
					message: __(
						"Nobody the budget covers has overtime in the attendance for this period that is not already being paid.",
					),
				});
				// named requests picked alongside are still paid
				frm.events.fetch_overtime(frm, overtime_requests, []);
				return;
			}
			frm.events.show_budget_dialog(frm, overtime_requests, employees, budgets);
		})
		.finally(() => {
			stop();
			bo_busy(frm, "get_from_overtime_request", false);
		});
	},

	show_budget_dialog(frm, overtime_requests, employees, budgets) {
		const budget_lines = budgets
			.map((b) =>
				__("<b>{0}</b> ({1}): {2} h left of {3} h{4}", [
					bo_esc(b.scope),
					bo_esc(b.parent),
					flt(b.hours_left || b.budget_hours),
					flt(b.budget_hours),
					cint(b.max_employees) ? __(", up to {0} employees", [b.max_employees]) : "",
				]),
			)
			.join("<br>");

		const dialog = new frappe.ui.Dialog({
			title: __("Choose Who to Pay ({0})", [employees.length]),
			size: "extra-large",
			fields: [
				{ fieldtype: "HTML", fieldname: "budgets" },
				{ fieldtype: "HTML", fieldname: "selected" },
				{ fieldtype: "HTML", fieldname: "employees_table" },
			],
			primary_action_label: __("Add Selected"),
			primary_action() {
				const chosen = checked().map((e) => e.employee);
				if (!chosen.length) {
					frappe.msgprint(__("Tick at least one employee."));
					return;
				}
				dialog.hide();
				frm.events.fetch_overtime(frm, overtime_requests, chosen);
			},
		});

		const checked = () => {
			const table = dialog.employees_datatable;
			if (!table) return [];
			const rows = table.datamanager.data || [];
			return (table.rowmanager.getCheckedRows() || [])
				.map((index) => (typeof index === "object" ? index : rows[index]))
				.filter((row) => row && row.employee);
		};
		const show_selected = () => {
			const rows = checked();
			const hours = rows.reduce((sum, row) => sum + flt(row.hours), 0);
			dialog
				.get_field("selected")
				.$wrapper.html(
					`<div class="text-muted" style="margin:8px 0;">${__(
						"Selected: <b>{0}</b> employee(s), <b>{1}</b> h",
						[rows.length, Math.round(hours * 100) / 100],
					)}</div>`,
				);
		};

		dialog
			.get_field("budgets")
			.$wrapper.html(`<div class="alert alert-info" style="padding:8px 12px;">${budget_lines}</div>`);
		dialog.show();

		// built after the modal is laid out, or every column measures 0 wide
		setTimeout(() => {
			const $wrapper = dialog.get_field("employees_table").$wrapper.empty();
			const $container = $(`<div style="min-height: 300px;"></div>`).appendTo($wrapper);
			const columns = [
				{ id: "employee", content: __("Employee"), width: 120 },
				{ id: "employee_name", content: __("Employee Name"), width: 200 },
				{ id: "custom_farm", content: __("Unit/Division"), width: 140 },
				{ id: "department", content: __("Department"), width: 160 },
				{ id: "budget_scope", content: __("Budget"), width: 180 },
				{ id: "days", content: __("Days"), width: 70 },
				{ id: "hours", content: __("Overtime (h)"), width: 110 },
			].map((column) => ({
				...column,
				name: column.id,
				editable: false,
				focusable: false,
				dropdown: false,
				align: ["days", "hours"].includes(column.id) ? "right" : "left",
				format: (value) => bo_esc(value),
			}));
			dialog.employees_datatable = new frappe.DataTable($container.get(0), {
				columns,
				data: employees,
				checkboxColumn: true,
				checkedRowStatus: false,
				serialNoColumn: false,
				inlineFilters: true,
				layout: "fluid",
				cellHeight: 35,
				disableReorderColumn: true,
				events: { onCheckRow: show_selected },
			});
			// the header's check-all box does not fire onCheckRow
			$container.on("click", ".dt-cell--col-0", () => setTimeout(show_selected, 0));
			show_selected();
		}, 150);
	},

	/** The fetch itself. Reached from the picker, and from Create > Bulk
	 * Overtime on an Overtime Request. Frappe calls a field handler as
	 * (frm, doctype, name), so no button may point here directly: its second
	 * argument would arrive as the doctype name. */
	fetch_overtime(frm, overtime_requests, budget_employees) {
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
		bo_call(frm, {
			method: "get_overtime",
			args: {
				overtime_requests: overtime_requests || null,
				// undefined, not null, when no budget was picked: null would
				// read as "nobody chosen" and leave a budget's rows out
				...(budget_employees ? { budget_employees } : {}),
			},
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
