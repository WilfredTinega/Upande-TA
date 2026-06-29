import json
import os

import frappe


# Both `csf_ke` and `payroll_africa` ship the SAME Salary Component Custom Field
# (dt='Salary Component', fieldname='p10a_tax_deduction_card_type') as a fixture, only
# under different docnames:
#
#   csf_ke         -> "Salary Component-custom_p10a_tax_deduction_card_type"
#   payroll_africa -> "Salary Component-p10a_tax_deduction_card_type"
#
# Within a single `sync_fixtures()` run, the first app imports cleanly; the field then
# appears in the cached-bypassing `frappe.get_meta(dt, cached=False)` that
# CustomField.validate() builds. The second app's fixture is a *different docname*, so
# it is is_new()=True, and its fieldname is already in meta -> Frappe throws
# "A field with the name p10a_tax_deduction_card_type already exists in Salary
# Component" (custom_field.py:175), aborting the whole migrate.
#
# The collision is between TWO fresh fixture inserts in the same run, so deleting
# pre-existing DB rows does nothing. The only durable fix on a site whose marketplace
# apps we can't fork is to stop ONE app from importing the field. We strip the p10a
# entry out of payroll_africa's fixture file on disk, leaving csf_ke as the sole owner
# (this is a Kenyan PAYE / CSF KE field). Run as `before_migrate`, this executes before
# sync_fixtures() every migrate and re-applies after any app-code re-pull. Idempotent.

DT = "Salary Component"
FIELDNAME = "p10a_tax_deduction_card_type"

# App to strip the duplicate from (csf_ke keeps ownership).
STRIP_FROM_APP = "payroll_africa"
FIXTURE_REL_PATH = os.path.join("payroll_africa", "fixtures", "custom_field.json")


# csf_ke's fixture docname — the owner we keep.
CSFKE_NAME = "Salary Component-custom_p10a_tax_deduction_card_type"
# payroll_africa's fixture docname — the orphan to drop from the DB.
PAYROLL_NAME = "Salary Component-p10a_tax_deduction_card_type"


def _drop_orphan_db_row():
	"""Remove payroll_africa's docname from the DB if csf_ke's row exists too.

	A prior run may have left the payroll_africa-named row in the DB. Once we stop
	payroll_africa from importing, csf_ke's fixture (a different docname) is is_new()
	and would still collide on fieldname against that leftover row. Delete it via raw
	SQL so on_trash/validate can't re-trip the guard. Keep csf_ke's row if present.
	"""
	rows = frappe.db.sql(
		"""
			SELECT name FROM `tabCustom Field`
			WHERE dt = %s AND fieldname = %s
		""",
		(DT, FIELDNAME),
		as_dict=True,
	)
	names = {r["name"] for r in rows}
	if not names:
		return

	# Determine which single row to keep: prefer csf_ke's docname (the surviving owner).
	survivor = CSFKE_NAME if CSFKE_NAME in names else sorted(names)[0]
	for name in names:
		if name == survivor:
			continue
		frappe.db.sql("DELETE FROM `tabCustom Field` WHERE name = %s", (name,))
		print(f"[fix_p10a_duplicate_custom_field] dropped orphan DB row '{name}'")

	# If only payroll_africa's row exists (no csf_ke row yet), rename it to csf_ke's
	# docname so csf_ke's fixture does an UPDATE instead of a colliding INSERT.
	if survivor == PAYROLL_NAME and CSFKE_NAME not in names:
		frappe.db.sql(
			"UPDATE `tabCustom Field` SET name = %s WHERE name = %s",
			(CSFKE_NAME, PAYROLL_NAME),
		)
		print(
			f"[fix_p10a_duplicate_custom_field] renamed '{PAYROLL_NAME}' -> '{CSFKE_NAME}'"
		)

	frappe.clear_cache(doctype=DT)
	frappe.db.commit()


def execute():
	_drop_orphan_db_row()

	try:
		app_path = frappe.get_app_path(STRIP_FROM_APP)
	except Exception:
		# payroll_africa not installed on this site — nothing to strip.
		print(f"[fix_p10a_duplicate_custom_field] {STRIP_FROM_APP} not installed; skipping")
		return

	# frappe.get_app_path returns .../payroll_africa/payroll_africa; the fixtures dir
	# lives under the inner module package.
	fixture_path = os.path.join(app_path, "fixtures", "custom_field.json")
	if not os.path.exists(fixture_path):
		print(f"[fix_p10a_duplicate_custom_field] no fixture at {fixture_path}; skipping")
		return

	with open(fixture_path, encoding="utf-8") as f:
		data = json.load(f)

	if not isinstance(data, list):
		print(f"[fix_p10a_duplicate_custom_field] unexpected fixture shape in {fixture_path}")
		return

	kept = [
		row
		for row in data
		if not (row.get("dt") == DT and row.get("fieldname") == FIELDNAME)
	]

	removed = len(data) - len(kept)
	if removed == 0:
		print(
			f"[fix_p10a_duplicate_custom_field] '{FIELDNAME}' already absent from "
			f"{STRIP_FROM_APP} fixture; nothing to do"
		)
		return

	# Match Frappe's own fixture serialization (indent=1, insertion-order keys) so the
	# rewrite only drops the one entry rather than reordering every key.
	with open(fixture_path, "w", encoding="utf-8") as f:
		json.dump(kept, f, indent=1)
		f.write("\n")

	print(
		f"[fix_p10a_duplicate_custom_field] removed {removed} '{FIELDNAME}' entry(ies) "
		f"from {STRIP_FROM_APP} fixture; csf_ke is now sole owner"
	)
