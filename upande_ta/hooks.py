# Copyright (c) 2026, Upande LTD and contributors

app_name = "upande_ta"
app_title = "T&A"
app_publisher = "Upande LTD"
app_description = "Upande Time and Attendance"
app_email = "info@upande.com"
app_license = "mit"

# Shown in the About dialog's app list and the navbar (Frappe reads this hook
# per app; without it the app falls back to a generic letter tile). The nav
# records (Desktop Icon / Workspace Sidebar / Workspace) ship as files and are
# re-synced on every migrate -- migrate's orphan sweep deletes any of them that
# has no matching JSON in an installed app, so they cannot live in the DB alone.
app_logo_url = "/assets/upande_ta/images/upande_logo.ico"

required_apps = ["hrms"]


doctype_js = {
	"Employee": "public/js/employee.js",
	"Stock Entry": "public/js/stock_entry.js",
}

doctype_list_js = {
	"Employee": "public/js/employee_list.js",
}


# The dashboard was renamed from /attendance-dashboard to /attendance-insights,
# which is what the page has always called itself. Bookmarks and any link that
# went out before the rename would otherwise 404.
website_redirects = [
	{"source": "/attendance-dashboard", "target": "/attendance-insights"},
]


before_request = [
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.apply_patch",
]


before_job = [
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.apply_patch",
]


# Tells the desk which of the extra Monthly Attendance Sheet filters this site
# can offer (Unit/Division only exists where the Employee custom field does), so
# the client patch can render them without an extra round trip.
extend_bootinfo = [
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.extend_bootinfo",
]


app_include_js = [
	"monthly_attendance_sheet_colors.bundle.js",
	# Signed QZ Tray printing. Loaded on every page so any form can print to the
	# local receipt printer; qz-tray.js itself is fetched only on terminals that
	# have a printer saved. See upande_ta/upande_ta/api/qz.py.
	"/assets/upande_ta/js/qz_bridge.js",
]

# The Printer Settings page lives on the website, not the desk.
web_include_js = [
	"/assets/upande_ta/js/qz_bridge.js",
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
# Keep these one string each and next to each other: a second assignment to the
# same hook silently shadows the first, which is how the resource resync and the
# nav normalisation stopped running once before.
after_install = "upande_ta.migrate.after_install"
after_migrate = "upande_ta.migrate.after_migrate"

before_uninstall = [
	"upande_ta.upande_ta.overrides.leave_type.remove_abbreviation_field",
	"upande_ta.upande_ta.overrides.stock_entry.remove_biometric_stock_entry_fields",
]

override_doctype_class = {
	"Overtime Slip": "upande_ta.upande_ta.overrides.overtime_slip.UpandeOvertimeSlip",
	# An auto-marked Absent must not block the record that corrects it: a late
	# check-in, or a supervisor marking someone Present. Stock ERPNext throws
	# DuplicateAttendanceError, which the check-in path answers by flagging the
	# scan `skip_auto_attendance` — losing it for good.
	"Attendance": "upande_ta.upande_ta.overrides.attendance.UpandeAttendance",
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

permission_query_conditions = {
	"Gate Pass": "upande_ta.upande_ta.doctype.gate_pass.gate_pass.get_permission_query_conditions",
}

has_permission = {
	"Gate Pass": "upande_ta.upande_ta.doctype.gate_pass.gate_pass.has_permission",
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
