// Monthly Attendance Sheet client enhancement (kaitet/mona, via upande_ta):
// Color the per-leave-type abbreviation cells (ML, PL, UL, AL, CL, SLFP, ...)
// blue, matching how the stock report colors the generic "L". Abbreviations come
// from the server patch (overrides/monthly_attendance_sheet.py); HRMS' own
// formatter only knows "L". Also bold/border the appended summary rows.
//
// Column freezing/pinning is intentionally NOT handled here — frappe-datatable
// now provides native "Freeze up to this column" / "Unfreeze columns", so a
// custom implementation would only duplicate the header menu.

frappe.provide("frappe.views");

(function () {
	const REPORT = "Monthly Attendance Sheet";

	// A day column's fieldname is a date like "07-05-2026".
	const DAY_RE = /^\d{2}-\d{2}-\d{4}$/;
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
			".dt-row.ta-summary-row .dt-cell { background:#f7f7f7 !important; }" +
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

	function patchPrototype() {
		const QR = frappe.views && frappe.views.QueryReport;
		if (!QR || !QR.prototype) return false;
		if (QR.prototype.__ta_mas_patched) return true;

		const origPrepareColumns = QR.prototype.prepare_columns;
		QR.prototype.prepare_columns = function (columns) {
			try {
				if (this.report_name === REPORT && this.report_settings) {
					this.report_settings.formatter = leaveColorFormatter;
				}
			} catch (e) {
				/* never break the report */
			}
			return origPrepareColumns.apply(this, arguments);
		};

		const origRender = QR.prototype.render_datatable;
		QR.prototype.render_datatable = function () {
			const out = origRender.apply(this, arguments);
			if (this.report_name === REPORT) {
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
