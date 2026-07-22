# Copyright (c) 2026, Upande LTD and contributors

app_name = "upande_ta"
app_title = "T&A"
app_publisher = "Upande LTD"
app_description = "Upande Time and Attendance"
app_email = "info@upande.com"
app_license = "mit"

# Shown in the About dialog's app list and the navbar (Frappe reads this hook
# per app; without it the app falls back to a generic letter tile). Same Upande
# logo used by the launcher Desktop Icon (see install.ensure_desktop_icon).
app_logo_url = "/assets/upande_ta/images/upande_logo.ico"

required_apps = ["hrms"]


doctype_js = {
	"Employee": "public/js/employee.js",
	"Stock Entry": "public/js/stock_entry.js",
}


before_request = [
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.apply_patch",
	"upande_ta.upande_ta.overrides.shift_type.apply_patch",
]


before_job = [
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.apply_patch",
	"upande_ta.upande_ta.overrides.shift_type.apply_patch",
]


app_include_js = [
	
	"monthly_attendance_sheet_colors.bundle.js",
]

after_install = [
	"upande_ta.install.ensure_desktop_icon",
	"upande_ta.install.ensure_ta_dashboard_block",
	"upande_ta.upande_ta.overrides.leave_type.ensure_abbreviation_field",
	"upande_ta.upande_ta.overrides.stock_entry.ensure_biometric_stock_entry_fields",
]

before_uninstall = [
	"upande_ta.upande_ta.overrides.leave_type.remove_abbreviation_field",
	"upande_ta.upande_ta.overrides.stock_entry.remove_biometric_stock_entry_fields",
]


after_migrate = [
	"upande_ta.patches.v1.sanitize_link_filters.after_migrate_drop_check",
	"upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.resync_scheduled_jobs",
	"upande_ta.install.ensure_desktop_icon",
	"upande_ta.install.ensure_ta_dashboard_block",
	"upande_ta.upande_ta.overrides.leave_type.ensure_abbreviation_field",
	"upande_ta.upande_ta.overrides.stock_entry.ensure_biometric_stock_entry_fields",
	"upande_ta.upande_ta.cleanup.remove_orphans",
	"upande_ta.upande_ta.doctype.bulk_overtime.bulk_overtime.ensure_overtime_setup",
	"upande_ta.upande_ta.overrides.leave_application.ensure_rejected_by_field",
	# HRMS ships Monthly Attendance Sheet with prepared_report=1, which re-enables
	# the background/cached "click Rebuild" mode on every migrate. Force it back off
	# so the report always renders live (our override does the heavy lifting anyway).
	"upande_ta.upande_ta.overrides.monthly_attendance_sheet.disable_prepared_report",
]

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
	"Leave Application": {
		"before_submit": "upande_ta.upande_ta.overrides.leave_application.capture_rejecter",
		"on_update_after_submit": "upande_ta.upande_ta.overrides.leave_application.capture_rejecter",
		"before_cancel": "upande_ta.upande_ta.overrides.leave_application.enforce_cancel_by_rejecter",
		# only at creation of the amended doc -- NOT on every later save, or it
		# would block the next approver from acting on the amended application.
		"before_insert": "upande_ta.upande_ta.overrides.leave_application.enforce_amend_by_rejecter",
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
		"*/5 * * * *": [
			"upande_ta.upande_ta.attendance_cleanup.cancel_absent_attendance_with_checkin"
		],
	},
}
