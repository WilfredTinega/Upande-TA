"""Install / migrate orchestration for Upande TA.

Everything this app owns but that a plain doctype sync does not restore is
re-applied here: the JSON resources Frappe skips on a modified-timestamp or hash
match, the custom fields the app adds to HRMS doctypes, the per-Settings
Scheduled Job Type rows, the shipped Custom HTML Block, and the Desk nav records
that every orphan sweep wants to delete.

`after_migrate()` is the single entry point wired into hooks. It runs every step
in isolation: a failure is logged and the remaining steps still run, so one
broken piece can never abort the migrate or silently skip the rest of the app.
"""

import json
import os

import frappe

APP_NAME = "upande_ta"
MODULE_NAME = "Upande TA"


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #


def _steps():
	"""Every after_install / after_migrate action, in dependency order."""
	from upande_ta.install import ensure_ta_dashboard_block
	from upande_ta.patches.v1.sanitize_link_filters import after_migrate_drop_check
	from upande_ta.upande_ta.api.absent_marking import ensure_absent_marking_defaults
	from upande_ta.upande_ta.api.attendance_insights import ensure_attendance_insights_fields
	from upande_ta.upande_ta.cleanup import remove_orphans
	from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import (
		resync_scheduled_jobs,
	)
	from upande_ta.upande_ta.doctype.bulk_overtime.bulk_overtime import ensure_overtime_setup
	from upande_ta.upande_ta.api.attendance_insights import ensure_attendance_insights_fields
	from upande_ta.upande_ta.overrides.leave_type import ensure_abbreviation_field
	from upande_ta.upande_ta.overrides.monthly_attendance_sheet import disable_prepared_report
	from upande_ta.upande_ta.overrides.stock_entry import ensure_biometric_stock_entry_fields

	return (
		# 1. Force-reload the JSON resources we ship (doctypes, reports, print
		#    formats) past Frappe's timestamp/hash skip.
		("resync_app_resources", resync_app_resources),
		("sanitize_link_filters", after_migrate_drop_check),
		# 2. Restore the Scheduled Job Type rows configured per Settings doc —
		#    they are not in scheduler_events, so the scheduler sync prunes them.
		("biometric_resync_scheduled_jobs", resync_scheduled_jobs),
		# 3. Records Frappe cannot sync from the app folder at all, and the custom
		#    fields this app adds to HRMS/ERPNext doctypes.
		("ensure_ta_dashboard_block", ensure_ta_dashboard_block),
		("ensure_attendance_insights_fields", ensure_attendance_insights_fields),
		# Single doctypes only materialise field defaults on save, so the absent
		# marking settings added to Biometric Setting would otherwise read as a
		# zero grace period until someone opened and saved the form.
		("ensure_absent_marking_defaults", ensure_absent_marking_defaults),
		("ensure_abbreviation_field", ensure_abbreviation_field),
		# HRMS ships Monthly Attendance Sheet with prepared_report on, so a JSON
		# re-sync keeps turning it back into a background snapshot. The report
		# has to run live.
		("disable_prepared_report", disable_prepared_report),
		("ensure_biometric_stock_entry_fields", ensure_biometric_stock_entry_fields),
		("ensure_overtime_setup", ensure_overtime_setup),
		# 4. This app's own orphan sweep. It keeps anything shipped as a file
		#    under the module folder, which now includes the workspace.
		("remove_orphans", remove_orphans),
		# 5. Nav identity and de-duplication, last — after anything that could
		#    have re-stamped or re-created a record.
		("normalize_ta_workspace", normalize_ta_workspace),
		("enforce_single_desktop_icon", enforce_single_desktop_icon),
		("enforce_single_workspace_sidebar", enforce_single_workspace_sidebar),
		# 6. Fold this app's nav into HRMS' Shift & Attendance rather than
		#    standing beside it as its own tile.
		("compose_shift_attendance_sidebar", compose_shift_attendance_sidebar),
		("compose_shift_attendance_workspace", compose_shift_attendance_workspace),
		("hide_ta_desktop_icon", hide_ta_desktop_icon),
		# Last of all: Frappe's own duplicate-icon sweep has already run by now,
		# so this is the final word on what the HR app group is called.
		("rename_hr_app_icon", rename_hr_app_icon),
	)


def _run(steps, context):
	"""Run every step, isolating failures so the rest of the app still updates."""
	for label, fn in steps:
		try:
			fn()
			frappe.db.commit()  # nosemgrep - each step must land independently
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"{APP_NAME} {context}: {label}",
				message=frappe.get_traceback(),
			)
			print(f"{APP_NAME} {context}: step '{label}' failed, see Error Log")


