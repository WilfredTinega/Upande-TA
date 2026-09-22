// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

/**
 * Holiday Assignment Tool — pick a Holiday List and dates, select employees,
 * assign. That is the whole form.
 *
 * A Single, like HRMS's Shift Assignment Tool: the form never saves, the
 * primary action does the work. There is no undo here on purpose — every run
 * leaves submitted Holiday List Assignment records, and those are cancelled
 * from their own list if a run has to be reversed. The Employees table is only
 * the work list for the next run; the server clears it when the run finishes.
 *
 * Company, Unit/Division, Department, Designation and a single-Employee
 * filter all live in the Select Employees dialog, not on the form. The chosen
 * Company is copied onto the form's hidden `company` field when employees are
 * added, because the run is validated against it.
 *
 * Nothing here freezes the desk. The employee fetch can run to thousands of
 * rows and the assignment run can take minutes; both report progress in place
 * and leave the rest of the page usable.
 */

const FETCH_METHOD =
	"upande_ta.upande_ta.api.holiday_assignment_employees.get_holiday_assignment_employees";

/** Realtime event published by holiday_assignment_tool.py when a run finishes.
 * Above 30 employees the run happens in a background worker and this event is
 * the only thing that reaches the user. Keep in step with ASSIGN_EVENT there. */
const ASSIGN_EVENT = "completed_holiday_assignment_tool";

/** Employees listed individually before a result table collapses to a count. */
const MAX_ROWS_IN_MESSAGE = 20;

const esc = (value) =>
	value === null || value === undefined || value === ""
		? ""
		: frappe.utils.escape_html(String(value));

const entry_label = (entry) => esc(entry.employee_name || entry.employee);

/** An inline bar for work whose length cannot be known in advance: it sweeps
 * towards the end rather than pretending to measure, and leaves the rest of
 * the page usable — which a freeze does not. Returns a stop(). */
function inline_progress($area, label) {
	$area.html(`
		<div class="ha-progress" style="margin-bottom:8px;">
			<div class="text-muted small" style="margin-bottom:4px;">${esc(label)}</div>
			<div class="progress" style="margin:0;">
				<div class="progress-bar" style="width:8%; transition:width .4s ease;"></div>
			</div>
		</div>`);
	const $bar = $area.find(".progress-bar");
	let width = 8;
	let stopped = false;
	const timer = setInterval(() => {
		width = Math.min(92, width + (92 - width) / 6);
		$bar.css("width", `${width}%`);
	}, 400);
	return () => {
		if (stopped) return;
		stopped = true;
		clearInterval(timer);
		$bar.css("width", "100%");
	};
}

/** The filter row has to paint over the DataTable below it: the table's sticky
 * checkbox column, its header and its column menus all carry z-indexes of
 * their own (up to 10), and a suggestion list that loses to them is drawn
 * behind the rows. Lifting the whole filter section into its own stacking
 * context above them puts every dropdown on top, whichever field it belongs
 * to. Installed once per session, not once per dialog. */
function ensure_picker_styles() {
	frappe.dom.set_style(
		`.ha-employee-picker .ha-filters { position: relative; z-index: 100; overflow: visible; }
		 .ha-employee-picker .ha-filters .form-column,
		 .ha-employee-picker .ha-filters .frappe-control { overflow: visible; }
		 .ha-employee-picker .ha-filters .awesomplete > ul { z-index: 101; }`,
		"ha-employee-picker-style"
	);
}

function truncation_note(entries) {
	const remaining = entries.length - MAX_ROWS_IN_MESSAGE;
	if (remaining <= 0) return "";
	return `<p class="text-muted small">${__("... and {0} more.", [remaining])}</p>`;
}

