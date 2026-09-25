// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

const OT_FETCH_METHOD =
	"upande_ta.upande_ta.api.holiday_assignment_employees.get_holiday_assignment_employees";
const OT_BULK_STATUS_METHOD =
	"upande_ta.upande_ta.doctype.overtime_request.overtime_request.bulk_overtime_status";
const OT_MAKE_BULK_METHOD =
	"upande_ta.upande_ta.doctype.overtime_request.overtime_request.make_bulk_overtime";

const ot_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));

/** ISO week arithmetic done in plain UTC dates, mirroring the server's
 * datetime.date.fromisocalendar: 4 January is always in week 1, and a week
 * belongs to the year that numbers it — so week 1 can start in December. */
const ot_ymd = (date) => date.toISOString().slice(0, 10);

const ot_add_days = (date, days) => {
	const moved = new Date(date);
	moved.setUTCDate(moved.getUTCDate() + days);
	return moved;
};

const ot_iso_week_start = (year, week) => {
	const jan4 = new Date(Date.UTC(year, 0, 4));
	const first_monday = ot_add_days(jan4, -((jan4.getUTCDay() || 7) - 1));
	return ot_add_days(first_monday, (week - 1) * 7);
};

const ot_iso_week_of = (date) => {
	const day = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
	// the Thursday of a week decides which year numbers it
	const thursday = ot_add_days(day, 4 - (day.getUTCDay() || 7));
	const year = thursday.getUTCFullYear();
	const week = Math.ceil(((thursday - Date.UTC(year, 0, 1)) / 86400000 + 1) / 7);
	return { year, week };
};

const ot_this_week = () => ot_iso_week_of(new Date());

/** An inline bar for work whose length cannot be known in advance: it sweeps
 * towards the end rather than pretending to measure, and leaves the rest of
 * the page usable — which a freeze does not. Returns a stop(). */