def after_install():
	"""Run after the app is installed on a site."""
	_run(_steps(), "after_install")


def after_migrate():
	"""Run after every `bench migrate` for a site that has this app installed."""
	_run(_steps(), "after_migrate")


# --------------------------------------------------------------------------- #
# JSON resources
# --------------------------------------------------------------------------- #

# Frappe's migrate skips a JSON resource when the DB record's `modified` is newer
# than the file, or when a stored hash matches (see frappe/modules/import_file.py).
# UI edits or another app's after_migrate hook bump that timestamp, so updates we
# ship silently never reach the site. This force-reloads every resource the app
# owns, bypassing those checks.
#
# Note this is deliberately destructive to site-side edits of the records we ship
# (the workspace layout, the sidebar) — the app's files are the source of truth,
# which is the whole point of running it on every migrate.

# Frappe syncs these from the app package root as flat `<name>.json` files rather
# than from a module directory (see frappe.model.sync.sync_for).
_APP_LEVEL_DIRS = ("desktop_icon", "workspace_sidebar", "sidebar_item_group")


def _app_resource_paths():
	"""Every JSON resource file this app ships, in Frappe's own sync order.

	Built from `frappe.model.sync.get_doc_files`, so the set tracks whatever
	Frappe considers importable instead of a hand-maintained list that drifts.
	"""
	from frappe.model.sync import get_doc_files
	from frappe.modules.utils import get_module_list

	paths = []
	for module in get_module_list(APP_NAME) or []:
		module_root = frappe.get_app_path(APP_NAME, frappe.scrub(module))
		if os.path.isdir(module_root):
			# Take the return value: get_doc_files() starts with `files = files or []`,
			# so an empty list argument is rebound to a new list and everything it
			# collected for the first module is dropped on the floor.
			paths = get_doc_files(files=paths, start_path=module_root)

	app_root = frappe.get_app_path(APP_NAME)
	for folder in _APP_LEVEL_DIRS:
		folder_path = os.path.join(app_root, folder)
		if not os.path.isdir(folder_path):
			continue
		for filename in sorted(os.listdir(folder_path)):
			if filename.endswith(".json"):
				paths.append(os.path.join(app_root, folder, filename))

	return paths


def _site_owns(path: str) -> bool:
	"""Has the site taken ownership of the record this file would overwrite?

	Nav records (Workspace Sidebar, Workspace, Desktop Icon) ship as standard
	records, and a standard record is read-only in the desk — you cannot remove
	an item somebody should not see. Clearing `standard` hands the record to the
	site, and from then on the app must stop overwriting it, or the next migrate
	quietly restores every item that was deleted.

	So: `standard = 1` means the app owns it and resync applies; `standard = 0`
	means the site owns it and the file stays on disk as the default for new
	installs only.
	"""
	try:
		with open(path) as fh:
			doc = json.load(fh)
	except Exception:
		return False

	doctype, name = doc.get("doctype"), doc.get("name")
	if not doctype or not name:
		return False
	if not frappe.db.has_column(doctype, "standard"):
		return False
	if not frappe.db.exists(doctype, name):
		return False

	return not frappe.db.get_value(doctype, name, "standard")


def resync_app_resources():
	"""Force-reload every JSON resource this app ships, ignoring DB-vs-file
	timestamps and hashes. Safe to run repeatedly."""
	from frappe.modules.import_file import import_file_by_path
	from frappe.modules.patch_handler import _patch_mode

	# Same guard sync_all() uses: importing a DocType can queue patches, and we
	# are already running inside (or just after) the patch phase.
	_patch_mode(True)
	try:
		for path in _app_resource_paths():
			# A record the site has taken ownership of is never overwritten —
			# see _site_owns().
			if _site_owns(path):
				continue
			try:
				import_file_by_path(path, force=True, ignore_version=True)
				frappe.db.commit()  # nosemgrep - keep each resource independent
			except Exception:
				frappe.db.rollback()
				frappe.log_error(
					title=f"{APP_NAME} resync_app_resources: {os.path.basename(path)}",
					message=frappe.get_traceback(),
				)
	finally:
		_patch_mode(False)

	frappe.clear_cache()


# --------------------------------------------------------------------------- #
# desk nav
# --------------------------------------------------------------------------- #

