// Client enhancement for the two monthly attendance reports (kaitet/mona, via
// upande_ta). Wired into app_include_js as
// "monthly_attendance_sheet_colors.bundle.js", so it loads on every desk page and
// patches frappe.views.QueryReport once.
//
// It covers BOTH reports listed in REPORTS:
//   - "Monthly Attendance Sheet"  — HRMS' standard report, server-side patched by
//     upande_ta/upande_ta/overrides/monthly_attendance_sheet.py
//   - "Monthly Attendance Report" — upande_ta's standard clone
//     (upande_ta/upande_ta/report/monthly_attendance_report/) whose execute()
//     delegates to that same patched implementation
//
// What it does:
//   - colors the per-leave-type abbreviation cells (ML, PL, UL, AL, CL, SLFP, ...)
//     blue, matching how the stock report colors the generic "L". Abbreviations
//     come from the server patch; HRMS' own formatter only knows "L".
//   - bolds/borders the appended summary rows and keeps them pinned to the bottom
//     when a column is sorted.
//   - injects the "Category" (Employee Grade) filter the server override reads.
//   - for "Monthly Attendance Report", mirrors the sheet's client settings
//     (filter list, year population) instead of duplicating them, so the two
//     reports cannot drift apart.
//
// Column freezing/pinning is intentionally NOT handled here — frappe-datatable
// now provides native "Freeze up to this column" / "Unfreeze columns", so a
// custom implementation would only duplicate the header menu.

frappe.provide("frappe.views");

(function () {
	// The report whose client settings are authoritative: HRMS ships its filter
	// list and onload in a file-based standard report JS.
	const SHEET = "Monthly Attendance Sheet";
	// upande_ta's clone, which mirrors SHEET's settings at runtime.
	const MIRROR = "Monthly Attendance Report";
	const REPORTS = new Set([SHEET, MIRROR]);

	const isTargetReport = (name) => REPORTS.has(name);

	// A day column's fieldname is a date like "07-05-2026".
	const DAY_RE = /^\d{2}-\d{2}-\d{4}$/;

	// The server puts the summary row's label in the first column (see
	// build_summary_rows) so it can be rendered spanning the empty leading columns.
	const SUMMARY_LABEL_FIELD = "employee";

	// Codes we recognise as attendance statuses. Anything else (shift names,
	// summary count numbers, blanks) is left untouched.
	const STATUS_CODES = /^(P|A|WFH|H|WO|HD\/P|HD\/A)$/;

	function leaveColorFormatter(value, row, column, data, default_formatter) {
		const rawValue = value;

		value = default_formatter ? default_formatter(value, row, column, data) : value;

		let summarized_view, group_by;
		try {
			summarized_view = frappe.query_report.get_filter_value("summarized_view");
			group_by = frappe.query_report.get_filter_value("group_by");
		} catch (e) {
			/* filters not ready */
		}

		if (group_by && column.colIndex === 1) {
			value = "<strong>" + value + "</strong>";
		}

		if (data && data._is_summary) {
			if (rawValue === null || rawValue === undefined || rawValue === "") return value;
			const fn = column && (column.fieldname || column.id);

			if (fn === SUMMARY_LABEL_FIELD) {
				// Absolutely positioned so the label reads across the narrow
				// Employee / Employee Name / Shift columns instead of being clipped.
				return (
					"<b class='ta-summary-label' style=\"position:absolute; left:0; top:0; bottom:0;" +
					" display:flex; align-items:center; padding-left:15px; white-space:nowrap;" +
					" z-index:5; background:#f7f7f7;\">" +
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
	function ensureSummaryStyle() {
		if (document.getElementById("ta-mas-summary-style")) return;
		const css =
			// position:relative anchors the absolutely positioned summary label above.
			".dt-row.ta-summary-row .dt-cell { background:#f7f7f7 !important; position:relative; }" +
			".dt-row.ta-summary-top .dt-cell { border-top:2px solid #000 !important; }";
		const style = document.createElement("style");
		style.id = "ta-mas-summary-style";
		style.textContent = css;
		document.head.appendChild(style);
	}

	// Tag the appended summary rows so CSS can bold/border them as a block.
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

	// frappe-datatable re-renders rows on scroll/resize, which drops our classes;
	// re-tag on any mutation of the report wrapper.
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

	// Keep the summary block at the bottom after the user sorts a column.
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
			if (!rep || !isTargetReport(rep.report_name)) return;
			pinSummaryRows(this, rep.data);
			if (this.rowmanager && this.rowmanager.refreshRows) this.rowmanager.refreshRows();
			setTimeout(() => markSummaryRows(rep), 30);
		} catch (e) {
			console.warn("[MAS onSortColumn]", e);
		}
	}

	// Add a "Category" filter (Link → Employee Grade). The server override reads
	// filters.category and applies WHERE e.grade = category. Both reports' filter
	// lists come from a file-based standard report JS, so we splice ours in at
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

	// Resolve SHEET's client settings, loading its report script if this desk
	// session has not opened the sheet yet. Mirrors what QueryReport itself does
	// (xcall get_script -> frappe.dom.eval), so there is one definition of the
	// filter list and MIRROR can never drift from it.
	function getSheetSettings() {
		if (frappe.query_reports[SHEET]) return Promise.resolve(frappe.query_reports[SHEET]);
		return frappe
			.xcall("frappe.desk.query_report.get_script", { report_name: SHEET })
			.then((settings) => {
				frappe.dom.eval(settings.script);
				return frappe.query_reports[SHEET];
			})
			.catch((e) => {
				console.warn("[MAS mirror] could not load " + SHEET + " settings", e);
				return null;
			});
	}

	// Point MIRROR's report_settings at a copy of SHEET's. Each filter definition
	// is shallow-copied so per-instance mutation (e.g. set_reqd_filter flipping
	// df.reqd when Filter Based On changes) cannot leak between the two reports.
	function mirrorSheetSettings(report) {
		return getSheetSettings().then((sheet) => {
			if (!sheet) return;
			const own = report.report_settings || {};
			report.report_settings = Object.assign({}, sheet, {
				filters: (sheet.filters || []).map((df) => Object.assign({}, df)),
				html_format: own.html_format,
				execution_time: own.execution_time || 0,
			});
			frappe.query_reports[MIRROR] = report.report_settings;
		});
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
				if (!isTargetReport(self.report_name)) return r;
				const ready =
					self.report_name === MIRROR ? mirrorSheetSettings(self) : Promise.resolve();
				return ready.then(() => {
					injectCategoryFilter(self.report_settings);
					return r;
				});
			});
		};

		const origPrepareColumns = QR.prototype.prepare_columns;
		QR.prototype.prepare_columns = function (columns) {
			try {
				if (isTargetReport(this.report_name) && this.report_settings) {
					this.report_settings.formatter = leaveColorFormatter;

					// Disable persisted sorting and pin the summary block on sort.
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
				/* never break the report */
			}
			return origPrepareColumns.apply(this, arguments);
		};

		const origRender = QR.prototype.render_datatable;
		QR.prototype.render_datatable = function () {
			const out = origRender.apply(this, arguments);
			if (isTargetReport(this.report_name)) {
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
			if (cur && isTargetReport(cur.report_name)) {
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
