import frappe


# Both `csf_ke` and `payroll_africa` ship a Custom Field on Salary Component with
# fieldname `p10a_tax_deduction_card_type`, but under different docnames:
#
#   csf_ke         -> "Salary Component-custom_p10a_tax_deduction_card_type"
#   payroll_africa -> "Salary Component-p10a_tax_deduction_card_type"
#
# The first app's fixture inserts cleanly. The second app's fixture is a *different*
# docname, so Frappe attempts an INSERT — and Custom Field.validate() rejects it with
# "A field with the name p10a_tax_deduction_card_type already exists in Salary
# Component", because the duplicate check keys on (dt, fieldname), not on docname.
# That aborts `sync_fixtures()` and the whole migrate.
#
# Cloning a second row does NOT help: the colliding insert is on fieldname, so a
# second row only guarantees the collision. The only thing that unblocks migrate is
# collapsing to exactly ONE row before fixtures sync, so whichever app owns the
# surviving docname does an UPDATE and the other app finds nothing to insert against
# the same fieldname... which still collides. So we go further: we keep one row and
# delete the duplicate, then let the surviving fixture win. The remaining app's
# fixture insert is pre-empted by deleting its target only if stale.
#
# Strategy that is actually order-independent and idempotent:
#   - Find every Custom Field row with dt='Salary Component' and the p10a fieldname.
#   - Keep the OLDEST (lowest creation) row — that's the one already wired into Salary
#     Component metadata / any data referencing it.
#   - Delete the rest via raw SQL (bypassing the on_trash/validate hooks so deletion
#     can't itself trip the duplicate guard).
#
# After this, only one row remains. Both apps' fixtures resolve to the same fieldname:
# the app whose docname matches updates in place; the app whose docname does NOT match
# would still try to insert and collide — so this patch ALSO runs as a before_migrate
# hook every migrate, guaranteeing a single row exists the instant before sync, and we
# rename the survivor to the docname that the duplicate-prone (last-syncing) app uses.

DT = "Salary Component"
FIELDNAME = "p10a_tax_deduction_card_type"

# payroll_africa syncs last in the kentrout migrate order, so its docname is the one
# that must survive — its fixture then UPDATEs the row instead of INSERTing a new one.
# csf_ke syncs earlier; if its docname is the survivor, payroll_africa collides. So we
# canonicalise on payroll_africa's (non-prefixed) docname.
CANONICAL_NAME = "Salary Component-p10a_tax_deduction_card_type"


def execute():
	rows = frappe.db.sql(
		"""
			SELECT name, creation
			FROM `tabCustom Field`
			WHERE dt = %s AND fieldname = %s
			ORDER BY creation ASC, name ASC
		""",
		(DT, FIELDNAME),
		as_dict=True,
	)

	if not rows:
		print(f"[fix_p10a_duplicate_custom_field] no '{FIELDNAME}' on '{DT}'; nothing to do")
		return

	names = [r["name"] for r in rows]

	# Pick the survivor: prefer the canonical (payroll_africa) docname if present,
	# else the oldest row.
	survivor = CANONICAL_NAME if CANONICAL_NAME in names else names[0]
	duplicates = [n for n in names if n != survivor]

	# Delete duplicates by raw SQL so on_trash/validate hooks can't re-trip the guard.
	for dup in duplicates:
		frappe.db.sql("DELETE FROM `tabCustom Field` WHERE name = %s", (dup,))
		print(f"[fix_p10a_duplicate_custom_field] deleted duplicate Custom Field '{dup}'")

	# Canonicalise the survivor's docname so the last-syncing app (payroll_africa)
	# matches it and UPDATEs rather than INSERTs.
	if survivor != CANONICAL_NAME:
		frappe.db.sql(
			"UPDATE `tabCustom Field` SET name = %s WHERE name = %s",
			(CANONICAL_NAME, survivor),
		)
		print(
			f"[fix_p10a_duplicate_custom_field] renamed survivor '{survivor}' -> '{CANONICAL_NAME}'"
		)

	frappe.clear_cache(doctype=DT)
	frappe.db.commit()
	print(f"[fix_p10a_duplicate_custom_field] reconciled to single row '{CANONICAL_NAME}'")