# The launcher chain is Desktop Icon -> Workspace Sidebar -> Workspace, and the
# app ships all three so a fresh install gets a working tile with no manual Desk
# setup:
#
#   upande_ta/desktop_icon/upande_ta.json                 (app level, flat file)
#   upande_ta/workspace_sidebar/upande_ta.json            (app level, flat file)
#   upande_ta/upande_ta/workspace/upande_ta/upande_ta.json (module level)
#
# Shipping them is also what keeps them: frappe.model.sync.remove_orphan_entities()
# deletes a public Workspace whose module+app are set, and a standard Workspace
# Sidebar / Desktop Icon whose app is set, when no installed app ships a matching
# file. resync_app_resources() force-imports all three every migrate.
#
# The trade-off is deliberate: Desk-side edits to the workspace layout or the
# sidebar are overwritten on the next migrate. Change the shipped JSON instead.
_NAV_NAME = "Upande TA"
# Both older spellings have shipped over time, on all three doctypes. Sites that
# still carry them are folded into the current name by
# patches/v1/rename_ta_to_upande_ta, and any leftover tile is swept up below.
_STALE_NAV_NAMES = ("Upande T&A", "T&A")
_ALL_NAV_NAMES = (_NAV_NAME, *_STALE_NAV_NAMES)


def normalize_ta_workspace():
	"""Force the Workspace's name/title/label consistent.

	Frappe derives the Desk route from slug(name) and expects name == title ==
	label; the rename left several sites with title "T&A" against a record named
	"Upande TA", so the header and the launcher disagree. A parent_page pointing
	at the workspace itself nests it under a missing parent and 404s the tile.
	"""
	if not frappe.db.exists("Workspace", _NAV_NAME):
		return

	current = frappe.db.get_value("Workspace", _NAV_NAME, ["title", "label", "parent_page"], as_dict=True)
	needs_fix = current.title != _NAV_NAME or current.label != _NAV_NAME or current.parent_page == _NAV_NAME
	if not needs_fix:
		return

	# Direct write: doc.save() would run Workspace's on_update rename trigger
	# (it collapses name->title when label == name), which would fight us.
	frappe.db.set_value(
		"Workspace",
		_NAV_NAME,
		{"title": _NAV_NAME, "label": _NAV_NAME, "parent_page": ""},
		update_modified=False,
	)
	# The sidebar header mirrors the title; keep it in step.
	if frappe.db.exists("Workspace Sidebar", _NAV_NAME):
		frappe.db.set_value("Workspace Sidebar", _NAV_NAME, "title", _NAV_NAME, update_modified=False)


def _duplicate_desktop_icons():
	"""Desk tiles that open the same place as the surviving "Upande TA" tile.

	Matched on *destination* rather than on `app`: a site can create its own
	extra workspaces in the Desk UI and Frappe stamps them with this module, so
	filtering on app/module would delete tiles for workspaces someone built
	here. Only the pre-rename spellings of this app's own nav are collected.
	"""
	names = set(
		frappe.get_all(
			"Desktop Icon",
			or_filters=[
				["link_to", "in", _ALL_NAV_NAMES],
				["sidebar", "in", _ALL_NAV_NAMES],
				["label", "in", _ALL_NAV_NAMES],
			],
			pluck="name",
		)
	)
	names.discard(_NAV_NAME)
	return sorted(names)


def enforce_single_desktop_icon():
	"""Leave exactly one Desk tile for this app's nav.

	`create_desktop_icons_from_workspace()` makes one icon per public Workspace
	and de-duplicates only on (label, icon_type), and `add_workspace_to_desktop()`
	or a user saving their Desk layout inserts more — none of which Frappe's own
	sweep can clean up once `standard` is 0. The pre-rename "T&A" / "Upande T&A"
	tiles land here too, on any site where the rename patch could not fold them
	into the surviving record.
	"""
	duplicates = _duplicate_desktop_icons()
	if not duplicates:
		return

	for name in duplicates:
		try:
			# Clear standard/app first: Desktop Icon.on_trash deletes a matching
			# JSON from the app folder when developer_mode is on and both are set.
			frappe.db.set_value(
				"Desktop Icon",
				name,
				{"standard": 0, "app": None, "restrict_removal": 0},
				update_modified=False,
			)
			frappe.delete_doc(
				"Desktop Icon",
				name,
				ignore_permissions=True,
				force=True,
				ignore_missing=True,
			)
			print(f"{APP_NAME}: removed duplicate Desktop Icon '{name}'")
		except Exception:
			frappe.log_error(
				title=f"{APP_NAME} enforce_single_desktop_icon: {name}",
				message=frappe.get_traceback(),
			)

	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")


# --------------------------------------------------------------------------- #
# fold this app's nav into Shift & Attendance
# --------------------------------------------------------------------------- #

