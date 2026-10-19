# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Call HRMS "HR" whichever desktop the site shows.

Frappe 16.50+ has two (Desktop Settings → Desktop Page):

* Apps, the default: no Desktop Icon rows at all; each app's name comes from
  its ``add_to_apps_screen`` hook -- HRMS' says "Frappe HR" -- through
  ``bootinfo.app_data`` and ``frappe.apps.get_apps``. Both are relabelled here.
* Desktop Icons: HRMS ships a "Frappe HR" Desktop Icon, renamed by
  ``migrate.rename_hr_app_icon`` on every migrate. Switching to this page
  imports the icons in a background job, between migrates, so a newly
  inserted HRMS icon is renamed as soon as it is committed, and the icons the
  desk is handed are relabelled too in case a rename has not run yet.
"""

import frappe

from upande_ta.migrate import _HR_ICON, _HR_ICON_SHIPPED

HR_APP = "hrms"


def extend_bootinfo(bootinfo=None):
	try:
		for app in (bootinfo or {}).get("app_data") or []:
			if app.get("app_name") == HR_APP:
				app["app_title"] = _HR_ICON
		for icon in (bootinfo or {}).get("desktop_icons") or []:
			if icon.get("label") == _HR_ICON_SHIPPED and icon.get("app") == HR_APP:
				icon["label"] = _HR_ICON
	except Exception:
		frappe.log_error(frappe.get_traceback(), "upande_ta: HR app title")


def on_desktop_icon_insert(doc, method=None):
	"""Desktop Icon after_insert: HRMS' icon arriving between migrates (the
	switch to the Desktop Icons page imports it) is renamed once committed."""
	if doc.name == _HR_ICON_SHIPPED and not frappe.flags.upande_ta_renaming_hr_icon:
		frappe.db.after_commit.add(rename_hr_icon_quietly)


def rename_hr_icon_quietly():
	from upande_ta.migrate import rename_hr_app_icon

	frappe.flags.upande_ta_renaming_hr_icon = True
	try:
		rename_hr_app_icon()
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "upande_ta: rename HR desktop icon")
	finally:
		frappe.flags.upande_ta_renaming_hr_icon = False


@frappe.whitelist()
def get_apps():
	from frappe.apps import get_apps as frappe_get_apps

	apps = frappe_get_apps()
	for app in apps or []:
		if app.get("name") == HR_APP:
			app["title"] = _HR_ICON
	return apps