const ot_inline_progress = ($area, label) => {
	$area.html(`
		<div class="ot-progress" style="margin-bottom:8px;">
			<div class="text-muted small" style="margin-bottom:4px;">${frappe.utils.escape_html(label)}</div>
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
};

// Week is picked by its ISO number — "Week 38" — alongside the year that
// numbers it. Month keeps the browser's own picker, which writes "2026-09".
const ot_week_label = (number) => `Week ${String(number).padStart(2, "0")}`;

const ot_week_number = (value) => {
	const digits = String(value || "").match(/\d+/g);
	if (!digits) return null;
	// "2026-W38" from the picker this field used to use carries its year first
	const number = cint(digits.length > 1 ? digits[digits.length - 1] : digits[0]);
	return number >= 1 && number <= 53 ? number : null;
};

const ot_is_range = (frm) => frm.doc.request_for && frm.doc.request_for !== "Single Day";

// a Week request asks for the week's total, shared out over the working days
const ot_hours_label = (frm) =>
	frm.doc.request_for === "Week" ? __("Requested Hours for the Week") : __("Requested Hours per Day");

frappe.ui.form.on("Overtime Request", {
	setup(frm) {
		frm.set_query("employee", "employees", () => ({
			filters: {
				company: frm.doc.company,
				status: "Active",
				...(frm.doc.department ? { department: frm.doc.department } : {}),
				...(frm.doc.designation ? { designation: frm.doc.designation } : {}),
				// custom_farm belongs to upande_kaitet, so it is absent on some sites
				...(frm.doc.custom_farm && frappe.meta.has_field("Employee", "custom_farm")
					? { custom_farm: frm.doc.custom_farm }
					: {}),
			},
		}));
		frm.set_query("department", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("custom_farm", () => ({ filters: { company: frm.doc.company } }));
	},

	refresh(frm) {
		// rows come from Select Employees; the grid only edits their hours
		frm.set_df_property("employees", "cannot_add_rows", 1);
		frm.toggle_display(["get_employees", "default_requested_hours"], frm.doc.docstatus === 0);
		frm.events.dress_period_fields(frm);

		if (frm.doc.docstatus === 1) frm.events.show_bulk_overtime_button(frm);
	},

	/** Create > Bulk Overtime once the request has cleared its last approval
	 * and has days worked that no batch holds yet — which includes the last
	 * days of a week an earlier batch paid before the week was out. A batch
	 * already paying it is one click away under View. */
	show_bulk_overtime_button(frm) {
		frappe
			.call({ method: OT_BULK_STATUS_METHOD, args: { overtime_request: frm.doc.name } })
			.then((r) => {
				const status = (r && r.message) || {};
				if (status.batch) {
					frm.add_custom_button(
						__("Bulk Overtime"),
						() => frappe.set_route("Form", "Bulk Overtime", status.batch),
						__("View")
					);
				}
				if (status.approved && status.days_left) {
					frm.add_custom_button(
						__("Bulk Overtime"),
						() => frm.events.make_bulk_overtime(frm),
						__("Create")
					);
				}
			});
	},

	/** Build and save the batch on the server, then open it: its rows are
	 * already stored, so leaving the form cannot lose them. */
	make_bulk_overtime(frm) {
		frappe
			.call({
				method: OT_MAKE_BULK_METHOD,
				args: { overtime_request: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating Bulk Overtime..."),
			})
			.then((r) => {
				if (r && r.message) frappe.set_route("Form", "Bulk Overtime", r.message);
			});
	},

	/** Month wears the browser's own picker; Week is a plain list of ISO week
	 * numbers. Either way the start date is named for the period requested. */
	dress_period_fields(frm) {
		const $month = frm.fields_dict.month && frm.fields_dict.month.$input;
		if ($month && $month.attr("type") !== "month") $month.attr("type", "month");
		frm.set_df_property(
			"overtime_date",
			"label",
			ot_is_range(frm) ? __("From Date") : __("Overtime Date")
		);
		frm.set_df_property("default_requested_hours", "label", ot_hours_label(frm));
		frm.fields_dict.employees.grid.update_docfield_property(
			"requested_hours",
			"label",
			ot_hours_label(frm)
		);
		frm.refresh_field("employees");
		frm.events.describe_week(frm);
	},

	/** Spell out the dates a week number lands on, next to the list itself. */
	describe_week(frm) {
		const dates =
			frm.doc.request_for === "Week" && frm.doc.overtime_date && frm.doc.to_date
				? __("{0} to {1}", [
						frappe.datetime.str_to_user(frm.doc.overtime_date),
						frappe.datetime.str_to_user(frm.doc.to_date),
				  ])
				: __("ISO week: Monday to Sunday.");
		frm.set_df_property("week", "description", dates);
	},

	/** Week and Month fix both ends; Single Day collapses them. The dates are
	 * filled in here rather than on save, because the desk checks its own
	 * mandatory fields before the server is ever asked. */
	set_period(frm) {
		// writing the corrected week back re-enters this through the week
		// handler, and week 53 of a short year is exactly that case
		if (frm._setting_period) return;
		frm._setting_period = true;
		try {
			const type = frm.doc.request_for;
			if (type === "Week") {
				const number = ot_week_number(frm.doc.week);
				if (!number) return;
				const start = ot_iso_week_start(
					cint(frm.doc.week_year) || ot_this_week().year,
					number
				);
				// read back off the resolved Monday: a week the year is too
				// short for rolls into the next one, and the list should say so
				const resolved = ot_iso_week_of(
					new Date(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate())
				);
				frm.set_value("week", ot_week_label(resolved.week));
				frm.set_value("week_year", resolved.year);
				frm.set_value("overtime_date", ot_ymd(start));
				frm.set_value("to_date", ot_ymd(ot_add_days(start, 6)));
			} else if (type === "Month") {
				const picked = /^(\d{4})-(\d{2})$/.exec(frm.doc.month || "");
				let year, month;
				if (picked) {
					year = cint(picked[1]);
					month = cint(picked[2]);
				} else if (frm.doc.overtime_date) {
					const on = frappe.datetime.str_to_obj(frm.doc.overtime_date);
					year = on.getFullYear();
					month = on.getMonth() + 1;
				} else {
					const now = new Date();
					year = now.getFullYear();
					month = now.getMonth() + 1;
				}
				frm.set_value("month", `${year}-${String(month).padStart(2, "0")}`);
				frm.set_value("overtime_date", ot_ymd(new Date(Date.UTC(year, month - 1, 1))));
				frm.set_value("to_date", ot_ymd(new Date(Date.UTC(year, month, 0))));
			} else if (type === "Single Day") {
				frm.set_value("to_date", frm.doc.overtime_date);
			} else if (!frm.doc.to_date) {
				frm.set_value("to_date", frm.doc.overtime_date);
			}
		} finally {
			frm._setting_period = false;
		}
		frm.events.describe_week(frm);
	},

	request_for(frm) {
		if (frm.doc.request_for === "Week") {
			// overtime is nearly always asked for the week it is worked in, so
			// that is where the fields start — both of them, every time
			const now = ot_this_week();
			if (!cint(frm.doc.week_year)) frm.set_value("week_year", now.year);
			if (!ot_week_number(frm.doc.week)) frm.set_value("week", ot_week_label(now.week));
		} else {
			frm.set_value("week", null);
			frm.set_value("week_year", null);
		}
		if (frm.doc.request_for !== "Month") frm.set_value("month", null);
		frm.events.dress_period_fields(frm);
		frm.events.set_period(frm);
	},

	week(frm) {
		if (frm.doc.request_for !== "Week") return;
		if (!cint(frm.doc.week_year)) frm.set_value("week_year", ot_this_week().year);
		frm.events.set_period(frm);
	},

	week_year(frm) {
		if (frm.doc.request_for === "Week") frm.events.set_period(frm);
	},

	month(frm) {
		if (frm.doc.request_for === "Month") frm.events.set_period(frm);
	},

	overtime_date(frm) {
		frm.events.set_period(frm);
	},

	onload(frm) {
		// one type carries the rates, so the last one used is nearly always right
		if (frm.is_new() && !frm.doc.overtime_type) {
			frappe.db
				.get_list("Overtime Type", { limit: 2, order_by: "modified desc" })
				.then((types) => {
					if (types.length === 1) frm.set_value("overtime_type", types[0].name);
				});
		}
	},

	company(frm) {
		// employees belong to one company, and so do departments
		if ((frm.doc.employees || []).length) {
			frm.clear_table("employees");
			frm.refresh_field("employees");
		}
		if (frm.doc.department) frm.set_value("department", null);
		if (frm.doc.custom_farm) frm.set_value("custom_farm", null);
	},

	// changing a filter on the request restarts the picker from it
	department(frm) {
		frm._last_filters = null;
	},

	designation(frm) {
		frm._last_filters = null;
	},

	custom_farm(frm) {
		frm._last_filters = null;
	},

	default_requested_hours(frm) {
		const hours = flt(frm.doc.default_requested_hours);
		if (!hours) return;
		(frm.doc.employees || []).forEach((row) => {
			row.requested_hours = hours;
		});
		frm.refresh_field("employees");
	},

	get_employees(frm) {
		if (!frm.doc.company || !frm.doc.overtime_date) {
			frappe.msgprint({
				title: __("Missing Details"),
				indicator: "red",
				message: ot_is_range(frm)
					? __("Set the <b>Company</b> and the <b>dates</b> first.")
					: __("Set the <b>Company</b> and <b>Overtime Date</b> first."),
			});
			return;
		}
		frm.events.show_selection_dialog(frm);
	},

	request_employees(frm, filters) {
		// Promise.resolve(): frappe.call hands back a jQuery promise, and jQuery
		// deferreds have no .finally() — load() below stops its bar with one.
		return Promise.resolve(
			frappe.call({
				method: OT_FETCH_METHOD,
				args: {
					company: frm.doc.company,
					from_date: frm.doc.overtime_date,
					to_date: frm.doc.to_date || frm.doc.overtime_date,
					custom_farm: filters.custom_farm || null,
					department: filters.department || null,
					designation: filters.designation || null,
					with_holiday_list: 0,
				},
			})
		).then((r) => (r && r.message) || {});
	},

	show_selection_dialog(frm) {
		let dialog; // declared first: Link fields can fire change during setup
		// the request's own filters are where the picker starts; narrowing it
		// further here is a one-off that the last pick remembers
		const filters = Object.assign(
			{
				custom_farm: frm.doc.custom_farm || null,
				department: frm.doc.department || null,
				designation: frm.doc.designation || null,
			},
			frm._last_filters || {}
		);
		const already = new Set((frm.doc.employees || []).map((row) => row.employee));

		const load = () => {
			// the list can run to thousands, so it reports progress in the
			// dialog rather than freezing the desk behind a modal
			const stop = ot_inline_progress(
				dialog.get_field("summary").$wrapper,
				__("Fetching employees...")
			);
			dialog.disable_primary_action();
			return frm.events
				.request_employees(frm, filters)
				.then((result) => {
					stop();
					// someone already on the request is not offered twice
					const employees = (result.employees || []).filter(
						(e) => !already.has(e.employee)
					);
					dialog.set_title(__("Select Employees ({0})", [employees.length]));
					dialog
						.get_field("summary")
						.$wrapper.html(
							result.truncated
								? `<div class="alert alert-warning" style="padding: 8px 12px; margin-bottom: 8px;">${__(
										"Showing the first <b>{0}</b> of <b>{1}</b> employees. Narrow by Unit/Division, Department or Designation.",
										[result.count, result.total]
								  )}</div>`
								: ""
						);
					frm.events.render_datatable(dialog, employees);
				})
				.finally(() => {
					stop();
					dialog.enable_primary_action();
				});
		};

		/** Every filter reloads the list the same way. */
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

		dialog = new frappe.ui.Dialog({
			title: __("Select Employees"),
			size: "extra-large",
			fields: [
				filter_field("custom_farm", __("Unit/Division"), "Farm", () => ({
					filters: { company: frm.doc.company },
				})),
				{ fieldtype: "Column Break" },
				filter_field("department", __("Department"), "Department", () => ({
					filters: { company: frm.doc.company },
				})),
				{ fieldtype: "Column Break" },
				filter_field("designation", __("Designation"), "Designation"),
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Float",
					fieldname: "requested_hours",
					label: ot_hours_label(frm),
					default: frm.doc.default_requested_hours || "",
					description: __("For everyone you tick."),
				},
				{ fieldtype: "Section Break" },
				{ fieldtype: "HTML", fieldname: "summary" },
				{ fieldtype: "HTML", fieldname: "employees_table" },
			],
			primary_action_label: __("Add Selected"),
			primary_action(values) {
				const selected = frm.events.get_checked_rows(dialog);
				if (!selected.length) {
					frappe.msgprint({
						title: __("No Employees Selected"),
						indicator: "red",
						message: __("Tick at least one employee to add."),
					});
					return;
				}
				const hours = flt(values.requested_hours);
				if (hours > 0 && hours !== flt(frm.doc.default_requested_hours)) {
					frm.doc.default_requested_hours = hours;
					frm.refresh_field("default_requested_hours");
				}
				frm._last_filters = Object.assign({}, filters);
				selected.forEach((employee) => {
					const row = frm.add_child("employees");
					row.employee = employee.employee;
					row.employee_name = employee.employee_name;
					row.department = employee.department;
					row.designation = employee.designation;
					row.custom_farm = employee.custom_farm;
					row.requested_hours = hours || 0;
				});
				frm.refresh_field("employees");
				frm.dirty();
				dialog.hide();
				frappe.show_alert({
					message: __("{0} employee(s) added.", [selected.length]),
					indicator: "green",
				});
			},
		});

		// the DataTable's sticky checkbox column would paint over the dropdown
		dialog
			.get_field("custom_farm")
			.$wrapper.closest(".form-section")
			.css({ position: "relative", zIndex: 10 });
		dialog.show();
		// built after the modal is laid out, or every column measures 0 wide
		setTimeout(load, 150);
	},

	render_datatable(dialog, employees) {
		const $wrapper = dialog.get_field("employees_table").$wrapper;
		$wrapper.empty();
		const $container = $(`<div style="min-height: 300px;"></div>`).appendTo($wrapper);

		const columns = [
			{ id: "employee", name: "employee", content: __("Employee"), width: 120 },
			{
				id: "employee_name",
				name: "employee_name",
				content: __("Employee Name"),
				width: 220,
			},
			{ id: "custom_farm", name: "custom_farm", content: __("Unit/Division"), width: 160 },
			{ id: "department", name: "department", content: __("Department"), width: 180 },
			{ id: "designation", name: "designation", content: __("Designation"), width: 170 },
		].map((column) => ({
			...column,
			editable: false,
			focusable: false,
			dropdown: false,
			align: "left",
			format: (value) => ot_esc(value),
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
	},

	get_checked_rows(dialog) {
		const datatable = dialog.employees_datatable;
		if (!datatable) return [];
		const rows = datatable.datamanager.data || [];
		return (datatable.rowmanager.getCheckedRows() || [])
			.map((index) => (typeof index === "object" ? index : rows[index]))
			.filter((row) => row && row.employee);
	},
});