# Time and attendance is not a department of its own: the links belong beside
# HRMS' own shift tools rather than behind a separate tile in the app switcher.
# So this app's sidebar rows are copied into HRMS' "Shift & Attendance" sidebar
# and its own Desk tile is hidden.
#
# The rows are read from this app's sidebar record rather than written out here,
# so a link added to `workspace_sidebar/upande_ta.json` reaches both places.
#
# Re-applied every migrate: `shift_&_attendance.json` is shipped by hrms and
# re-imported by frappe.model.sync, which drops everything added here.
_TA_SIDEBAR = "Upande TA"
_HR_SHIFT_SIDEBAR = "Shift & Attendance"

# The sidebar as the business wants to read it. This is the whole list and its
# order: rows are matched to what is already there by destination, so HRMS' own
# icons and labels survive, but the order and the membership come from here.
#
# Flat on purpose -- no Section Breaks, nothing nested. Every entry is one click
# from the top, which is the whole point of the list; HRMS' own "Overtime",
# "Reports" and "Setup" headings are dropped along with the rest. The order
# below is the grouping: the things opened daily first, then overtime, reports,
# time & attendance, setup, and settings last.
_SIDEBAR_LAYOUT = (
	{"type": "Link", "link_type": "Workspace", "link_to": _HR_SHIFT_SIDEBAR, "label": "Home", "icon": "home"},
	# Roster, Employee Attendance Tool, Shift Request and Attendance Request name
	# no icon on purpose: HRMS already sets one on each, and a row keeps whatever
	# is already there for any field the layout leaves out.
	{"type": "Link", "link_type": "URL", "url": "/hr/roster", "label": "Roster"},
	# The two things people open this sidebar for, in the slot the Attendance
	# dashboard used to hold.
	{"type": "Link", "link_type": "Report", "link_to": "Monthly Attendance Sheet",
	 "label": "Monthly Attendance Sheet", "icon": "table"},
	{"type": "Link", "link_type": "URL", "url": "/attendance-insights",
	 "label": "Attendance Insights", "icon": "chart-line", "open_in_new_tab": 1},
	{"type": "Link", "link_type": "DocType", "link_to": "Employee Attendance Tool", "label": "Employee Attendance Tool"},
	{"type": "Link", "link_type": "DocType", "link_to": "Employee Checkin", "label": "Employee Checkin", "icon": "pointer"},
	{"type": "Link", "link_type": "DocType", "link_to": "Shift Request", "label": "Shift Request"},
	{"type": "Link", "link_type": "DocType", "link_to": "Attendance Request", "label": "Attendance Request"},
	# Asked for at the top level rather than inside a group: it is run often
	# enough that it should never be a click away behind a collapsed section.
	{"type": "Link", "link_type": "DocType", "link_to": "Bulk Week Off", "label": "Bulk Week Off", "icon": "calendar-clock"},

	# Ordered the way overtime is actually worked: the bulk entry raises the
	# slips, and the type master is the thing you touch least.
	{"type": "Link", "link_type": "DocType", "link_to": "Bulk Overtime", "label": "Bulk Overtime", "icon": "clipboard-list"},
	{"type": "Link", "link_type": "DocType", "link_to": "Overtime Slip", "label": "Overtime Slip", "icon": "file-clock"},
	{"type": "Link", "link_type": "DocType", "link_to": "Overtime Type", "label": "Overtime Type", "icon": "layers"},

	{"type": "Link", "link_type": "Report", "link_to": "Shift Attendance", "label": "Shift Attendance", "icon": "chart-column"},
	{"type": "Link", "link_type": "Report", "link_to": "Employee Hours Utilization Based On Timesheet",
	 "label": "Employee Hours Utilization", "icon": "chart-pie"},
	{"type": "Link", "link_type": "Report", "link_to": "Project Profitability", "label": "Project Profitability", "icon": "banknote"},

	{"type": "Link", "link_type": "DocType", "link_to": "Attendance", "label": "Attendance", "icon": "calendar-check"},
	{"type": "Link", "link_type": "DocType", "link_to": "Gate Pass", "label": "Gate Pass", "icon": "unlock"},
	{"type": "Link", "link_type": "DocType", "link_to": "Biometric Logs", "label": "Biometric Logs", "icon": "list"},

	{"type": "Link", "link_type": "DocType", "link_to": "Shift Type", "label": "Shift Type", "icon": "clock"},
	{"type": "Link", "link_type": "DocType", "link_to": "Shift Location", "label": "Shift Location", "icon": "map-pin"},
	{"type": "Link", "link_type": "DocType", "link_to": "Shift Schedule", "label": "Shift Schedule", "icon": "calendar-days"},
	{"type": "Link", "link_type": "DocType", "link_to": "Activity Type", "label": "Activity Type", "icon": "activity"},
	{"type": "Link", "link_type": "DocType", "link_to": "Timesheet", "label": "Timesheet", "icon": "file-text"},

	# Settings is a group, not a single link: every settings doctype lives under
	# it, and so do the biometric enrolment masters, which are configuration
	# rather than day-to-day records.
	{"type": "Link", "link_type": "DocType", "link_to": "HR Settings", "label": "HR Settings", "icon": "settings"},
	{"type": "Link", "link_type": "DocType", "link_to": "Biometric Setting", "label": "Biometric Setting", "icon": "settings"},
	{"type": "Link", "link_type": "DocType", "link_to": "Biometric User", "label": "Biometric User", "icon": "user-check"},
	{"type": "Link", "link_type": "DocType", "link_to": "Biometric Template", "label": "Biometric Template", "icon": "scan-face"},
)

