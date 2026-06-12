app_name = "upande_ta"
app_title = "T&A"
app_publisher = "Upande LTD"
app_description = "Upande Time and Attendance"
app_email = "info@upande.com"
app_license = "mit"

add_to_apps_screen = [
	{
		"name": "upande_ta",
		"logo": "/assets/upande_ta/images/upande_logo.ico",
		"title": "T&A",
		"route": "/app/t%26a",
	}
]

doctype_js = {"Employee": "public/js/employee.js"}

after_install = [
	"upande_ta.patches.v1.ensure_daily_checkin_summary_report.execute",
	"upande_ta.install.ensure_desktop_icon",
]

# Frappe's scheduler sync deletes Scheduled Job Type rows whose method isn't declared in any
# app's scheduler_events. Biometric Setting-driven jobs are user-configured per device, so
# we re-upsert them after every migrate.
after_migrate = [
	"upande_ta.patches.v1.sanitize_link_filters.after_migrate_drop_check",
	"upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.resync_scheduled_jobs",
	"upande_ta.install.ensure_desktop_icon",
]

# `csf_ke` and `payroll_africa` both ship a Salary Component Custom Field with
# fieldname `p10a_tax_deduction_card_type` under different docnames. The second
# fixture to sync tries to INSERT a colliding fieldname and aborts migrate. This runs
# in `pre_schema_updates`, before `sync_fixtures()`, collapsing the duplicate to a
# single canonical row every migrate so fixtures UPDATE instead of INSERT-collide.
before_migrate = [
	"upande_ta.patches.v1.fix_p10a_duplicate_custom_field.execute",
]

doc_events = {
	"Employee Checkin": {
		"validate": "upande_ta.upande_ta.overrides.employee_checkin.prevent_duplicate"
	},
	"Employee": {
		"before_save": "upande_ta.upande_ta.overrides.employee.set_attendance_device_id",
		"after_insert": "upande_ta.upande_ta.overrides.employee.set_attendance_device_id",
	},
	"Workspace": {
		"validate": "upande_ta.upande_ta.overrides.workspace.validate",
		"on_trash": "upande_ta.upande_ta.overrides.workspace.on_trash",
	},
}

scheduler_events = {
	"daily": [
		"upande_ta.upande_ta.doctype.bulk_week_off.bulk_week_off.submit_due_employee_transfers"
	],
	"cron": {
		"* * * * *": [
			"upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.mark_stale_devices_offline_scheduled"
		]
	},
}