function build_success_html(success) {
	const created = success.reduce((total, entry) => total + (cint(entry.count) || 0), 0);

	const replaced = success.reduce((total, entry) => total + (cint(entry.replaced) || 0), 0);

	let html = `<p>${__(
		"Created <b>{0}</b> Holiday List Assignment(s) for <b>{1}</b> employee(s).",
		[created, success.length]
	)}`;
	if (replaced) html += ` ${__("Replaced <b>{0}</b> earlier one(s).", [replaced])}`;
	html += "</p>";

	html += `<table class="table table-bordered"><tr><th>${__("Employee")}</th><th>${__(
		"Holiday List Assignments"
	)}</th></tr>`;
	for (const entry of success.slice(0, MAX_ROWS_IN_MESSAGE)) {
		// entry.doc is a server-built get_link_to_form() anchor, inserted as
		// markup on purpose; everything else on the entry is escaped.
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
		"Reason"
	)}</th></tr>`;
	for (const entry of failure.slice(0, MAX_ROWS_IN_MESSAGE)) {
		html += `<tr><td>${entry_label(entry)}</td><td>${esc(entry.reason)}</td></tr>`;
	}
	html += "</table>";
	html += `<p class="text-muted small">${__(
		"Check <a href='/app/List/Error Log?reference_doctype=Holiday List Assignment'>{0}</a> for more details",
		[__("Error Log")]
	)}</p>`;

	return html + truncation_note(failure);
}

function build_skipped_html(skipped, to_date) {
	const names = skipped.slice(0, MAX_ROWS_IN_MESSAGE).map(entry_label).join(", ");
	const period_end = to_date ? frappe.datetime.str_to_user(to_date) : "";

	return (
		`<p>${__(
			"Skipped <b>{0}</b> employee(s) with no previous Holiday List Assignment — there is nothing to restore them to after <b>{1}</b>. Give them a Holiday List Assignment first, then run this tool again for them:",
			[skipped.length, period_end]
		)}</p><p>${names}</p>` + truncation_note(skipped)
	);
}

frappe.ui.form.on("Holiday Assignment Tool", {
	refresh(frm) {
		frm.page.clear_indicator();
		frm.disable_save();
		frm.page.clear_primary_action();
		frm.page.set_primary_action(__("Assign Holidays"), () => frm.events.assign_holidays(frm));
		frm.clear_custom_buttons();
		frm.add_custom_button(__("Clear Filters"), () => frm.events.clear_filters(frm));
		// rows only come from Select Employees; cannot_add_rows is not a DocField
		// column on this frappe, so the JSON flag alone does not hide "Add row"
		frm.set_df_property("employees", "cannot_add_rows", 1);
		frm.events.listen_for_completion(frm);
	},

	/** A Single keeps whatever the last run left in it. The server blanks the
	 * company and the window when a run finishes; this covers everything that
	 * happened before it did — a document saved by an older version, or a run
	 * that never reached the end. */
	onload(frm) {
		frm.events.clear_run_scope(frm);
	},

	/** The run is validated against this company and the picker is scoped to
	 * it, so employees fetched under a different one cannot stay behind. */
	company(frm) {
		if ((frm.doc.employees || []).length) {
			frm.clear_table("employees");
			frm.refresh_field("employees");
			frappe.show_alert({
				message: __("Employees cleared — they belonged to the previous company."),
				indicator: "orange",
			});
		}
		frm._last_filters = null;
	},

	clear_run_scope(frm) {
		const stale = ["company", "from_date", "to_date"].filter((field) => frm.doc[field]);
		if (!stale.length) return;
		stale.forEach((field) => {
			frm.doc[field] = null;
		});
		frm.refresh_fields(stale);
		// nothing was written: the form never saves from here
		frm.doc.__unsaved = 0;
		if (frm.page) frm.page.clear_indicator();
	},

	clear_filters(frm) {
		// The Single is never saved from here, so resetting the local doc is
		// enough; nothing is written until Assign Holidays runs.
		frm._last_filters = null;
		frm.clear_table("employees");
		frm.set_value({ holiday_list: "", from_date: "", to_date: "", company: "" }).then(() => {
			frm.refresh_fields();
			frm.doc.__unsaved = 0;
			frm.page.clear_indicator();
		});
	},

	from_date(frm) {
		if (frm.doc.to_date && frm.doc.from_date > frm.doc.to_date) frm.set_value("to_date", null);
	},

	to_date(frm) {
		if (frm.doc.from_date && frm.doc.to_date && frm.doc.to_date < frm.doc.from_date) {
			frm.set_value("to_date", null);
		}
	},

	// ── Run ──────────────────────────────────────────────────────────────────

	assign_holidays(frm) {
		const employees = frm.doc.employees || [];

		if (!employees.length) {
			frappe.msgprint({
				title: __("No Employees"),
				indicator: "red",
				message: __("Click <b>Select Employees</b> and add at least one employee first."),
			});
			return;
		}

		frappe.confirm(
			__("Assign <b>{0}</b> to {1} employee(s)?", [
				esc(frm.doc.holiday_list),
				employees.length,
			]),
			() => {
				// the desk stays usable while this runs; the primary action is
				// disabled instead, so the run cannot be started twice
				const $primary = frm.page.btn_primary;
				$primary.prop("disabled", true);
				frappe.show_alert({ message: __("Assigning Holidays..."), indicator: "blue" });

				// posts the in-memory document, rows and all; the controller saves
				// it (running validate) before it writes anything
				Promise.resolve(frm.call({ method: "assign_holidays", doc: frm.doc }))
					.then(() => frm.reload_doc())
					.finally(() => $primary.prop("disabled", false));
			}
		);
	},

	/**
	 * off() before on(): refresh runs on every reload_doc, and realtime.on()
	 * appends, so without it one finished run would raise the dialog several
	 * times over.
	 */
	listen_for_completion(frm) {
		frappe.realtime.off(ASSIGN_EVENT);
		frappe.realtime.on(ASSIGN_EVENT, (message) =>
			frm.events.notify_completion(frm, message || {})
		);
	},

	notify_completion(frm, message) {
		// site-room event: drop anything that is not about this tool
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
		if (success.length) sections.push(build_success_html(success));
		if (skipped.length) sections.push(build_skipped_html(skipped, frm.doc.to_date));
		if (failure.length) sections.push(build_failure_html(failure));

		frappe.msgprint({
			title,
			indicator,
			message: sections.join("<hr>"),
			is_minimizable: true,
		});

		// the server emptied the Employees table; show that
		frm.reload_doc();
	},

	// ── Select Employees ─────────────────────────────────────────────────────

	/** The `get_employees` Button field ("Select Employees") above the table. */
	get_employees(frm) {
		if (!frm.doc.from_date) {
			frappe.msgprint({
				title: __("From Date Required"),
				indicator: "red",
				message: __(
					"Set the <b>From Date</b> first — it decides each employee's current holiday list."
				),
			});
			return;
		}

		frm.events.show_selection_dialog(frm);
	},

	request_employees(frm, filters) {
		// Promise.resolve(): frappe.call hands back a jQuery promise, which has
		// no .finally() for the caller to stop its progress bar with.
		return Promise.resolve(
			frappe.call({
				method: FETCH_METHOD,
				args: {
					company: filters.company,
					from_date: frm.doc.from_date,
					to_date: frm.doc.to_date || null,
					employee: filters.employee || null,
					custom_farm: filters.custom_farm || null,
					department: filters.department || null,
					designation: filters.designation || null,
				},
			})
		).then((r) => (r && r.message) || {});
	},

	show_selection_dialog(frm) {
		if (frm._employee_selection_dialog) {
			try {
				frm._employee_selection_dialog.hide();
			} catch (e) {
				// already torn down
			}
		}

		// `dialog` is declared before construction because Link fields can fire
		// change during setup, before `new frappe.ui.Dialog(...)` returns.
		let dialog;

		// last company used, else the user's default; the rest of the filters are
		// remembered for the session. Employee is not: picking one person out of a
		// unit is a one-off, and remembering it would silently hide everyone else
		// the next time the dialog opens.
		const remembered = frm._last_filters || {};
		const filters = {
			company:
				frm.doc.company ||
				remembered.company ||
				frappe.defaults.get_user_default("Company") ||
				"",
			custom_farm: remembered.custom_farm || null,
			department: remembered.department || null,
			designation: remembered.designation || null,
			employee: null,
		};

		// Every filter change starts a fetch; only the newest one may paint, so a
		// slow early request cannot overwrite the list the user is now asking for.
		let ticket = 0;

		const load = () => {
			const mine = ++ticket;
			const $summary = dialog.get_field("summary").$wrapper;

			if (!filters.company) {
				dialog.set_title(__("Select Employees"));
				$summary.html(frm.events.get_summary_html(frm, {}, filters));
				frm.events.render_datatable(frm, dialog, []);
				return Promise.resolve();
			}

			// the list can run to thousands, so it reports progress in the dialog
			// rather than freezing the desk behind a modal
			const stop = inline_progress($summary, __("Fetching employees..."));
			dialog.disable_primary_action();

			return frm.events
				.request_employees(frm, filters)
				.then((result) => {
					if (mine !== ticket) return; // a newer fetch is already in flight
					const employees = result.employees || [];
					dialog.set_title(__("Select Employees ({0})", [employees.length]));
					dialog
						.get_field("summary")
						.$wrapper.html(frm.events.get_summary_html(frm, result, filters));
					frm.events.render_datatable(frm, dialog, employees);
				})
				.finally(() => {
					stop();
					if (mine === ticket) dialog.enable_primary_action();
				});
		};

		/** Every filter reloads the list the same way. All five share one
		 * section, so they lay out as a single row of columns — frappe widens a
		 * five-column section to col-sm-20 rather than wrapping it. */
		const filter_field = (fieldname, label, doctype, get_query) => ({
			fieldtype: "Link",
			fieldname,
			label,
			options: doctype,
			default: filters[fieldname] || "",
			get_query,
			change() {
				const value = (dialog && dialog.get_value(fieldname)) || null;
				if (!dialog || value === filters[fieldname]) return;
				filters[fieldname] = value;
				load();
			},
		});

		/** The company scopes the other pickers, so it is read live off the
		 * dialog rather than closed over. */
		const in_company = () => ({ filters: { company: filters.company } });

		dialog = new frappe.ui.Dialog({
			title: __("Select Employees"),
			size: "extra-large",
			fields: [
				{
					fieldtype: "Link",
					fieldname: "company",
					label: __("Company"),
					options: "Company",
					reqd: 1,
					default: filters.company,
					change() {
						const value = (dialog && dialog.get_value("company")) || "";
						if (!dialog || value === filters.company) return;
						filters.company = value;
						// a unit, a department and a person all belong to one company.
						// filters is cleared first so each field's own change handler
						// sees nothing new and does not fire a second fetch.
						["custom_farm", "department", "employee"].forEach((field) => {
							filters[field] = null;
							if (dialog.get_value(field)) dialog.set_value(field, "");
						});
						load();
					},
				},
				{ fieldtype: "Column Break" },
				filter_field("custom_farm", __("Unit/Division"), "Farm", in_company),
				{ fieldtype: "Column Break" },
				filter_field("department", __("Department"), "Department", in_company),
				{ fieldtype: "Column Break" },
				filter_field("designation", __("Designation"), "Designation"),
				{ fieldtype: "Column Break" },
				filter_field("employee", __("Employee"), "Employee", () => ({
					filters: {
						company: filters.company,
						status: "Active",
						...(filters.department ? { department: filters.department } : {}),
						...(filters.designation ? { designation: filters.designation } : {}),
						// custom_farm belongs to upande_kaitet, so it is absent on some sites
						...(filters.custom_farm && frappe.meta.has_field("Employee", "custom_farm")
							? { custom_farm: filters.custom_farm }
							: {}),
					},
				})),
				{ fieldtype: "Section Break" },
				{ fieldtype: "HTML", fieldname: "summary" },
				{ fieldtype: "HTML", fieldname: "employees_table" },
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
				// the run is validated against the form's company, so it must be
				// the one these employees were fetched from
				if (filters.company !== frm.doc.company) frm.set_value("company", filters.company);
				frm._last_filters = Object.assign({}, filters, { employee: null });
				frm.events.set_employees(frm, selected);
				dialog.hide();
			},
		});

		ensure_picker_styles();
		dialog.$wrapper.addClass("ha-employee-picker");
		// only the filter section is lifted: give the table's own section the
		// same z-index and it would win on DOM order instead.
		dialog.get_field("company").$wrapper.closest(".form-section").addClass("ha-filters");

		// a placeholder until load() runs 150ms from now: `result` only exists
		// once a fetch has returned, and load() fills this in either way
		dialog.get_field("summary").$wrapper.html(frm.events.get_summary_html(frm, {}, filters));
		dialog.show();
		frm._employee_selection_dialog = dialog;

		// The DataTable measures its own width, so it is built after the modal
		// is laid out; built against a fading-in dialog every column is 0 wide.
		setTimeout(load, 150);
	},

	get_summary_html(frm, result, filters) {
		if (!filters.company) {
			return `<div class="text-muted small" style="margin-bottom: 8px;">${__(
				"Pick a Company to list its employees."
			)}</div>`;
		}
		if (!(result.employees || []).length) {
			return `<div class="text-muted small" style="margin-bottom: 8px;">${__(
				"No active employees found for {0}.",
				[frappe.datetime.str_to_user(frm.doc.from_date)]
			)}</div>`;
		}
		if (result.truncated) {
			return `<div class="alert alert-warning" style="padding: 8px 12px; margin-bottom: 8px;">${__(
				"Showing the first <b>{0}</b> of <b>{1}</b> matching employees. Narrow by Unit/Division, Department, Designation or Employee.",
				[result.count, result.total]
			)}</div>`;
		}
		return "";
	},

	render_datatable(frm, dialog, employees) {
		const $wrapper = dialog.get_field("employees_table").$wrapper;
		$wrapper.empty();

		const $container = $(
			`<div class="bulk-holiday-employees" style="min-height: 300px;"></div>`
		).appendTo($wrapper);

		const columns = [
			{ id: "employee", name: "employee", content: __("Employee"), width: 120 },
			{
				id: "employee_name",
				name: "employee_name",
				content: __("Employee Name"),
				width: 200,
			},
			{ id: "custom_farm", name: "custom_farm", content: __("Unit/Division"), width: 140 },
			{ id: "department", name: "department", content: __("Department"), width: 160 },
			{ id: "designation", name: "designation", content: __("Designation"), width: 150 },
			{ id: "prior_holiday_list", name: "prior_holiday_list", content: __("Current Holiday List"), width: 200 },
		].map((column) => ({
			...column,
			editable: false,
			focusable: false,
			dropdown: false,
			align: "left",
			format: (value) => esc(value),
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
			noDataMessage: __("No employees to show."),
		});

		// everything fetched is assignable, so pre-tick all: unticking a few is
		// less work than ticking four hundred
		try {
			dialog.employees_datatable.rowmanager.checkAll(true);
		} catch (e) {
			// older datatable builds have no checkAll; the user ticks manually
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

	/** Replace `employees` with the selection. The only writer of the table. */
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
			message: __("{0} employee(s) added. Press <b>Assign Holidays</b> to run.", [
				selected.length,
			]),
			indicator: "green",
		});
	},
});
