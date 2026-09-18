// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

/**
 * Holiday Assignment Tool — employee selection and the two run actions.
 *
 * This is a Single, like HRMS's Shift Assignment Tool, which it borrows its
 * DataTable handling and its whole action flow from: the form never saves, the
 * toolbar has no Save/Submit, and the primary action button does the work. The
 * `action` field picks which: "Assign Holidays" calls assign_holidays(),
 * "Undo Assignment" calls undo_assignment(). That select is the entire reason
 * reversal is still reachable after on_cancel stopped existing.
 *
 * Where it departs from the shift tool is the employee list. That tool posts
 * the checked rows straight to a bulk action and stores nothing. This one keeps
 * its own `employees` child table — a Single's child rows persist perfectly
 * well, with parent = the doctype name — because each row carries the employee's
 * prior holiday list and, after a run, the `assignments_json` backlink the undo
 * action unwinds from. The rows are written by the form and saved server-side
 * by assign_holidays(); the user can still edit, delete or add rows by hand
 * before running.
 *
 * Which produces the one rule this file exists to enforce:
 *
 *   THE CHILD TABLE IS ONLY EVER REPLACED BY AN EXPLICIT, CONFIRMED FETCH.
 *
 * Bulk Week Off re-fetches on every filter keystroke and calls clear_table()
 * with the result, so any manual edit is destroyed the moment someone adjusts a
 * filter. Here, changing a filter does nothing but re-scope the link fields;
 * employees only move into the table when the user presses "Get Employees",
 * ticks rows, and confirms — and if the table already has rows, the replacement
 * is confirmed a second time.
 */

const FETCH_METHOD = "upande_ta.upande_ta.api.holiday_assignment_employees.get_holiday_assignment_employees";

/**
 * Realtime events published by holiday_assignment_tool.py when a run finishes.
 * Keep these two strings in step with ASSIGN_EVENT / UNDO_EVENT there.
 *
 * Why they matter: at or below BATCH_THRESHOLD (30) employees the work runs
 * inline inside the request the primary action fired, and the controller's
 * msgprints are the answer. Above it the work is enqueued, the request returns
 * immediately, and the msgprints are raised in a background worker where nobody
 * sees them — these events are the only thing that reaches the user.
 */
const ASSIGN_EVENT = "completed_holiday_assignment_tool";
const UNDO_EVENT = "completed_holiday_assignment_tool_cancellation";

/**
 * Employees listed individually before a table collapses to a count. Mirrors
 * MAX_NAMES_IN_MESSAGE in the controller: a 500-employee run must not render
 * 500 rows into a msgprint.
 */
const MAX_ROWS_IN_MESSAGE = 20;

/**
 * Deliberately NOT hrms.notify_bulk_action_status.
 *
 * `hrms` is on the global namespace and hrms.bundle.js is in hrms' own
 * app_include_js, so on a site that has hrms installed (upande_ta declares it
 * in required_apps, so that is every site) the helper does exist. It is still
 * the wrong thing to call here, for two reasons that are about the payload
 * rather than about availability:
 *
 *   * it renders `failure` with frappe.utils.comma_and(), i.e. it assumes a
 *     list of plain strings. This controller sends objects carrying a per
 *     employee `reason`, which is the single most useful thing in the payload
 *     and which comma_and() would render as "[object Object]";
 *   * it has no notion of a `skipped` bucket at all, and skipped employees
 *     (no prior Holiday List Assignment, so nothing to restore them to) are a
 *     first-class outcome of this tool, not an error.
 *
 * So the three buckets are rendered locally. That also keeps this form working
 * if a future hrms renames or reshapes those helpers.
 */
const esc = (value) =>
	value === null || value === undefined || value === ""
		? ""
		: frappe.utils.escape_html(String(value));

const entry_label = (entry) => esc(entry.employee_name || entry.employee);

