
frappe.provide("frappe.views");

(function () {
	const REPORT = "Monthly Attendance Sheet";

	const DAY_RE = /^\d{2}-\d{2}-\d{4}$/;

	const SUMMARY_LABEL_FIELD = "employee";

	const STATUS_CODES = /^(P|A|WFH|H|WO|HD\/P|HD\/A)$/;

	function leaveColorFormatter(value, row, column, data, default_formatter) {
		const rawValue = value;

		value = default_formatter ? default_formatter(value, row, column, data) : value;

		let summarized_view, group_by;
		try {
			summarized_view = frappe.query_report.get_filter_value("summarized_view");
			group_by = frappe.query_report.get_filter_value("group_by");
		} catch (e) {
			
		}

		if (group_by && column.colIndex === 1) {
			value = "<strong>" + value + "</strong>";
		}

		if (data && data._is_summary) {
			if (rawValue === null || rawValue === undefined || rawValue === "") return value;
			const fn = column && (column.fieldname || column.id);

			if (fn === SUMMARY_LABEL_FIELD) {

				return (
					// Background comes from the themed variable in
					// ensureSummaryStyle(): this label floats over the frozen
					// column, so it has to repaint the band behind itself.
					"<b class='ta-summary-label' style=\"position:absolute; left:0; top:0; bottom:0;" +
					" display:flex; align-items:center; padding-left:15px; white-space:nowrap;" +
					" z-index:5;\">" +
					rawValue +
					"</b>"
				);
			}
			return "<b>" + rawValue + "</b>";
		}

		if (summarized_view) return value;

		const fieldname = column && (column.fieldname || column.id);
		if (!DAY_RE.test(fieldname || "")) return value;

		const txt = (value || "").toString().replace(/<[^>]*>/g, "").trim();
		if (!txt) return value;

		let color;
		if (STATUS_CODES.test(txt)) {
			color =
				txt === "P" || txt === "WFH"
					? "green"
					: txt === "A"
					? "red"
					: txt === "HD/P"
					? "#914EE3"
					: txt === "HD/A"
					? "orange"
					: "#878787"; // H, WO
		} else {
			color = "#318AD8"; // a leave-type abbreviation -> blue
		}
		return "<span style='color:" + color + "'>" + value + "</span>";
	}

	// Inject the border/styling for the summary block once.
	//
	// Themed, not hard-coded: the band used to be #f7f7f7 with a black rule,
	// which turned the totals into a white slab with unreadable text once the
	// desk was in dark mode. `--subtle-accent` resolves to gray-50 on light and
	// gray-900 on dark — a step away from the row background either way — and
	// `--heading-color` inverts with it, so the block reads the same in both.
	function ensureSummaryStyle() {
		if (document.getElementById("ta-mas-summary-style")) return;
		const css =
			":root { --ta-summary-bg: var(--subtle-accent, #f7f7f7);" +
			" --ta-summary-fg: var(--heading-color, #171717); }" +
			".dt-row.ta-summary-row .dt-cell { background:var(--ta-summary-bg) !important; color:var(--ta-summary-fg) !important; position:relative; }" +
			".dt-row.ta-summary-top .dt-cell" +
			" { border-top:2px solid var(--ta-summary-fg) !important; }" +
			".dt-row.ta-summary-row .ta-summary-label" +
			" { background:var(--ta-summary-bg); color:var(--ta-summary-fg); }";
		const style = document.createElement("style");
		style.id = "ta-mas-summary-style";
		style.textContent = css;
		document.head.appendChild(style);
	}


	function markSummaryRows(report) {
		try {
			const wrapper = report && report.$report && report.$report[0];
			const rows = report && report.data;
			if (!wrapper || !rows) return;

			ensureSummaryStyle();

			wrapper.querySelectorAll(".dt-row.ta-summary-row, .dt-row.ta-summary-top").forEach((el) => {
				el.classList.remove("ta-summary-row", "ta-summary-top");
			});

			let firstSummaryMarked = false;
			rows.forEach((row, i) => {
				if (!row || !row._is_summary) return;
				const $row = wrapper.querySelector(".dt-row-" + i);
				if (!$row) return;
				$row.classList.add("ta-summary-row");
				if (!firstSummaryMarked) {
					$row.classList.add("ta-summary-top");
					firstSummaryMarked = true;
				}
			});
		} catch (e) {
			console.warn("[MAS summary]", e);
		}
	}

	function installSummaryObserver(report) {
		try {
			const wrapper = report && report.$report && report.$report[0];
			if (!wrapper || wrapper.__ta_summary_observer) return;

			let scheduled = false;
			const obs = new MutationObserver(() => {
				if (scheduled) return;
				scheduled = true;
				requestAnimationFrame(() => {
					scheduled = false;
					markSummaryRows(report);
				});
			});
			obs.observe(wrapper, { childList: true, subtree: true });
			wrapper.__ta_summary_observer = obs;
		} catch (e) {
			console.warn("[MAS observer]", e);
		}
	}


	function pinSummaryRows(datatable, dataRows) {
		try {
			const dm = datatable && datatable.datamanager;
			if (!dm || !Array.isArray(dm.rowViewOrder) || !dataRows) return;

			const normal = [];
			const summary = [];
			dm.rowViewOrder.forEach((idx) => {
				if (dataRows[idx] && dataRows[idx]._is_summary) summary.push(idx);
				else normal.push(idx);
			});
			if (!summary.length) return;
			summary.sort((a, b) => a - b); // preserve Present/Absent/.../Total order

			dm.rowViewOrder.splice(0, dm.rowViewOrder.length, ...normal, ...summary);

			// keep the Sr. No. column consistent with the new view order
			if (dm.hasColumnById && dm.hasColumnById("_rowIndex")) {
				const sr = dm.getColumnIndexById("_rowIndex");
				dm.rows.forEach((row, index) => {
					const viewIndex = dm.rowViewOrder.indexOf(index);
					if (row[sr]) row[sr].content = viewIndex + 1 + "";
				});
			}
		} catch (e) {
			console.warn("[MAS pin summary]", e);
		}
	}

	function onSortColumnPin() {
		try {
			const rep = frappe.query_report;
			if (!rep || rep.report_name !== REPORT) return;
			pinSummaryRows(this, rep.data);
			if (this.rowmanager && this.rowmanager.refreshRows) this.rowmanager.refreshRows();
			setTimeout(() => markSummaryRows(rep), 30);
		} catch (e) {
			console.warn("[MAS onSortColumn]", e);
		}
	}

	// Add a "Category" filter (Link → Employee Grade) to the standard sheet,
	// matching the custom Monthly Attendance Report. The server override reads
	// filters.category and applies WHERE e.grade = category. The sheet's filter
	// list lives in a file-based standard report JS, so we splice ours in at
	// runtime once report_settings is loaded (before setup_filters reads it).
	function addGreenhouseGroupBy(settings) {
		if (!(frappe.boot && frappe.boot.upande_ta_group_by_greenhouse)) return;
		const groupBy = settings.filters.find((df) => df && df.fieldname === "group_by");
		if (!groupBy) return;
		const options = Array.isArray(groupBy.options)
			? groupBy.options
			: String(groupBy.options || "").split("\n");
		if (!options.includes("Greenhouse")) groupBy.options = [...options, "Greenhouse"];
	}

	function injectCategoryFilter(settings) {
		try {
			if (!settings || !Array.isArray(settings.filters)) return;
			addGreenhouseGroupBy(settings);
			if (settings.filters.some((f) => f && f.fieldname === "category")) return;
			const filter = {
				fieldname: "category",
				label: __("Category"),
				fieldtype: "Link",
				options: "Employee Grade",
			};
			// Place it right after Company (fallback: before Group By, else end).
			let idx = settings.filters.findIndex((f) => f && f.fieldname === "company");
			if (idx === -1) {
				const gb = settings.filters.findIndex((f) => f && f.fieldname === "group_by");
				idx = gb === -1 ? settings.filters.length - 1 : gb - 1;
			}
			settings.filters.splice(idx + 1, 0, filter);
		} catch (e) {
			console.warn("[MAS category filter]", e);
		}
	}

	const CHANGE_WEEK_OFF_METHOD = "upande_ta.upande_ta.week_off_change.change_week_off";
	const REMOVE_WEEK_OFF_METHOD = "upande_ta.upande_ta.week_off_change.remove_week_off";
	const ADD_WEEK_OFF_METHOD = "upande_ta.upande_ta.week_off_change.add_week_off";
	const HOLIDAY_LIST_ON_METHOD = "upande_ta.upande_ta.week_off_change.holiday_list_on";
	const ASSIGN_HOLIDAY_LIST_METHOD = "upande_ta.upande_ta.week_off_change.assign_holiday_list";
	const SHIFT_SCHEDULE_METHOD = "upande_ta.upande_ta.shift_change.get_shift_schedule";
	const CHANGE_SHIFT_METHOD = "upande_ta.upande_ta.shift_change.change_shift";
	const SHIFT_SCHEDULE_DAYS = 30;

	const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
	const WEEK_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

	/** Clicking an employee's day cell changes their week off from that day:
	 * the weekday clicked becomes their only week off until the End Date. On a
	 * week-off cell it can instead remove that weekday as a week off. */
	function bindWeekOffChange(report) {
		const dt = report.datatable;
		if (!dt || !dt.wrapper || dt.__ta_week_off) return;
		dt.__ta_week_off = true;

		$(dt.wrapper).on("click", ".dt-cell__content", function () {
			try {
				if (frappe.query_report.get_filter_value("summarized_view")) return;
			} catch (e) {
				return;
			}
			const weekOff = canChangeWeekOff();
			const shift = canChangeShift();
			if (!weekOff && !shift) return;

			const $cell = $(this).closest(".dt-cell");
			const colIndex = cint($cell.attr("data-col-index"));
			const rowIndex = $cell.attr("data-row-index");
			if (rowIndex === undefined) return;

			const column = dt.getColumn(colIndex) || {};
			const fieldname = (column.docfield && column.docfield.fieldname) || column.id || "";
			const row = dt.datamanager.getData(cint(rowIndex));
			if (!row || row._is_summary || !row.employee) return;

			if (fieldname === "shift") {
				if (shift) showShiftDialog(report, row);
				return;
			}
			if (!weekOff || !DAY_RE.test(fieldname)) return;

			const [day, month, year] = fieldname.split("-");
			const isWeekOff = String(row[fieldname] || "").replace(/<[^>]*>/g, "").trim() === "WO";
			const clicked = `${year}-${month}-${day}`;
			frappe
				.call({
					method: HOLIDAY_LIST_ON_METHOD,
					args: { employee: row.employee, date: clicked },
				})
				.then((r) => {
					if (r && r.message) showWeekOffDialog(report, row, clicked, isWeekOff);
					else showAssignHolidayListDialog(report, row, clicked);
				});
		});
	}

	function canChangeWeekOff() {
		return (
			frappe.model.can_create("Holiday List Assignment") &&
			!(frappe.boot && frappe.boot.upande_ta_week_off_change_disabled)
		);
	}

	function canChangeShift() {
		return (
			frappe.model.can_create("Shift Assignment") &&
			frappe.model.can_submit("Shift Assignment") &&
			!(frappe.boot && frappe.boot.upande_ta_shift_change_disabled)
		);
	}

	function shiftScheduleHtml(schedule) {
		const ranges = (schedule && schedule.ranges) || [];
		if (!ranges.length) return "";
		const day = (d) => frappe.datetime.str_to_user(d);
		const rows = ranges
			.map((r) => {
				const shift = r.shift_type ? frappe.utils.escape_html(r.shift_type) : "—";
				const link = r.assignment
					? `<a href="/app/shift-assignment/${encodeURIComponent(r.assignment)}" target="_blank">${frappe.utils.escape_html(r.assignment)}</a>`
					: `<span class="text-muted">${__("Default")}</span>`;
				const startDate = r.assignment ? day(r.start_date) : "";
				const endDate = r.assignment ? (r.end_date ? day(r.end_date) : __("Open")) : "";
				return `<tr><td>${shift}</td><td>${startDate}</td><td>${endDate}</td><td>${link}</td></tr>`;
			})
			.join("");
		return `<table class="table table-bordered table-sm" style="margin:0">
			<thead><tr><th>${__("Shift")}</th><th>${__("Start Date")}</th><th>${__("End Date")}</th><th>${__("Assignment")}</th></tr></thead>
			<tbody>${rows}</tbody></table>`;
	}

	function showAssignHolidayListDialog(report, row, date) {
		const dialog = new frappe.ui.Dialog({
			title: __("Assign Holiday List"),
			fields: [
				{
					fieldtype: "Data",
					fieldname: "employee",
					label: __("Employee"),
					read_only: 1,
					default: `${row.employee_name || ""} (${row.employee})`,
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Date",
					fieldname: "from_date",
					label: __("From Date"),
					reqd: 1,
					default: date,
				},
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Link",
					fieldname: "holiday_list",
					label: __("Holiday List"),
					options: "Holiday List",
					reqd: 1,
				},
			],
			primary_action_label: __("Assign Holiday List"),
			primary_action(values) {
				frappe
					.call({
						method: ASSIGN_HOLIDAY_LIST_METHOD,
						type: "POST",
						args: {
							employee: row.employee,
							holiday_list: values.holiday_list,
							from_date: values.from_date,
						},
						freeze: true,
					})
					.then((r) => {
						const result = (r && r.message) || {};
						dialog.hide();
						frappe.msgprint({
							title: __("Holiday List Assigned"),
							indicator: "green",
							message: [
								__("{0}: {1} from {2}.", [
									frappe.utils.escape_html(row.employee_name || row.employee),
									frappe.utils.escape_html(values.holiday_list),
									frappe.datetime.str_to_user(values.from_date),
								]),
								...(result.created || []),
							].join("<br>"),
						});
						report.refresh();
					});
			},
		});
		dialog.show();
	}

	function showWeekOffDialog(report, row, date, isWeekOff) {
		const weekday = WEEKDAYS[new Date(`${date}T00:00:00`).getDay()];
		const who = frappe.utils.escape_html(row.employee_name || row.employee);

		const run = (method, values, done, args = {}) => {
			if (!values.to_date) {
				dialog.get_field("to_date").$input.focus();
				return;
			}
			if (values.to_date < date) {
				frappe.msgprint(__("End Date cannot be before {0}.", [frappe.datetime.str_to_user(date)]));
				return;
			}
			frappe
				.call({
					method,
					type: "POST",
					args: Object.assign({ employee: row.employee, from_date: date, to_date: values.to_date }, args),
					freeze: true,
				})
				.then((r) => {
					const result = (r && r.message) || {};
					dialog.hide();
					const range = {
						from_date: result.from_date || date,
						to_date: result.to_date || values.to_date,
					};
					const lines = [done(range)];
					if (result.restored_to) {
						lines.push(
							__("Back on {0} from {1}.", [
								frappe.utils.escape_html(result.restored_to),
								frappe.datetime.str_to_user(
									frappe.datetime.add_days(range.to_date, 1),
								),
							]),
						);
					}
					if ((result.created || []).length) lines.push(result.created.join(", "));
					if ((result.absent_removed || []).length) {
						lines.push(
							__("Absent removed on: {0}", [
								result.absent_removed.map((d) => frappe.datetime.str_to_user(d)).join(", "),
							])
						);
					}
					if ((result.absent_on_week_off || []).length) {
						lines.push(
							__("Still marked Absent on: {0}", [
								result.absent_on_week_off.map((d) => frappe.datetime.str_to_user(d)).join(", "),
							])
						);
					}
					frappe.msgprint({ title: __("Week Off Updated"), indicator: "green", message: lines.join("<br>") });
					report.refresh();
				});
		};

		const span = (range) => [
			frappe.datetime.str_to_user(range.from_date),
			frappe.datetime.str_to_user(range.to_date),
		];

		const dialog = new frappe.ui.Dialog({
			title: __("Week Off"),
			fields: [
				{
					fieldtype: "Data",
					fieldname: "employee",
					label: __("Employee"),
					read_only: 1,
					default: `${row.employee_name || ""} (${row.employee})`,
				},
				{ fieldtype: "Column Break" },
				{ fieldtype: "Date", fieldname: "from_date", label: __("From Date"), read_only: 1, default: date },
				{ fieldtype: "Date", fieldname: "to_date", label: __("End Date"), reqd: 1 },
				{ fieldtype: "Section Break" },
				{
					fieldtype: "MultiCheck",
					fieldname: "week_off_days",
					label: __("Week Off Days"),
					columns: 4,
					options: WEEK_ORDER.map((day) => ({ label: __(day), value: day, checked: day === weekday })),
				},
			],
			primary_action_label: __("Change Week Off"),
			primary_action(values) {
				weekOffAction(CHANGE_WEEK_OFF_METHOD, values, "{0}: {1} week off from {2} to {3}.");
			},
		});

		const weekOffAction = (method, values, message) => {
			const days = values.week_off_days || [];
			if (!days.length) {
				frappe.msgprint(__("Tick at least one Week Off Day."));
				return;
			}
			const ordered = WEEK_ORDER.filter((day) => days.includes(day));
			run(
				method,
				values,
				(v) => __(message, [who, ordered.map((day) => __(day)).join(" & "), ...span(v)]),
				{ weekdays: JSON.stringify(ordered) },
			);
		};

		dialog.add_custom_action(__("Add Week Off"), () =>
			weekOffAction(
				ADD_WEEK_OFF_METHOD,
				dialog.get_values(true),
				"{0}: {1} added as a week off from {2} to {3}.",
			),
		);
		dialog.add_custom_action(__("Remove Week Off"), () =>
			weekOffAction(
				REMOVE_WEEK_OFF_METHOD,
				dialog.get_values(true),
				"{0}: no {1} week off from {2} to {3}.",
			),
		);
		dialog.show();
	}

	/** The turns a rotation makes, as the server builds them: calendar weeks
	 * Monday to Sunday, `weeks` each, the first ending on the Sunday of
	 * `from`'s week; back-to-back turns of one shift merge. */
	function rotationBlocks(from, to, shifts, weeks) {
		const blocks = [];
		if (!from || !to || to < from || !shifts.length) return blocks;
		let start = from;
		for (let turn = 0; start <= to && turn < 500; turn++) {
			const weekday = (new Date(`${start}T00:00:00`).getDay() + 6) % 7;
			let end = frappe.datetime.add_days(start, 6 - weekday + 7 * (weeks - 1));
			if (end > to) end = to;
			const shift = shifts[turn % shifts.length];
			const last = blocks[blocks.length - 1];
			if (last && last.shift === shift) last.to = end;
			else blocks.push({ from: start, to: end, shift });
			start = frappe.datetime.add_days(end, 1);
		}
		return blocks;
	}

	/** `after`: what the employee is on from the day after the End Date —
	 * `{ from, to, shift }`, `to` empty when that assignment is open-ended. */
	function rotationPreviewHtml(blocks, after) {
		if (!blocks.length) return "";
		const day = (d) => frappe.datetime.str_to_user(d);
		const line = (b, cls = "") =>
			`<tr class="${cls}"><td>${day(b.from)}</td><td>${b.to ? day(b.to) : __("Open")}</td><td>${frappe.utils.escape_html(b.shift)}</td></tr>`;
		const rows = blocks.map((b) => line(b)).join("") + (after && after.shift ? line(after, "text-muted") : "");
		return `<table class="table table-bordered table-sm" style="margin:0">
			<thead><tr><th>${__("From")}</th><th>${__("To")}</th><th>${__("Shift")}</th></tr></thead>
			<tbody>${rows}</tbody></table>`;
	}

	function showShiftDialog(report, row) {
		const who = frappe.utils.escape_html(row.employee_name || row.employee);
		const day = (d) => frappe.datetime.str_to_user(d);
		const dates = (d) => (d || []).map(day).join(", ");
		let dialog;
		let current = null;
		let changed = false;
		let saving = false;
		let autoFrom = null;
		const today = frappe.datetime.get_today();

		const rotationShifts = () =>
			((dialog && dialog.get_value("rotation")) || []).map((r) => r.shift_type).filter(Boolean);

		const updatePreview = () => {
			if (!dialog) return;
			dialog.refresh_dependency();
			const rotation = rotationShifts();
			const from_date = dialog.get_value("from_date");
			// a rotation runs from this week; a single shift from where the last entry ends
			if (rotation.length && from_date === autoFrom && autoFrom !== today) {
				autoFrom = today;
				dialog.set_value("from_date", today);
				return;
			}
			const shift_type = dialog.get_value("shift_type");
			const to_date = dialog.get_value("to_date");
			const blocks = rotationBlocks(
				from_date,
				to_date,
				[shift_type, ...rotation].filter(Boolean),
				rotation.length ? Math.max(cint(dialog.get_value("weeks_per_shift")) || 1, 1) : 9999
			);
			const $preview = dialog.get_field("rotation_preview").$wrapper;
			$preview.html(rotationPreviewHtml(blocks, afterCache[to_date]));
			if (!blocks.length || to_date in afterCache) return;

			const next = frappe.datetime.add_days(to_date, 1);
			frappe
				.call({
					method: SHIFT_SCHEDULE_METHOD,
					args: { employee: row.employee, from_date: next, days: 1 },
				})
				.then((r) => {
					const range = ((r && r.message && r.message.ranges) || [])[0] || {};
					afterCache[to_date] = { from: next, to: range.end_date || "", shift: range.shift_type };
					if (dialog.get_value("to_date") === to_date) {
						$preview.html(rotationPreviewHtml(blocks, afterCache[to_date]));
					}
				});
		};
		// End Date -> what follows it; cleared whenever the schedule is saved
		let afterCache = {};

		let previewTimer = null;
		const schedulePreview = () => {
			clearTimeout(previewTimer);
			previewTimer = setTimeout(updatePreview, 250);
		};

		const loadSchedule = () =>
			frappe
				.call({
					method: SHIFT_SCHEDULE_METHOD,
					args: { employee: row.employee, days: SHIFT_SCHEDULE_DAYS },
				})
				.then((r) => {
					const schedule = (r && r.message) || {};
					current = schedule.current || null;
					dialog.get_field("shift_schedule").$wrapper.html(shiftScheduleHtml(schedule));
					if (schedule.next_from) {
						autoFrom = schedule.next_from;
						return dialog.set_value("from_date", schedule.next_from);
					}
				});

		const resetEntry = () => {
			dialog.set_value("shift_type", "");
			const rotation = dialog.get_field("rotation");
			rotation.df.data = [];
			rotation.grid.refresh();
			schedulePreview();
		};

		dialog = new frappe.ui.Dialog({
			title: __("Shift"),
			size: "large",
			fields: [
				{
					fieldtype: "Data",
					fieldname: "employee",
					label: __("Employee"),
					read_only: 1,
					default: `${row.employee_name || ""} (${row.employee})`,
				},
				{ fieldtype: "Section Break" },
				{ fieldtype: "HTML", fieldname: "shift_schedule" },
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Link",
					fieldname: "shift_type",
					label: __("Shift Type"),
					options: "Shift Type",
					// a rotation names its own shifts
					mandatory_depends_on: "eval:!(doc.rotation || []).some((r) => r.shift_type)",
					get_query: () => ({
						filters: current ? { name: ["!=", current.shift_type] } : {},
					}),
					change: schedulePreview,
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Date",
					fieldname: "from_date",
					label: __("From Date"),
					reqd: 1,
					change: () => {
						const from_date = dialog && dialog.get_value("from_date");
						if (from_date) {
							dialog.set_value("to_date", frappe.datetime.add_days(from_date, SHIFT_SCHEDULE_DAYS - 1));
						}
						schedulePreview();
					},
				},
				{
					fieldtype: "Date",
					fieldname: "to_date",
					label: __("End Date"),
					reqd: 1,
					change: schedulePreview,
				},
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Table",
					fieldname: "rotation",
					label: __("Rotate With"),
					in_place_edit: true,
					data: [],
					fields: [
						{
							fieldtype: "Link",
							fieldname: "shift_type",
							label: __("Shift Type"),
							options: "Shift Type",
							in_list_view: 1,
							columns: 10,
							reqd: 1,
							// fires once the picked shift is stored on the row
							onchange: () => schedulePreview(),
						},
					],
				},
				{
					fieldtype: "Int",
					fieldname: "weeks_per_shift",
					label: __("Weeks per Shift"),
					default: 1,
					depends_on: "eval:(doc.rotation || []).some((r) => r.shift_type)",
					change: schedulePreview,
				},
				{ fieldtype: "HTML", fieldname: "rotation_preview" },
			],
			primary_action_label: __("Change Shift"),
			primary_action(values) {
				if (saving) return;
				const rotation = (values.rotation || []).map((r) => r.shift_type).filter(Boolean);
				if (!values.shift_type && !rotation.length) {
					dialog.get_field("shift_type").$input.focus();
					return;
				}
				if (values.to_date < values.from_date) {
					frappe.msgprint(__("End Date cannot be before {0}.", [day(values.from_date)]));
					return;
				}
				saving = true;
				dialog.disable_primary_action();
				frappe
					.call({
						method: CHANGE_SHIFT_METHOD,
						type: "POST",
						args: {
							employee: row.employee,
							from_date: values.from_date,
							to_date: values.to_date,
							shift_type: values.shift_type || "",
							rotation: JSON.stringify(rotation),
							weeks_per_shift: values.weeks_per_shift || 1,
						},
					})
					.then((r) => {
						const result = (r && r.message) || {};
						changed = true;
						const lines = [
							...(result.blocks || []).map((b) =>
								__("{0}: {1} from {2} to {3}.", [
									who,
									frappe.utils.escape_html(b.shift_type),
									day(b.from_date),
									day(b.to_date),
								])
							),
							result.restored_to &&
								__("Back on {0} from {1}.", [
									frappe.utils.escape_html(result.restored_to),
									day(frappe.datetime.add_days(values.to_date, 1)),
								]),
							(result.absent_removed || []).length &&
								__("Absent removed on: {0}", [dates(result.absent_removed)]),
							(result.attendance_on_other_shift || []).length &&
								__("Still marked under another shift on: {0}", [dates(result.attendance_on_other_shift)]),
						];
						frappe.show_alert({ message: lines.filter(Boolean).join("<br>"), indicator: "green" }, 10);
						afterCache = {};
						resetEntry();
						return loadSchedule();
					})
					.always(() => {
						saving = false;
						dialog.enable_primary_action();
					});
			},
			secondary_action_label: __("Close"),
			secondary_action() {
				dialog.hide();
			},
			on_hide() {
				if (!changed) return;
				changed = false;
				// after the modal has finished closing: an error thrown inside
				// Bootstrap's hide event stops the close and leaves it stuck open
				setTimeout(() => {
					try {
						report.refresh();
					} catch (e) {
						console.warn("[MAS shift refresh]", e);
					}
				}, 300);
			},
		});
		// desk caps a grid's last column at 30px for the row's edit button,
		// which a dialog grid does not have, so Shift Type would be that column
		dialog.$wrapper.addClass("ta-shift-dialog");
		if (!document.getElementById("ta-shift-dialog-style")) {
			$("<style id='ta-shift-dialog-style'>")
				.text(
					`.ta-shift-dialog .form-grid .grid-row > .row .col.grid-static-col[data-fieldname="shift_type"] {
						max-width: none; min-width: 0; flex: 1 1 auto;
					}`
				)
				.appendTo(document.head);
		}
		dialog.show();
		// rows added, picked or deleted in Rotate With
		$(dialog.get_field("rotation").grid.wrapper).on(
			"change input click awesomplete-selectcomplete",
			schedulePreview
		);
		loadSchedule();
	}

	function patchPrototype() {
		const QR = frappe.views && frappe.views.QueryReport;
		if (!QR || !QR.prototype) return false;
		if (QR.prototype.__ta_mas_patched) return true;

		const origGetReportSettings = QR.prototype.get_report_settings;
		QR.prototype.get_report_settings = function () {
			const self = this;
			const out = origGetReportSettings.apply(this, arguments);
			return Promise.resolve(out).then((r) => {
				if (self.report_name === REPORT) injectCategoryFilter(self.report_settings);
				return r;
			});
		};

		const origPrepareColumns = QR.prototype.prepare_columns;
		QR.prototype.prepare_columns = function (columns) {
			try {
				if (this.report_name === REPORT && this.report_settings) {
					this.report_settings.formatter = leaveColorFormatter;


					if (!this.report_settings.__ta_gdo) {
						const origGDO = this.report_settings.get_datatable_options;
						this.report_settings.get_datatable_options = function (options) {
							options = origGDO ? origGDO(options) || options : options;
							options.saveSorting = false;
							options.events = Object.assign({}, options.events, {
								onSortColumn: onSortColumnPin,
							});
							return options;
						};
						this.report_settings.__ta_gdo = true;
					}
				}
			} catch (e) {
				
			}
			return origPrepareColumns.apply(this, arguments);
		};

		const origRender = QR.prototype.render_datatable;
		QR.prototype.render_datatable = function () {
			const out = origRender.apply(this, arguments);
			if (this.report_name === REPORT) {
				bindWeekOffChange(this);
				installSummaryObserver(this);
				setTimeout(() => {
					markSummaryRows(this);
				}, 50);
			}
			return out;
		};

		QR.prototype.__ta_mas_patched = true;

		try {
			const cur = frappe.query_report;
			if (cur && cur.report_name === REPORT) {
				if (cur.report_settings) cur.report_settings.formatter = leaveColorFormatter;
				if (cur.datatable && cur.data) cur.render_datatable();
			}
		} catch (e) {
			/* ignore */
		}

		return true;
	}

	if (!patchPrototype()) {
		const poll = setInterval(function () {
			if (patchPrototype()) clearInterval(poll);
		}, 300);
		setTimeout(function () {
			clearInterval(poll);
		}, 60000);
	}
})();