# Rows HRMS ships that this layout deliberately drops. Anything else HRMS adds
# is carried over rather than lost, so a future version's new link still shows
# up — at the end, where it is obvious it needs placing.
_SIDEBAR_DROP = {
	("link", "Dashboard", "attendance"),          # the Attendance dashboard tile
	# The Upande TA workspace link. It has to be named here, not merely left out
	# of the layout: the carry-over rule below would otherwise treat a row this
	# app itself put there on an earlier migrate as something to preserve, and
	# shuffle it to the bottom instead of removing it.
	("link", "Workspace", _TA_SIDEBAR.lower()),
}

# Everything on a Workspace Sidebar Item that describes the row itself. idx is
# left out: append() numbers the rows by their position.
_SIDEBAR_ITEM_FIELDS = (
	"type",
	"label",
	"link_type",
	"link_to",
	"url",
	"icon",
	"indent",
	"keep_closed",
	"collapsible",
	"show_arrow",
	"open_in_new_tab",
	"filters",
	"route_options",
	"navigate_to_tab",
)


def _sidebar_item_key(item):
	"""What makes two sidebar rows the same entry.

	Links are compared on where they go, headings on their label.
	"""
	if item.get("type") != "Link":
		return ("section", (item.get("label") or "").strip().lower())
	destination = item.get("link_to") or item.get("url") or ""
	return ("link", item.get("link_type"), destination.strip().lower())


def _link_target_exists(spec):
	"""Skip a row whose destination this site does not have."""
	link_type = spec.get("link_type")
	if link_type in ("DocType", "Report", "Dashboard", "Page", "Workspace"):
		return bool(frappe.db.exists(link_type, spec.get("link_to")))
	return True


def compose_shift_attendance_sidebar():
	"""Lay out HRMS' Shift & Attendance sidebar the way the business reads it."""
	if not frappe.db.exists("Workspace Sidebar", _HR_SHIFT_SIDEBAR):
		return

	target = frappe.get_doc("Workspace Sidebar", _HR_SHIFT_SIDEBAR)
	current = [item.as_dict() for item in target.items]
	existing = {_sidebar_item_key(item): item for item in current}

	rows = []

	for spec in _SIDEBAR_LAYOUT:
		if not _link_target_exists(spec):
			continue

		# Start from the row that is already there, so an icon or label HRMS
		# set (or someone tuned in the Desk) is not thrown away.
		prior = existing.get(_sidebar_item_key(spec))
		row = {f: prior.get(f) for f in _SIDEBAR_ITEM_FIELDS if prior and prior.get(f)}
		row.update(spec)
		row["child"] = 0
		# A row promoted out of one of HRMS' sections still carries that
		# section's nesting flags; clear them or it renders indented and
		# collapsed under nothing.
		for flag in ("indent", "keep_closed", "collapsible", "show_arrow"):
			row.pop(flag, None)
		rows.append(row)

	# Carry over anything HRMS ships that the layout does not name, so a future
	# version's new link is never silently dropped. Headings are not carried:
	# this sidebar is flat, and a Section Break would re-nest everything after
	# it. Links only, at the top level, at the end.
	planned = {_sidebar_item_key(row) for row in rows}
	for item in current:
		key = _sidebar_item_key(item)
		if item.get("type") != "Link" or key in planned or key in _SIDEBAR_DROP:
			continue
		row = {f: item.get(f) for f in _SIDEBAR_ITEM_FIELDS if item.get(f)}
		row["child"] = 0
		for flag in ("indent", "keep_closed", "collapsible", "show_arrow"):
			row.pop(flag, None)
		rows.append(row)

	def shape(row):
		"""The row reduced to what this layout actually decides.

		Blank, zero and unset all read the same, and a Section Break's
		link_type/link_to are ignored -- the Select defaults them to "DocType"
		on save, so a stored heading never matches a computed one on those.
		"""
		is_link = (row.get("type") or "Link") == "Link"
		fields = _SIDEBAR_ITEM_FIELDS if is_link else ("type", "label", "icon", "indent", "keep_closed", "collapsible")
		shaped = [(f, row.get(f) or None) for f in fields]
		if is_link:
			shaped.append(("child", 1 if row.get("child") else None))
		return tuple(shaped)

	if [shape(row) for row in rows] == [shape(item) for item in current]:
		return

	target.items = []
	for row in rows:
		target.append("items", row)

	# WorkspaceSidebar.before_save exports a standard sidebar back to its own
	# app folder when developer_mode is on -- which would write this layout into
	# HRMS' source tree. in_import is the flag that export checks.
	in_import = frappe.flags.in_import
	frappe.flags.in_import = True
	try:
		target.flags.ignore_permissions = True
		target.save()
	finally:
		frappe.flags.in_import = in_import

	print(f"{APP_NAME}: laid out the '{_HR_SHIFT_SIDEBAR}' sidebar ({len(rows)} rows)")



