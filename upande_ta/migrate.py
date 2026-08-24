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
	from upande_ta.upande_ta.cleanup import remove_orphans
	from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import (
		resync_scheduled_jobs,
	)
	from upande_ta.upande_ta.doctype.bulk_overtime.bulk_overtime import ensure_overtime_setup
	from upande_ta.upande_ta.overrides.leave_type import ensure_abbreviation_field
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
		("ensure_abbreviation_field", ensure_abbreviation_field),
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
			get_doc_files(files=paths, start_path=module_root)

	app_root = frappe.get_app_path(APP_NAME)
	for folder in _APP_LEVEL_DIRS:
		folder_path = os.path.join(app_root, folder)
		if not os.path.isdir(folder_path):
			continue
		for filename in sorted(os.listdir(folder_path)):
			if filename.endswith(".json"):
				paths.append(os.path.join(app_root, folder, filename))

	return paths


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