function truncation_note(entries) {
	const remaining = entries.length - MAX_ROWS_IN_MESSAGE;
	if (remaining <= 0) return "";
	return `<p class="text-muted small">${__("... and {0} more.", [remaining])}</p>`;
}

function build_success_html(success, cancelling) {
	const created = success.reduce((total, entry) => total + (cint(entry.count) || 0), 0);

	let html = `<p>${
		cancelling
			? __("Cancelled <b>{0}</b> Holiday List Assignment(s) for <b>{1}</b> employee(s).", [
					created,
					success.length,
				])
			: __("Created <b>{0}</b> Holiday List Assignment(s) for <b>{1}</b> employee(s).", [
					created,
					success.length,
				])
	}</p>`;

	html += `<table class="table table-bordered"><tr><th>${__("Employee")}</th><th>${__(
		"Holiday List Assignments",
	)}</th></tr>`;
	for (const entry of success.slice(0, MAX_ROWS_IN_MESSAGE)) {
		// entry.doc is a server-built get_link_to_form() anchor to the first
		// assignment of the chain, so it is inserted as markup on purpose;
		// everything else on the entry is escaped.
		const detail = entry.doc
			? `${cint(entry.count) || 0} &middot; ${entry.doc}`
			: String(cint(entry.count) || 0);
		html += `<tr><td>${entry_label(entry)}</td><td>${detail}</td></tr>`;
	}
	html += "</table>";

	return html + truncation_note(success);
}

function build_failure_html(failure) {
	let html = `<p>${__("<b>{0}</b> employee(s) failed.", [failure.length])}</p>`;
	html += `<table class="table table-bordered"><tr><th>${__("Employee")}</th><th>${__(
		"Reason",
	)}</th></tr>`;
	for (const entry of failure.slice(0, MAX_ROWS_IN_MESSAGE)) {
		html += `<tr><td>${entry_label(entry)}</td><td>${esc(entry.reason)}</td></tr>`;
	}
	html += "</table>";
	html += `<p class="text-muted small">${__(
		"Check <a href='/app/List/Error Log?reference_doctype=Holiday List Assignment'>{0}</a> for more details",
		[__("Error Log")],
	)}</p>`;

	return html + truncation_note(failure);
}

function build_skipped_html(skipped, cancelling, to_date) {
	const names = skipped.slice(0, MAX_ROWS_IN_MESSAGE).map(entry_label).join(", ");
	const period_end = to_date ? frappe.datetime.str_to_user(to_date) : "";

	let html = `<p>${
		cancelling
			? __("<b>{0}</b> employee(s) had no recorded Holiday List Assignment to cancel:", [
					skipped.length,
				])
			: __(
					"Skipped <b>{0}</b> employee(s) with no previous Holiday List Assignment — there is nothing to restore them to after <b>{1}</b>, and guessing a list would move them somewhere they have never been. Give them a Holiday List Assignment first, then run this tool again for them:",
					[skipped.length, period_end],
				)
	}</p>`;
	html += `<p>${names}</p>`;

	return html + truncation_note(skipped);
}