# The workspace page itself, as opposed to its sidebar. HRMS puts an
# "Attendance Count" dashboard chart at the top of it; the chart needs a company
# filter to draw anything, so for everyone who has not set one it renders a
# full-width box reading "Please select company." above the cards people
# actually came for.
#
# Re-applied every migrate for the usual reason: the workspace is shipped by
# hrms as `hr/workspace/shift_&_attendance/shift_&_attendance.json` and the
# chart comes back with it.
_HR_SHIFT_WORKSPACE = "Shift & Attendance"
_DROP_WORKSPACE_CHARTS = ("Attendance Count",)

# What this app adds to the workspace cards, so the page lists the same ground
# the sidebar does. Each entry is a card label and the links it must contain;
# a card that does not exist is created, with a matching block in `content` so
# it actually renders. Only DocType/Report/Page can be a card link -- the child
# doctype's link_type is a Select limited to those -- so the Attendance Insights
# web page stays a sidebar-only entry.
_WORKSPACE_CARD_LINKS = (
	("Attendance", (("DocType", "Gate Pass"),)),
	("Shifts", (("DocType", "Bulk Week Off"),)),
	("Overtime", (("DocType", "Bulk Overtime"),)),
	("Biometrics", (
		("DocType", "Biometric Logs"),
		("DocType", "Biometric User"),
		("DocType", "Biometric Template"),
		("DocType", "Biometric Setting"),
	)),
)

# Everything on a Workspace Link row worth carrying when the list is rebuilt.
_WORKSPACE_LINK_FIELDS = (
	"type", "label", "icon", "hidden", "link_type", "link_to", "doctype_layout",
	"dependencies", "only_for", "onboard", "is_query_report", "link_count",
	"description", "report_ref_doctype",
)


def compose_shift_attendance_workspace():
	"""Lay out the Shift & Attendance workspace page: drop the dead chart, and
	list this app's doctypes on the cards alongside HRMS' own."""
	if not frappe.db.exists("Workspace", _HR_SHIFT_WORKSPACE):
		return

	workspace = frappe.get_doc("Workspace", _HR_SHIFT_WORKSPACE)

	def is_dropped_chart(block):
		return (
			block.get("type") == "chart"
			and (block.get("data") or {}).get("chart_name") in _DROP_WORKSPACE_CHARTS
		)

	blocks = frappe.parse_json(workspace.content or "[]")
	kept = []
	for index, block in enumerate(blocks):
		if is_dropped_chart(block):
			continue
		# The spacer directly under the chart only existed to separate it from
		# the heading below; on its own it is a gap at the top of the page.
		if (
			block.get("type") == "spacer"
			and index
			and is_dropped_chart(blocks[index - 1])
		):
			continue
		kept.append(block)

	charts = [c for c in workspace.charts if c.chart_name not in _DROP_WORKSPACE_CHARTS]

	links, new_cards = _shift_attendance_card_links(workspace)

	# A card only shows up if `content` carries a block for it.
	for card in new_cards:
		kept.append({"type": "card", "data": {"card_name": card, "col": 4}})

	if (
		len(kept) == len(blocks)
		and len(charts) == len(workspace.charts)
		and len(links) == len(workspace.links)
	):
		return

	workspace.content = json.dumps(kept)
	workspace.charts = charts
	workspace.links = []
	for link in links:
		workspace.append("links", link)

	# Workspace.on_update exports a public workspace back to its own app folder
	# when developer_mode is on. `in_migrate` is the flag that suppresses it --
	# already set during a real migrate, set here too so a manual bench execute
	# cannot write this change into HRMS' source tree.
	in_migrate = frappe.flags.in_migrate
	frappe.flags.in_migrate = True
	try:
		workspace.flags.ignore_permissions = True
		workspace.save()
	finally:
		frappe.flags.in_migrate = in_migrate

	print(
		f"{APP_NAME}: laid out the '{_HR_SHIFT_WORKSPACE}' workspace "
		f"({len(kept)} blocks, {len(links)} card links)"
	)


