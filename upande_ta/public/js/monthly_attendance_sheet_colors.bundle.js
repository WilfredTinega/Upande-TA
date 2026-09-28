
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
	function injectCategoryFilter(settings) {
		try {
			if (!settings || !Array.isArray(settings.filters)) return;
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
			if (!frappe.model.can_create("Holiday List Assignment")) return;
			if (frappe.boot && frappe.boot.upande_ta_week_off_change_disabled) return;

			const $cell = $(this).closest(".dt-cell");
			const colIndex = cint($cell.attr("data-col-index"));
			const rowIndex = $cell.attr("data-row-index");
			if (rowIndex === undefined) return;

			const column = dt.getColumn(colIndex) || {};
			const fieldname = (column.docfield && column.docfield.fieldname) || column.id || "";
			if (!DAY_RE.test(fieldname)) return;

			const row = dt.datamanager.getData(cint(rowIndex));
			if (!row || row._is_summary || !row.employee) return;

			const [day, month, year] = fieldname.split("-");
			const isWeekOff = String(row[fieldname] || "").replace(/<[^>]*>/g, "").trim() === "WO";
			showWeekOffDialog(report, row, `${year}-${month}-${day}`, isWeekOff);
		});
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
					const lines = [done(values)];
					if (result.restored_to) {
						lines.push(
							__("Back on {0} from {1}.", [
								frappe.utils.escape_html(result.restored_to),
								frappe.datetime.str_to_user(frappe.datetime.add_days(values.to_date, 1)),
							])
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

		const span = (values) =>
			[frappe.datetime.str_to_user(date), frappe.datetime.str_to_user(values.to_date)];

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
				const days = values.week_off_days || [];
				if (!days.length) {
					frappe.msgprint(__("Tick at least one Week Off Day."));
					return;
				}
				const ordered = WEEK_ORDER.filter((day) => days.includes(day));
				run(
					CHANGE_WEEK_OFF_METHOD,
					values,
					(v) =>
						__("{0}: {1} week off from {2} to {3}.", [
							who,
							ordered.map((day) => __(day)).join(" & "),
							...span(v),
						]),
					{ weekdays: JSON.stringify(ordered) }
				);
			},
		});

		if (isWeekOff) {
			dialog.set_secondary_action_label(__("Remove Week Off"));
			dialog.set_secondary_action(() => {
				run(REMOVE_WEEK_OFF_METHOD, dialog.get_values(true), (v) =>
					__("{0}: no {1} week off from {2} to {3}.", [who, __(weekday), ...span(v)])
				);
			});
		}
		dialog.show();
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
