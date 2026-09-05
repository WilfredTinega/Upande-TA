# Copyright (c) 2026, Upande LTD and contributors
"""Override of the HRMS Attendance doctype: an auto-marked Absent gives way.

Absent is written by a machine here — by the shift-end pass in
``upande_ta.upande_ta.api.absent_marking`` and by HRMS's own auto attendance —
so it is a provisional statement about a day, not a verdict. ERPNext treats it
as a verdict: any later record for the same employee and date is rejected with
DuplicateAttendanceError while the Absent sits at ``docstatus < 2``.

That has two costs on a biometric site:

  * **A late check-in is lost.** ``mark_attendance_and_link_log`` catches the
    error and calls ``handle_attendance_exception``, which sets
    ``skip_auto_attendance = 1`` on the logs. The scan is then excluded from
    every future run, permanently, and the day stays Absent even though the
    person's own punches are sitting in the table.

  * **Marking someone Present by hand fails.** The Attendance form, the bulk
    Employee Attendance Tool and ``mark_attendance()`` all throw, so correcting
    a wrong Absent means finding and cancelling it first.

So: when a record that is *not* Absent is created for a date whose only
conflict is an Absent, that Absent is cancelled (drafts are deleted) and the
new record proceeds. Cancelling rather than deleting is what makes this safe to
audit — ``docstatus < 2`` is exactly how ERPNext scopes its duplicate check, so
a cancelled Absent frees the date while staying on the record, and its
``on_cancel`` unlinks any check-ins it had claimed so they can be reprocessed.

The conflict is resolved through the parent's own two lookups
(``get_duplicate_attendance_record`` / ``get_overlapping_shift_attendance``), so
this cancels precisely the row that would have blocked the insert and nothing
else. Any other status — Present, On Leave, Holiday — still collides exactly as
it does in stock ERPNext.

Switched by ``Biometric Setting.auto_supersede_absent`` (on by default).
"""

import frappe
from frappe import _
from frappe.utils import cint
from hrms.hr.doctype.attendance.attendance import Attendance

# Statuses that outrank a provisional Absent. "Absent" itself is missing on
# purpose: two Absents for one day is a duplicate to reject, not to resolve.
SUPERSEDES_ABSENT = ("Present", "Half Day", "Work From Home", "On Leave")

# A date should never hold more than one conflicting row; the loop exists only
# so a legacy site with several does not need a second pass.
MAX_SUPERSEDE_PASSES = 5


class UpandeAttendance(Attendance):
	def validate(self):
		self.supersede_conflicting_absent()
		super().validate()

	def after_insert(self):
		superseded = self.flags.get("superseded_absent") or []
		if superseded:
			self.add_comment(
				"Comment",
				text=_("Superseded Absent {0} for this date.").format(", ".join(superseded)),
			)

	def supersede_conflicting_absent(self):
		"""Clear an Absent that would otherwise block this record."""
		if (self.status or "") not in SUPERSEDES_ABSENT:
			return
		if not (self.employee and self.attendance_date):
			return
		# Escape hatch for callers that want stock behaviour (and for the
		# cancel/delete we perform below, which must not recurse).
		if self.flags.get("ignore_absent_supersede"):
			return
		if not _supersede_enabled():
			return

		superseded = self.flags.get("superseded_absent") or []
		for _pass in range(MAX_SUPERSEDE_PASSES):
			conflicts = self._conflicting_absent_records()
			if not conflicts:
				break
			for name in conflicts:
				if _release_absent(name):
					superseded.append(name)

		self.flags.superseded_absent = superseded

	def _conflicting_absent_records(self):
		"""Names of the Absent records that would fail this record's validation.

		Uses the parent's own queries so the set is exactly what ERPNext would
		reject — including the overlapping-shift case, where the blocking row
		carries a different shift whose timings overlap this one.
		"""
		names = []

		duplicate = self.get_duplicate_attendance_record()
		if duplicate:
			names.append(duplicate)

		overlapping = self.get_overlapping_shift_attendance() or {}
		if overlapping.get("name") and overlapping["name"] not in names:
			names.append(overlapping["name"])

		absent = []
		for name in names:
			status = frappe.db.get_value("Attendance", name, "status")
			if (status or "") == "Absent":
				absent.append(name)

		return absent


_SWITCH_FLAG = "upande_ta_supersede_absent"


def _supersede_enabled():
	"""Read the switch defensively — this runs inside every Attendance validate.

	Read straight off `tabSingles` rather than through `get_single_value`, which
	casts a Check field with `cint()` and so cannot tell "unticked" (0) from
	"this field has no row yet" (None -> 0). A Single doctype only writes field
	defaults when the document is saved, so on a site that has not re-saved
	Biometric Setting since the upgrade that difference is the difference
	between the shipped default (on) and silently keeping the old
	lost-check-in behaviour. `ensure_absent_marking_defaults()` seeds the row on
	migrate; this covers the window before it runs, and any site where the
	doctype is missing entirely.

	Cached per request/job: the value is read on every Attendance write, and a
	settings change lands on the next one either way.
	"""
	if _SWITCH_FLAG in frappe.flags:
		return frappe.flags[_SWITCH_FLAG]

	enabled = True
	try:
		rows = frappe.db.sql(
			"""SELECT value FROM tabSingles WHERE doctype = %s AND field = %s""",
			("Biometric Setting", "auto_supersede_absent"),
			pluck=True,
		)
		if rows:
			enabled = bool(cint(rows[0]))
	except Exception:
		enabled = True

	frappe.flags[_SWITCH_FLAG] = enabled
	return enabled


def _release_absent(name):
	"""Cancel (or delete, if still draft) one Absent. Never raises.

	A failure here must not turn into a failure of the record we are trying to
	write: if the Absent cannot be released, the parent's duplicate check throws
	next and the caller sees the same error it always did.
	"""
	try:
		doc = frappe.get_doc("Attendance", name)
		doc.flags.ignore_permissions = True
		doc.flags.ignore_absent_supersede = True

		if doc.docstatus == 1:
			doc.cancel()
		elif doc.docstatus == 0:
			doc.delete(ignore_permissions=True)
		else:
			return False

		return True
	except Exception:
		frappe.log_error(
			title=f"Upande TA: could not supersede Absent {name}",
			message=frappe.get_traceback(),
		)
		return False