def _shift_attendance_card_links(workspace):
	"""Return (link rows, labels of cards created) with this app's links added.

	Each link is placed at the end of its own card rather than appended to the
	table, because the rows are a flat list where a "Card Break" opens a card
	and every row after it belongs to that card until the next break.
	"""
	rows = [
		{f: link.get(f) for f in _WORKSPACE_LINK_FIELDS if link.get(f)}
		for link in workspace.links
	]
	present = {
		(row.get("link_type"), row.get("link_to"))
		for row in rows
		if row.get("type") == "Link"
	}
	new_cards = []

	for card, wanted in _WORKSPACE_CARD_LINKS:
		missing = [
			{"type": "Link", "label": link_to, "link_type": link_type, "link_to": link_to}
			for link_type, link_to in wanted
			if (link_type, link_to) not in present
			and _link_target_exists({"link_type": link_type, "link_to": link_to})
		]
		if not missing:
			continue

		# where this card's rows end: the next Card Break, or the end of the list
		start = next(
			(
				i
				for i, row in enumerate(rows)
				if row.get("type") == "Card Break" and row.get("label") == card
			),
			None,
		)
		if start is None:
			rows.append({"type": "Card Break", "label": card, "hidden": 0})
			rows.extend(missing)
			new_cards.append(card)
			continue

		end = next(
			(i for i in range(start + 1, len(rows)) if rows[i].get("type") == "Card Break"),
			len(rows),
		)
		rows[end:end] = missing

	return rows, new_cards


def hide_ta_desktop_icon():
	"""Drop this app's own tile from the switcher now its links live under HR."""
	if not frappe.db.exists("Desktop Icon", _NAV_NAME):
		return
	if frappe.db.get_value("Desktop Icon", _NAV_NAME, "hidden"):
		return

	frappe.db.set_value("Desktop Icon", _NAV_NAME, "hidden", 1, update_modified=False)
	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")
	print(f"{APP_NAME}: hid the '{_NAV_NAME}' Desk tile")


# --------------------------------------------------------------------------- #
# HR app group
# --------------------------------------------------------------------------- #

# The Desk app switcher draws one group per "App" Desktop Icon, and HRMS ships
# that record as `frappe_hr.json`, labelled "Frappe HR". The group stopped being
# HRMS' alone some time ago -- upande_ta and cova_clinic_integration both ship
# their tile with `parent_icon: "Frappe HR"` -- and the business calls the
# department HR, so the vendor name is the wrong heading over it.
#
# Renaming the *record* is the only way to relabel it. Desktop Icon is autonamed
# `field:label`, so the label is the docname, and get_desktop_icons() drops any
# child whose `parent_icon` does not match a visible parent's label: writing
# `label` on its own would hide all eleven tiles instead of renaming their group.
#
# This has to re-run on every migrate. frappe.model.sync re-imports frappe_hr.json
# and the children's JSONs, recreating "Frappe HR" and re-pointing them at it,
# and `delete_duplicate_icons()` then drops our renamed tile because the hrms app
# folder holds no `hr.json`. Both run before the after_migrate hooks
# (frappe/migrate.py), so this step has the last word.
_HR_ICON_SHIPPED = "Frappe HR"
_HR_ICON = "HR"


