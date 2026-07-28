// Copyright (c) 2026, Upande LTD and contributors

// Monthly Attendance Report — upande_ta's standard clone of HRMS' Monthly
// Attendance Sheet (server side: monthly_attendance_report.py).
//
// Deliberately almost empty. The filter list, the onload that populates the Year
// options, the cell formatter and the summary-row styling are all mirrored from
// "Monthly Attendance Sheet" at runtime by
// upande_ta/public/js/monthly_attendance_sheet_colors.bundle.js, which is loaded
// on every desk page via app_include_js. Re-declaring the filters here would fork
// them from HRMS' list and let the two reports drift apart, which is exactly what
// this report exists to prevent.
//
// The empty filters array is only a placeholder so frappe has a settings object
// to work with before the mirror resolves.
frappe.query_reports["Monthly Attendance Report"] = {
	filters: [],
};
