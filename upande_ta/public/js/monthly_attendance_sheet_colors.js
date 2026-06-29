// Monthly Attendance Sheet client enhancements (kaitet/mona, via upande_ta):
//   1. Color the per-leave-type abbreviation cells (ML, PL, UL, AL, CL, SLFP,
//      ...) blue, matching how the stock report colors the generic "L".
//      Abbreviations come from the server patch (overrides/
//      monthly_attendance_sheet.py); HRMS' own formatter only knows "L".
//   2. Add "Freeze up to this column" / "Unfreeze columns" to the column-header
//      dropdown so the user can pin the identity columns (Employee, Name, Shift)
//      and scroll the day columns underneath. frappe-datatable has no native
//      freeze, so we pin the chosen .dt-cell--col-N cells with position:sticky.

frappe.provide("frappe.views");

(function () {
	const REPORT = "Monthly Attendance Sheet";
	// Per-report-instance freeze boundary: freeze columns 0..N inclusive.
	// Stored on the QueryReport instance as `__ta_freeze_upto` (-1 = none).

	// ---- 1. Leave-type coloring -------------------------------------------
	// A day column's fieldname is a date like "07-05-2026".
	const DAY_RE = /^\d{2}-\d{2}-\d{4}$/;
	// Codes we recognise as attendance statuses. Anything else (shift names,
	// summary count numbers, blanks) is left untouched.
	const STATUS_CODES = /^(P|A|WFH|H|WO|HD\/P|HD\/A)$/;

	function leaveColorFormatter(value, row, column, data, default_formatter) {
		// Capture the raw cell value BEFORE default_formatter runs, so we can
		// render summary zeros correctly (default_formatter / `value || ""`
		// would otherwise turn the falsy 0 into a blank).
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

		// Bold the appended summary rows (Present/Absent/.../Total Headcount)
		// and leave their numbers uncolored — they are totals, not statuses.
		// Use rawValue so 0 stays "0" rather than collapsing to blank.
		if (data && data._is_summary) {
			if (rawValue === null || rawValue === undefined || rawValue === "") return value;
			return "<b>" + rawValue + "</b>";
		}

		// Only color the day grid in the detailed view.
		if (summarized_view) return value;

		// Only color actual day columns (fieldname = dd-mm-yyyy) — never the
		// Employee/Name/Shift columns.
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

	// ---- 2. Column freezing (sticky) --------------------------------------
	// Inject freeze-related CSS once: header dropdown must float above the
	// frozen cells, and a header cell with an open dropdown jumps to the front.
	function ensureFreezeStyle() {
		if (document.getElementById("ta-mas-freeze-style")) return;
		// The column dropdown list (.dt-dropdown__list) is `position: fixed`
		// (viewport-positioned) but ships with z-index:1, so once frozen/header
		// cells get higher z-indexes the menu is covered by the columns to its
		// right. Lift the container + list above every cell, and keep the items
		// on one line each so the menu keeps its full width (no left squeeze).
		const css =
			".dt-cell.ta-frozen.dt-cell--header { z-index: 20 !important; }" +
			".dt-cell.ta-frozen { z-index: 11 !important; }" +
			// frozen filter (search) cells: opaque backdrop on the CELL, but the
			// search input pill must stay visible on top with its own control bg
			".dt-cell.ta-frozen.dt-cell--filter { background: var(--fg-color, #fff) !important; }" +
			".dt-cell.ta-frozen.dt-cell--filter .dt-filter.dt-input { background-color: var(--control-bg, #f4f5f6) !important; opacity: 1 !important; }" +
			".dt-dropdown-container { z-index: 100000 !important; }" +
			".dt-dropdown__list { z-index: 100000 !important; }" +
			".dt-dropdown__list-item { white-space: nowrap !important; }";
		const style = document.createElement("style");
		style.id = "ta-mas-freeze-style";
		style.textContent = css;
		document.head.appendChild(style);
	}

	function clearFreeze(wrapper) {
		wrapper.querySelectorAll(".dt-cell.ta-frozen").forEach((el) => {
			el.classList.remove("ta-frozen", "dt-cell--header");
			el.style.position = "";
			el.style.left = "";
			el.style.zIndex = "";
			el.style.background = "";
			el.style.boxShadow = "";
			el.style.overflow = "";
		});
	}

	function applyFreeze(report) {
		try {
			const wrapper = report && report.$report && report.$report[0];
			if (!wrapper) return;

			ensureFreezeStyle();
			clearFreeze(wrapper);

			const upto = typeof report.__ta_freeze_upto === "number" ? report.__ta_freeze_upto : -1;
			if (upto < 0) return;

			let bg = "#fff";
			try {
				const c = getComputedStyle(wrapper).backgroundColor;
				if (c && c !== "rgba(0, 0, 0, 0)" && c !== "transparent") bg = c;
			} catch (e) {
				/* keep default */
			}

			let left = 0;
			for (let colIndex = 0; colIndex <= upto; colIndex++) {
				const headerCell = wrapper.querySelector(".dt-header .dt-cell--col-" + colIndex);
				const width = headerCell ? headerCell.getBoundingClientRect().width : 0;

				wrapper.querySelectorAll(".dt-cell--col-" + colIndex).forEach((cell) => {
					const isHeader = !!cell.closest(".dt-header");
					const isFilter = cell.classList.contains("dt-cell--filter");
					cell.classList.add("ta-frozen");
					if (isHeader) {
						cell.classList.add("dt-cell--header");
						// let the column dropdown escape the sticky cell box
						cell.style.overflow = "visible";
					}
					cell.style.position = "sticky";
					cell.style.left = left + "px";
					// z-index handled by injected CSS (header > body), keep header
					// cells reliably above scrolling content here too
					cell.style.zIndex = isHeader ? 20 : 11;
					// Paint an opaque backdrop so scrolling cells don't show
					// through — but NOT on the filter row, whose search input pill
					// would otherwise be hidden behind the cell background.
					if (!isFilter) {
						cell.style.background = bg;
					}
					// subtle divider on the last frozen column
					if (colIndex === upto) {
						cell.style.boxShadow = "2px 0 4px -2px rgba(0,0,0,0.25)";
					}
				});

				left += width;
			}
		} catch (e) {
			console.warn("[MAS freeze]", e);
		}
	}

	// Move the datatable's dropdown container out to <body> so the column
	// header menu can never be clipped by the table's overflow:hidden box.
	// Idempotent; the menu is positioned with viewport coords so it stays put.
	// The column dropdown (.dt-dropdown__list) is position:fixed but with a tiny
	// z-index (1), so once we give frozen/header cells higher z-indexes it gets
	// covered by the columns to its right. We don't move it in the DOM (that
	// caused duplicate menus); we only ensure the stacking CSS is present and
	// the menu width is preserved so it stays readable.
	function portalDropdownContainer(report) {
		try {
			ensureFreezeStyle();
			// clean up any container left in <body> by an earlier (portal-based)
			// version of this script, which caused a duplicated menu
			document
				.querySelectorAll("body > .dt-dropdown-container.ta-portaled")
				.forEach((el) => el.remove());
		} catch (e) {
			console.warn("[MAS dropdown css]", e);
		}
	}

	// Inject the border/styling for the summary block once.
	function ensureSummaryStyle() {
		if (document.getElementById("ta-mas-summary-style")) return;
		const css =
			".dt-row.ta-summary-row .dt-cell { background:#f7f7f7 !important; }" +
			// black top border on the first summary row marks where totals begin
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
				// border on the first summary row (the spacer) = top of the block
				if (!firstSummaryMarked) {
					$row.classList.add("ta-summary-top");
					firstSummaryMarked = true;
				}
			});
		} catch (e) {
			console.warn("[MAS summary]", e);
		}
	}

	function buildHeaderDropdown(report) {
		// Start from the datatable's default items (sort/remove) then append ours.
		const base = [
			{
				label: __("Sort Ascending"),
				action: function (column) {
					this.sortColumn(column.colIndex, "asc");
				},
			},
			{
				label: __("Sort Descending"),
				action: function (column) {
					this.sortColumn(column.colIndex, "desc");
				},
			},
			{
				label: __("Reset sorting"),
				action: function (column) {
					this.sortColumn(column.colIndex, "none");
				},
			},
			{
				label: __("Remove column"),
				action: function (column) {
					this.removeColumn(column.colIndex);
				},
			},
			{
				label: __("❄ Freeze up to this column"),
				action: function (column) {
					report.__ta_freeze_upto = column.colIndex;
					applyFreeze(report);
				},
			},
			{
				label: __("Unfreeze columns"),
				action: function () {
					report.__ta_freeze_upto = -1;
					applyFreeze(report);
				},
			},
		];
		return base;
	}

	// ---- Prototype patch (install once QueryReport class is loaded) --------
	function patchPrototype() {
		const QR = frappe.views && frappe.views.QueryReport;
		if (!QR || !QR.prototype) return false;
		if (QR.prototype.__ta_mas_patched) return true;

		// Install our formatter + custom header dropdown just before columns are
		// baked into the datatable.
		const origPrepareColumns = QR.prototype.prepare_columns;
		QR.prototype.prepare_columns = function (columns) {
			try {
				if (this.report_name === REPORT && this.report_settings) {
					this.report_settings.formatter = leaveColorFormatter;
					const report = this;
					this.report_settings.get_datatable_options = function (options) {
						options = options || {};
						options.headerDropdown = buildHeaderDropdown(report);
						return options;
					};
				}
			} catch (e) {
				/* never break the report */
			}
			return origPrepareColumns.apply(this, arguments);
		};

		// Re-apply freezing + summary styling after every datatable render.
		const origRender = QR.prototype.render_datatable;
		QR.prototype.render_datatable = function () {
			const out = origRender.apply(this, arguments);
			if (this.report_name === REPORT) {
				setTimeout(() => {
					portalDropdownContainer(this);
					applyFreeze(this);
					markSummaryRows(this);
				}, 50);
			}
			return out;
		};

		QR.prototype.__ta_mas_patched = true;

		// If the report is already open and rendered, apply now.
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

	// Re-measure freeze offsets when the window resizes (column widths change).
	$(window).on("resize.ta_mas_freeze", function () {
		const cur = frappe.query_report;
		if (cur && cur.report_name === REPORT && cur.datatable) {
			applyFreeze(cur);
		}
	});
})();