def rename_hr_app_icon():
	"""Label the Desk app group holding HR, Upande TA and Cova Clinic "HR"."""
	from frappe.model.rename_doc import rename_doc

	if not frappe.db.exists("Desktop Icon", _HR_ICON_SHIPPED):
		_reparent_hr_children()
		return

	if frappe.db.exists("Desktop Icon", _HR_ICON):
		# A sync recreated the shipped tile beside ours instead of replacing it.
		# Fold its children over and drop it; renaming onto a taken name fails.
		_reparent_hr_children()
		# Clear standard/app first: Desktop Icon.on_trash deletes the matching
		# JSON from the *hrms* app folder when developer_mode is on and both set.
		frappe.db.set_value(
			"Desktop Icon",
			_HR_ICON_SHIPPED,
			{"standard": 0, "app": None, "restrict_removal": 0},
			update_modified=False,
		)
		frappe.delete_doc(
			"Desktop Icon",
			_HR_ICON_SHIPPED,
			ignore_permissions=True,
			force=True,
			ignore_missing=True,
		)
	else:
		# Desktop Icon.after_rename rewrites the app folder -- it deletes
		# `hrms/desktop_icon/frappe_hr.json` and exports `hr.json` in its place,
		# unconditionally on delete. Blanking `app` for the duration keeps this
		# app out of HRMS' source tree; it is restored immediately after.
		frappe.db.set_value(
			"Desktop Icon", _HR_ICON_SHIPPED, "app", None, update_modified=False
		)
		try:
			rename_doc(
				"Desktop Icon",
				_HR_ICON_SHIPPED,
				_HR_ICON,
				force=True,
				ignore_permissions=True,
				show_alert=False,
			)
		except Exception:
			frappe.db.set_value(
				"Desktop Icon", _HR_ICON_SHIPPED, "app", "hrms", update_modified=False
			)
			raise
		# `app` is what pairs the tile with hrms for Frappe's own duplicate sweep
		# and for check_app_permission(), so it has to go back.
		frappe.db.set_value("Desktop Icon", _HR_ICON, "app", "hrms", update_modified=False)

	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")
	print(f"{APP_NAME}: Desk app group '{_HR_ICON_SHIPPED}' relabelled '{_HR_ICON}'")


def _reparent_hr_children():
	"""Move any tile still pointing at the shipped label onto the renamed group."""
	for child in frappe.get_all(
		"Desktop Icon", filters={"parent_icon": _HR_ICON_SHIPPED}, pluck="name"
	):
		frappe.db.set_value(
			"Desktop Icon", child, "parent_icon", _HR_ICON, update_modified=False
		)


# --------------------------------------------------------------------------- #
# workspace sidebar
# --------------------------------------------------------------------------- #

# `create_workspace_sidebar_for_workspaces()` (bench install-app, some upgrades)
# makes one Workspace Sidebar per public Workspace, titled after it. Delete the
# workspace later and the sidebar is left behind — and because those auto-created
# records carry no `app` and `standard=0`, frappe.model.sync.remove_orphan_entities()
# can never see them. The result is a second, dead sidebar in the Desk switcher
# alongside the one this app ships.
#
# Only genuine orphans are removed: a sidebar still backing a live Workspace
# belongs to the site, even when Frappe has stamped it with this app's module.
_CANONICAL_SIDEBAR = "Upande TA"


def _orphan_workspace_sidebars():
	"""Sidebars stamped with this module whose Workspace no longer exists."""
	orphans = []
	rows = frappe.get_all(
		"Workspace Sidebar",
		filters={"module": MODULE_NAME, "for_user": ["in", ["", None]]},
		pluck="name",
	)
	for name in rows:
		if name == _CANONICAL_SIDEBAR:
			continue
		# Auto-created sidebars are titled after their workspace, so a matching
		# Workspace means it is still live.
		if frappe.db.exists("Workspace", name):
			continue
		# ...and honour a renamed one whose Home item still resolves.
		targets = frappe.get_all(
			"Workspace Sidebar Item",
			filters={"parent": name, "link_type": "Workspace"},
			pluck="link_to",
		)
		if any(t and frappe.db.exists("Workspace", t) for t in targets):
			continue
		orphans.append(name)
	return orphans


def enforce_single_workspace_sidebar():
	"""Drop Workspace Sidebars left behind by deleted workspaces."""
	orphans = _orphan_workspace_sidebars()
	if not orphans:
		return

	for name in orphans:
		try:
			# Clear app first: Workspace Sidebar.on_trash deletes the app's shipped
			# JSON when developer_mode is on and `app` is set.
			frappe.db.set_value(
				"Workspace Sidebar", name, {"standard": 0, "app": None}, update_modified=False
			)
			frappe.delete_doc(
				"Workspace Sidebar",
				name,
				ignore_permissions=True,
				force=True,
				ignore_missing=True,
			)
			print(f"{APP_NAME}: removed orphan Workspace Sidebar '{name}'")
		except Exception:
			frappe.log_error(
				title=f"{APP_NAME} enforce_single_workspace_sidebar: {name}",
				message=frappe.get_traceback(),
			)

	frappe.cache.delete_key("bootinfo")
