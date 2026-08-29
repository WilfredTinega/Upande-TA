// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.query_reports["Stock Entry Biometric Verification"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "stock_entry_type",
			label: __("Stock Entry Type"),
			fieldtype: "Link",
			options: "Stock Entry Type",
			get_query: () => ({ filters: { require_biometric: 1 } }),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "biometric_status",
			label: __("Verification Status"),
			fieldtype: "Select",
			options: "\nPending\nVerified\nFailed",
		},
		{
			fieldname: "include_cancelled",
			label: __("Include Cancelled"),
			fieldtype: "Check",
			default: 0,
		},
	],
};