frappe.ui.form.on("Holiday Assignment Tool", {
	setup(frm) {
		frm.set_query("department", function () {
			return frm.doc.company ? { filters: { company: frm.doc.company } } : {};
		});

		// No set_query on holiday_list: Holiday List has no company field, so
		// filtering it by company would send an unknown-column filter and break
		// the link search outright.

		// custom_farm is a custom field (Unit/Division) and may be absent on a
		// site that does not install it, so never assume the control exists.
		if (frm.fields_dict.custom_farm) {
			frm.set_query("custom_farm", function () {
				return frm.doc.company ? { filters: { company: frm.doc.company } } : {};
			});
		}
	},

	refresh(frm) {
		// A Single tool form: nothing to save, so the toolbar's Save button and
		// the draft/submitted indicator are both noise. The primary action does
		// the work instead, exactly as in shift_assignment_tool.js.
		frm.page.clear_indicator();
		frm.disable_save();
		frm.trigger("set_primary_action");
		frm.trigger("add_fetch_button");
		frm.events.listen_for_completion(frm);
	},

	action(frm) {
		frm.trigger("set_primary_action");
	},

	// ── Primary action ───────────────────────────────────────────────────────

	/**
	 * The `action` select is the whole reversal story. A submittable document
	 * got its undo for free from Cancel; a Single has no docstatus and no
	 * on_cancel, so the same button is repointed at the other whitelisted
	 * method instead.
	 *
	 * clear_primary_action() first, because refresh runs again on every
	 * reload_doc and set_primary_action() would otherwise leave the previous
	 * label in place. Custom buttons are deliberately NOT cleared here — that
	 * would take "Get Employees" with them.
	 */
	set_primary_action(frm) {
		frm.page.clear_primary_action();

		if (frm.doc.action === "Undo Assignment") {
			frm.page.set_primary_action(__("Undo Assignment"), () => {
				frm.events.undo_assignment(frm);
			});
			return;
		}

		// Anything else, including a Single that predates the field and has no
		// value stored, is the assign case — the controller defaults it the
		// same way.
		frm.page.set_primary_action(__("Assign Holidays"), () => {
			frm.events.assign_holidays(frm);
		});
	},

	assign_holidays(frm) {
		const employees = frm.doc.employees || [];

		if (!employees.length) {
			frappe.msgprint({
				title: __("No Employees"),
				indicator: "red",
				message: __("Click <b>Get Employees</b> and add at least one employee first."),
			});
			return;
		}

		frappe.confirm(
			__("Assign <b>{0}</b> to {1} employee(s)?", [
				frappe.utils.escape_html(frm.doc.holiday_list || ""),
				employees.length,
			]),
			() => {
				// doc: frm.doc posts the in-memory document, child rows and all,
				// to run_doc_method. The controller saves it before it writes
				// anything, which is what gives those rows the database identity
				// `assignments_json` is later written back onto.
				frm.call({
					method: "assign_holidays",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Assigning Holidays..."),
				});
			},
		);
	},

	undo_assignment(frm) {
		const employees = frm.doc.employees || [];

		if (!employees.length) {
			frappe.msgprint({
				title: __("Nothing to Undo"),
				indicator: "red",
				message: __("This tool has no employees recorded."),
			});
			return;
		}

		frappe.confirm(
			__(
				"Cancel the Holiday List Assignments this tool created for {0} employee(s)? Each employee goes back to the Holiday List they were on before.",
				[employees.length],
			),
			() => {
				frm.call({
					method: "undo_assignment",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Undoing Assignment..."),
				});
			},
		);
	},

	// ── Realtime completion ──────────────────────────────────────────────────

	/**
	 * Subscribe to the controller's two completion events.
	 *
	 * frappe.realtime.off(event) BEFORE .on(event, ...) is not optional.
	 * `refresh` runs on every reload_doc and on every route back onto the form
	 * — and this form reloads itself at the end of every run — while
	 * frappe.realtime.on() appends: without the off() the handlers stack and one
	 * finished run raises the same dialog three or four times. Same order, for
	 * the same reason, as hrms.handle_realtime_bulk_action_notification.
	 *
	 * off() with no callback drops every handler for the event. On a Single
	 * there is only ever one such form in a desk session, so that is simply the
	 * previous subscription of this same form.
	 */
	listen_for_completion(frm) {
		[
			[ASSIGN_EVENT, false],
			[UNDO_EVENT, true],
		].forEach(([event, cancelling]) => {
			frappe.realtime.off(event);
			frappe.realtime.on(event, (message) => {
				frm.events.notify_completion(frm, message || {}, cancelling);
			});
		});
	},

	/**
	 * Render the three buckets the controller reports, then reload.
	 *
	 * The reload is the point of the exercise as much as the dialog is: the
	 * background worker writes `assignments_json` onto each employee row with
	 * db_set(), so the form in front of the user still holds the pre-run copy
	 * and shows no backlink to anything that was created until it re-reads.
	 */
	notify_completion(frm, message, cancelling) {
		// publish_realtime() with a doctype and no docname falls through to the
		// site room, so this fires on every open desk session. The filter is
		// kept: on a Single, frm.doc.name is the doctype name itself and the
		// controller sends that same value as `docname`, so it matches and every
		// session watching the tool is told — which is right, because there is
		// only one Holiday Assignment Tool and they are all watching the run
		// that just finished. What it still buys is rejecting a payload that is
		// not about this document at all. As before, a payload with no name is
		// tolerated rather than dropped, so an older server still notifies.
		if (message.docname && message.docname !== frm.doc.name) return;

		const success = message.success || [];
		const failure = message.failure || [];
		const skipped = message.skipped || [];

		if (!success.length && !failure.length && !skipped.length) return;

		let title = __("Success");
		let indicator = "green";
		if (failure.length) {
			title = success.length ? __("Partial Success") : __("Failure");
			indicator = success.length ? "orange" : "red";
		} else if (skipped.length) {
			title = success.length ? __("Partial Success") : __("Skipped Employees");
			indicator = "orange";
		}

		const sections = [];
		if (success.length) sections.push(build_success_html(success, cancelling));
		if (skipped.length) sections.push(build_skipped_html(skipped, cancelling, frm.doc.to_date));
		if (failure.length) sections.push(build_failure_html(failure));

		frappe.msgprint({
			title: title,
			indicator: indicator,
			message: sections.join("<hr>"),
			is_minimizable: true,
		});

		frm.reload_doc();
	},

	add_fetch_button(frm) {
		frm.add_custom_button(__("Get Employees"), () => {
			frm.events.fetch_employees(frm);
		}).addClass("btn-primary-light");
	},

	// ── Filter changes ───────────────────────────────────────────────────────
	// These re-scope the dependent link fields and nothing else. They must never
	// touch frm.doc.employees.

	/**
	 * The `clear_filters` button. Resets the narrowing filters only — company
	 * stays (it is mandatory and scopes everything else), and the employee rows
	 * already gathered stay too. Clearing a filter is not a request to throw
	 * away work.
	 */
	clear_filters(frm) {
		if (frm.fields_dict.custom_farm) frm.set_value("custom_farm", "");
		frm.set_value("department", "");
		frm.set_value("designation", "");
	},

	company(frm) {
		if (frm.doc.department) frm.set_value("department", "");
		if (frm.fields_dict.custom_farm && frm.doc.custom_farm) frm.set_value("custom_farm", "");
		frm.events.warn_filters_changed(frm);
	},

	custom_farm(frm) {
		frm.events.warn_filters_changed(frm);
	},

	department(frm) {
		frm.events.warn_filters_changed(frm);
	},

	designation(frm) {
		frm.events.warn_filters_changed(frm);
	},

	from_date(frm) {
		if (frm.doc.to_date && frm.doc.from_date > frm.doc.to_date) frm.set_value("to_date", null);
		frm.events.warn_filters_changed(frm);
	},

	to_date(frm) {
		if (frm.doc.from_date && frm.doc.to_date && frm.doc.to_date < frm.doc.from_date) {
			frm.set_value("to_date", null);
			return;
		}
		frm.events.warn_filters_changed(frm);
	},

	/**
	 * Say out loud that the rows already in the table are stale — rather than
	 * quietly refetching over them, which is the Bulk Week Off behaviour this
	 * form is correcting. Throttled so a burst of filter edits shows one alert.
	 */
	warn_filters_changed(frm) {
		if (!(frm.doc.employees || []).length) return;
		if (frm._filter_warning_pending) return;

		frm._filter_warning_pending = true;
		setTimeout(() => {
			frm._filter_warning_pending = false;
			frappe.show_alert({
				message: __(
					"Filters changed. The {0} employee row(s) already added were kept — click <b>Get Employees</b> to replace them.",
					[(frm.doc.employees || []).length],
				),
				indicator: "orange",
			});
		}, 600);
	},

	// ── Fetch ────────────────────────────────────────────────────────────────

	fetch_employees(frm) {
		const missing = [];
		if (!frm.doc.company) missing.push(__("Company"));
		if (!frm.doc.from_date) missing.push(__("From Date"));

		if (missing.length) {
			frappe.msgprint({
				title: __("Missing Filters"),
				indicator: "red",
				message: __("Please set {0} before fetching employees.", [
					frappe.utils.comma_and(missing),
				]),
			});
			return;
		}

		frappe.call({
			method: FETCH_METHOD,
			args: {
				company: frm.doc.company,
				from_date: frm.doc.from_date,
				to_date: frm.doc.to_date || null,
				custom_farm: frm.doc.custom_farm || null,
				department: frm.doc.department || null,
				designation: frm.doc.designation || null,
			},
			freeze: true,
			freeze_message: __("Fetching Employees..."),
			callback(r) {
				const result = r.message || {};
				const employees = result.employees || [];

				if (!employees.length) {
					frappe.msgprint({
						title: __("No Employees Found"),
						indicator: "orange",
						message: __(
							"No active employee matches these filters for {0}.<br><br>Employees who already have a submitted Holiday List Assignment starting on that date are excluded, because a second one for the same date cannot be created.",
							[frappe.datetime.str_to_user(frm.doc.from_date)],
						),
					});
					return;
				}

				if (result.truncated) {
					frappe.msgprint({
						title: __("Too Many Employees"),
						indicator: "orange",
						message: __(
							"<b>{0}</b> employees match these filters, but only the first <b>{1}</b> are shown.<br><br>Narrow the filters — by Unit/Division, Department or Designation — and fetch again, or assign them in batches. Anything past the first {1} will not be added.",
							[result.total, result.limit],
						),
					});
				}

				frm.events.show_selection_dialog(frm, result);
			},
		});
	},

	show_selection_dialog(frm, result) {
		const employees = result.employees || [];

		if (frm._employee_selection_dialog) {
			try {
				frm._employee_selection_dialog.hide();
			} catch (e) {
				// dialog already torn down; nothing to do
			}
		}

		const dialog = new frappe.ui.Dialog({
			title: __("Select Employees ({0})", [employees.length]),
			size: "extra-large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "summary",
				},
				{
					fieldtype: "HTML",
					fieldname: "employees_table",
				},
			],
			primary_action_label: __("Add Selected to Table"),
			primary_action() {
				const selected = frm.events.get_checked_rows(dialog);

				if (!selected.length) {
					frappe.msgprint({
						title: __("No Employees Selected"),
						indicator: "red",
						message: __("Tick at least one employee to add."),
					});
					return;
				}

				const existing = (frm.doc.employees || []).length;
				if (existing) {
					frappe.confirm(
						__(
							"This replaces the {0} employee row(s) currently in the table with the {1} selected. Any manual edits to those rows will be lost. Continue?",
							[existing, selected.length],
						),
						() => {
							frm.events.set_employees(frm, selected);
							dialog.hide();
						},
					);
					return;
				}

				frm.events.set_employees(frm, selected);
				dialog.hide();
			},
		});

		dialog.get_field("summary").$wrapper.html(frm.events.get_summary_html(frm, result));

		dialog.show();
		frm._employee_selection_dialog = dialog;

		// The DataTable measures its own width, so it has to be built after the
		// modal is actually laid out; building it inline against a fading-in
		// dialog gives every column zero width.
		setTimeout(() => {
			frm.events.render_datatable(frm, dialog, employees);
		}, 150);
	},

	get_summary_html(frm, result) {
		const scope = [];
		if (frm.doc.company)
			scope.push(__("Company") + ": " + frappe.utils.escape_html(frm.doc.company));
		if (frm.doc.custom_farm)
			scope.push(__("Unit/Division") + ": " + frappe.utils.escape_html(frm.doc.custom_farm));
		if (frm.doc.department)
			scope.push(__("Department") + ": " + frappe.utils.escape_html(frm.doc.department));
		if (frm.doc.designation)
			scope.push(__("Designation") + ": " + frappe.utils.escape_html(frm.doc.designation));

		let html = `<div class="text-muted small" style="margin-bottom: 8px;">
			${scope.join(" &middot; ")}
		</div>`;

		if (result.truncated) {
			html += `<div class="alert alert-warning" style="padding: 8px 12px; margin-bottom: 8px;">
				${__("Showing the first <b>{0}</b> of <b>{1}</b> matching employees. Narrow the filters and fetch again to reach the rest.", [result.count, result.total])}
			</div>`;
		}

		return html;
	},

	render_datatable(frm, dialog, employees) {
		const $wrapper = dialog.get_field("employees_table").$wrapper;
		$wrapper.empty();

		const $container = $(`<div class="bulk-holiday-employees" style="min-height: 300px;"></div>`).appendTo(
			$wrapper,
		);

		const escape = (value) =>
			value === null || value === undefined || value === ""
				? ""
				: frappe.utils.escape_html(String(value));

		const columns = [
			{ id: "employee", name: "employee", content: __("Employee"), width: 120 },
			{ id: "employee_name", name: "employee_name", content: __("Employee Name"), width: 200 },
			{ id: "custom_farm", name: "custom_farm", content: __("Unit/Division"), width: 140 },
			{ id: "department", name: "department", content: __("Department"), width: 160 },
			{ id: "designation", name: "designation", content: __("Designation"), width: 150 },
			{
				id: "prior_holiday_list",
				name: "prior_holiday_list",
				content: __("Current Holiday List"),
				width: 200,
			},
		].map((column) => ({
			...column,
			editable: false,
			focusable: false,
			dropdown: false,
			align: "left",
			format: (value) => escape(value),
		}));

		dialog.employees_datatable = new frappe.DataTable($container.get(0), {
			columns: columns,
			data: employees,
			checkboxColumn: true,
			checkedRowStatus: false,
			serialNoColumn: false,
			inlineFilters: true,
			layout: "fluid",
			cellHeight: 35,
			disableReorderColumn: true,
			noDataMessage: __("No employees to show."),
		});

		// Everything the fetch returned is already filtered to assignable
		// employees, so pre-ticking them is the useful default; unticking a few
		// is less work than ticking four hundred.
		try {
			dialog.employees_datatable.rowmanager.checkAll(true);
		} catch (e) {
			// older datatable builds expose no checkAll; the user ticks manually
		}
	},

	get_checked_rows(dialog) {
		const datatable = dialog.employees_datatable;
		if (!datatable) return [];

		const rows = datatable.datamanager.data || [];
		const checked = datatable.rowmanager.getCheckedRows() || [];

		return checked
			.map((index) => (typeof index === "object" ? index : rows[index]))
			.filter((row) => row && row.employee);
	},

	/**
	 * Replace `employees` with the confirmed selection. This is the ONLY place
	 * in this file that writes to the child table.
	 */
	set_employees(frm, selected) {
		frm.clear_table("employees");

		selected.forEach((employee) => {
			const row = frm.add_child("employees");
			row.employee = employee.employee;
			row.employee_name = employee.employee_name;
			row.department = employee.department;
			row.designation = employee.designation;
			row.custom_farm = employee.custom_farm;
			row.prior_holiday_list = employee.prior_holiday_list;
		});

		frm.refresh_field("employees");

		frappe.show_alert({
			message: __(
				"{0} employee(s) added. Press <b>Assign Holidays</b> to run — the rows are stored as part of that.",
				[selected.length],
			),
			indicator: "green",
		});
	},
});
