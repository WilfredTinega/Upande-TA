# Copyright (c) 2026, Upande LTD and contributors

app_name = "upande_ta"
app_title = "T&A"
app_publisher = "Upande LTD"
app_description = "Upande Time and Attendance"
app_email = "info@upande.com"
app_license = "mit"

# Shown in the About dialog's app list and the navbar (Frappe reads this hook
# per app; without it the app falls back to a generic letter tile). The nav
# records themselves (Desktop Icon / Workspace Sidebar / Workspace) ARE shipped
# by the app and force-resynced on every migrate -- see upande_ta/migrate.py.
app_logo_url = "/assets/upande_ta/images/upande_logo.ico"

required_apps = ["hrms"]


doctype_js = {
	"Employee": "public/js/employee.js",
	"Stock Entry": "public/js/stock_entry.js",
}


before_request = [
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.apply_patch",
]


before_job = [
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.apply_patch",
]


app_include_js = [
	
	"monthly_attendance_sheet_colors.bundle.js",
]

# One entry point each, so install and migrate apply the exact same set of steps
# and a failure in any one of them is logged instead of aborting the rest. See
# upande_ta/migrate.py for the ordered list, which covers:
#   * force-reloading the module-level JSON resources the app ships (doctypes,
#     reports, print formats) past Frappe's timestamp/hash skip;
#   * the Scheduled Job Type rows configured per Biometric Setting, which the
#     scheduler sync prunes on every migrate;
#   * the Custom HTML Block and the custom fields added to HRMS/ERPNext doctypes;
#   * the Desk nav the app ships (Workspace + Workspace Sidebar + Desktop Icon),
#     normalising the workspace identity and collapsing duplicate tiles.
after_install = "upande_ta.migrate.after_install"

before_uninstall = [
	"upande_ta.upande_ta.overrides.leave_type.remove_abbreviation_field",
	"upande_ta.upande_ta.overrides.stock_entry.remove_biometric_stock_entry_fields",
]


after_migrate = "upande_ta.migrate.after_migrate"

override_doctype_class = {
	"Overtime Slip": "upande_ta.upande_ta.overrides.overtime_slip.UpandeOvertimeSlip",
}

doc_events = {
	"Employee Checkin": {
		"validate": "upande_ta.upande_ta.overrides.employee_checkin.prevent_duplicate"
	},
	"Employee": {
		"before_save": "upande_ta.upande_ta.overrides.employee.set_attendance_device_id",
		"after_insert": "upande_ta.upande_ta.overrides.employee.set_attendance_device_id",
		"on_update": "upande_ta.upande_ta.overrides.employee.sync_attendance_device_id_change",
	},
	"Workspace": {
		"validate": "upande_ta.upande_ta.overrides.workspace.validate",
		"on_trash": "upande_ta.upande_ta.overrides.workspace.on_trash",
	},
	"Stock Entry": {
		"validate": "upande_ta.upande_ta.overrides.stock_entry.auto_verify_biometric",
	},
	"Biometric Logs": {
		"after_insert": "upande_ta.upande_ta.overrides.stock_entry.verify_pending_stock_entries",
		"on_update": "upande_ta.upande_ta.overrides.stock_entry.verify_pending_stock_entries",
	},
	"Meal Checkin": {
		"after_insert": "upande_ta.upande_ta.meal_checkin_receipt.attach_receipt",
	},
}

scheduler_events = {
	"cron": {
		"0 0 * * *": [
			"upande_ta.upande_ta.doctype.bulk_week_off.bulk_week_off.submit_due_employee_transfers"
		],
		"* * * * *": [
			"upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.mark_stale_devices_offline_scheduled"
		],
	},
}
