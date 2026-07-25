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


def cancel_absent_attendance_on_weekoff():
	"""Cancel Administrator-owned Absent attendance that falls on a day which is
	the employee's *date-effective* weekly off / holiday.

	Companion to :func:`cancel_absent_attendance_with_checkin`. Reassigning a
	week off creates a date-effective Holiday List Assignment, but the HRMS
	auto-attendance job resolves holidays from the employee's single *current*
	``holiday_list`` field (not the assignment in force on the date). So it keeps
	marking Absent on days that are the employee's assigned week off — both
	future and historical. This drains those false absents in batches.

	Guarantees the end state "no Absent on an assigned week off" regardless of
	whether the Shift Type date-effective patch is active on the worker. Only
	system-owned (Administrator) absents are touched; a real HR user's manual
	absent is a deliberate decision and is left untouched. Idempotent; per-record
	failures are logged and skipped so one bad row never aborts the batch.
	"""
	from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday
	from upande_ta.upande_ta.holiday_list import get_holiday_list_for_employee

	cutoff_dt = frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-LOOKBACK_DAYS)
	cutoff = str(frappe.utils.getdate(cutoff_dt))

	# Most Administrator absents are on real working days, so scan a wider slice
	# than the cancel cap to find up to BATCH_SIZE that actually sit on an off.
	candidates = frappe.db.sql(
		"""
		SELECT a.name, a.employee, a.attendance_date
		FROM `tabAttendance` a
		WHERE a.status = 'Absent'
			AND a.docstatus = 1
			AND a.owner = %(owner)s
			AND a.attendance_date >= %(cutoff)s
		ORDER BY a.attendance_date
		LIMIT %(lim)s
		""",
		{"owner": ABSENT_OWNER, "cutoff": cutoff, "lim": BATCH_SIZE * 5},
		as_dict=True,
	)

	cancelled = 0
	failed = 0
	for row in candidates:
		try:
			hl = get_holiday_list_for_employee(
				row.employee, raise_exception=False, as_on=row.attendance_date
			)
			if not hl or not is_holiday(hl, frappe.utils.getdate(row.attendance_date)):
				continue
			att_doc = frappe.get_doc("Attendance", row.name)
			att_doc.flags.ignore_permissions = True
			att_doc.cancel()
			cancelled += 1
			if cancelled >= BATCH_SIZE:
				break
		except Exception:
			failed += 1
			frappe.log_error(
				title="Cancel Absent on Week Off Failed",
				message=f"{row.name}: {frappe.get_traceback()}",
			)

	frappe.db.commit()

	summary = {"cancelled": cancelled, "failed": failed}
	frappe.logger("upande_ta").info(f"Cancel Absent on Week Off run: {summary}")
	return summary
