
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

	// Extra filters (Employment Type, Unit/Division) added to HRMS' stock filter
	// list. Which ones the site can offer comes from the boot payload, since
	// Unit/Division is an Employee custom field that only some sites ship --
	// see overrides/monthly_attendance_sheet.py (extend_bootinfo).
	const EXTRA_FILTER_ANCHOR = "branch"; // slot them in right after this one

	function extraFilterDefs() {
		const defs = (frappe.boot && frappe.boot.upande_ta_attendance_filters) || [];
		return defs
			.filter((df) => df && df.fieldname && df.options)
			.map((df) => ({
				fieldname: df.fieldname,
				label: __(df.label || df.fieldname),
				fieldtype: "Link",
				options: df.options,
			}));
	}

	function addExtraFilters(settings) {
		try {
			if (!settings || !Array.isArray(settings.filters)) return;

			const existing = new Set(settings.filters.map((df) => df && df.fieldname));
			const missing = extraFilterDefs().filter((df) => !existing.has(df.fieldname));
			if (!missing.length) return;

			let at = settings.filters.findIndex(
				(df) => df && df.fieldname === EXTRA_FILTER_ANCHOR
			);
			at = at === -1 ? settings.filters.length : at + 1;
			settings.filters.splice(at, 0, ...missing);
		} catch (e) {
			console.warn("[MAS filters]", e);
		}
	}

	function patchPrototype() {
		const QR = frappe.views && frappe.views.QueryReport;
		if (!QR || !QR.prototype) return false;
		if (QR.prototype.__ta_mas_patched) return true;

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

		const origSetupFilters = QR.prototype.setup_filters;
		QR.prototype.setup_filters = function () {
			if (this.report_name === REPORT) addExtraFilters(this.report_settings);
			return origSetupFilters.apply(this, arguments);
		};

		const origRender = QR.prototype.render_datatable;
		QR.prototype.render_datatable = function () {
			const out = origRender.apply(this, arguments);
			if (this.report_name === REPORT) {
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
