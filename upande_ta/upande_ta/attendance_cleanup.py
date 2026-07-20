# Copyright (c) 2026, Upande LTD and contributors

"""Cancel bogus "Absent" attendance that actually has a check-in log.

Ported from the "Cancel Absent Attendance with Checkin log" Server Script.

An employee marked Absent for a day on which they *did* punch a biometric
check-in is a false absent -- the attendance was auto-created before the log
synced. This drains such records in batches so a single scheduler tick never
tries to cancel the whole backlog at once.

Scope: only absents *created by the Administrator* are cancelled. Those are the
ones the auto-attendance / bulk jobs produce under the system user; an absent
marked by a real HR user is a deliberate human decision and is left untouched.
"""

import frappe

# How many attendance docs to cancel per scheduler tick.
BATCH_SIZE = 100
# Only look at recent attendance; older backlog is out of scope.
LOOKBACK_DAYS = 30
# Only cancel absents owned by the system user -- never a real user's.
ABSENT_OWNER = "Administrator"

_BASE_FROM_WHERE = """
	FROM `tabAttendance` a
	INNER JOIN `tabEmployee Checkin` c
		ON c.employee = a.employee
		AND c.time >= a.attendance_date
		AND c.time < DATE_ADD(a.attendance_date, INTERVAL 1 DAY)
	WHERE a.status = 'Absent'
		AND a.docstatus = 1
		AND a.owner = %(owner)s
		AND a.attendance_date >= %(cutoff)s
"""


def cancel_absent_attendance_with_checkin():
	"""Cancel one batch of Administrator-owned Absent attendance that has a check-in."""
	cutoff_dt = frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-LOOKBACK_DAYS)
	cutoff = str(frappe.utils.getdate(cutoff_dt))
	params = {"cutoff": cutoff, "owner": ABSENT_OWNER}

	remaining_before = frappe.db.sql(
		"SELECT COUNT(DISTINCT a.name) AS cnt " + _BASE_FROM_WHERE,
		params,
		as_dict=True,
	)[0].cnt

	if not remaining_before:
		return {"cancelled": 0, "failed": 0, "remaining": 0}

	candidates = frappe.db.sql(
		"SELECT DISTINCT a.name AS att_name "
		+ _BASE_FROM_WHERE
		+ " ORDER BY a.attendance_date LIMIT %(lim)s",
		{**params, "lim": BATCH_SIZE},
		as_dict=True,
	)

	cancelled = 0
	failed = 0
	for row in candidates:
		try:
			att_doc = frappe.get_doc("Attendance", row.att_name)
			att_doc.flags.ignore_permissions = True
			att_doc.cancel()
			cancelled += 1
		except Exception:
			failed += 1
			frappe.log_error(
				title="Cancel Absent Attendance Failed",
				message=f"{row.att_name}: {frappe.get_traceback()}",
			)

	frappe.db.commit()

	summary = {
		"cancelled": cancelled,
		"failed": failed,
		"remaining": remaining_before - cancelled,
	}
	frappe.logger("upande_ta").info(f"Cancel Absent Attendance run: {summary}")
	return summary
