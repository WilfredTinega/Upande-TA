# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt
"""Shift-end absent marking, on a period the UI controls.

HRMS marks Absent from ``Shift Type.process_auto_attendance``, and it does so a
full day late by construction: ``get_start_and_end_dates`` takes the shift that
contains ``last_sync_of_checkin``, subtracts exactly one day and stops there
("absentees are auto-marked 1 day after the shift to wait for any manual
attendance records"). It also cannot run at all until the biometric integration
has pushed ``last_sync_of_checkin`` past the shift, so a stalled poll freezes
absent marking indefinitely.

This module marks the same days off a wall clock instead of that watermark: once
a shift's window has closed and the grace period configured on Biometric
Setting has elapsed, every assigned employee with no check-in log inside that
window and no attendance for the date is marked Absent. Nothing here reads
``last_sync_of_checkin``, and no 24-hour wait is involved.

It is additive, not a replacement. HRMS's own run still owns the interesting
case — logs exist but fall short of the working-hours threshold — and simply
finds the date already marked when it eventually gets there. Conversely, a log
that arrives after we marked the day Absent is no longer lost: the Attendance
override in ``upande_ta.upande_ta.overrides.attendance`` cancels the superseded
Absent so auto attendance can write the real status.

Guards, because marking absence wrongly is expensive:
  * **week off is never marked.** Rest days come from a Holiday List assigned
    to the employee — the "Sunday Week Off 2026" style lists Bulk Week Off
    hands out — and an employee with no such list of their own is skipped, not
    marked, because a company default holiday list says nothing about week
    offs. Never ``Shift Type.get_holiday_list``, which prefers the shift's list
    and ignores the employee's week off entirely;
  * **every employee is marked under their own shift.** Candidates are
    confirmed one by one against ``get_employee_shift``, so an Absent is only
    ever written for the shift that moment actually belongs to — an assignment
    beats ``Employee.default_shift``, which is how the same day used to collect
    an Absent from one shift's run beside a Present from another's;
  * an approved Leave Application on the date is skipped even when its
    Attendance has not been created yet;
  * with "Skip When No Device Activity" on, a shift window in which *nobody*
    scanned is treated as a device or network outage and left alone.
"""

from datetime import datetime, timedelta

import frappe
from frappe.utils import add_days, cint, get_datetime, get_time, getdate, now_datetime, nowdate

SETTINGS_DOCTYPE = "Biometric Setting"

# Every number this module runs on comes from Biometric Setting, and the shipped
# values for them live in that doctype's own field defaults — nothing is tuned
# here. `ensure_absent_marking_defaults()` reads those defaults straight off the
# metadata, so changing one in the doctype changes it everywhere.
ABSENT_MARKING_FIELDS = (
	"absent_grace_minutes",
	"absent_lookback_days",
	"absent_max_lookback_days",
	"absent_batch_size",
	"absent_skip_when_no_device_activity",
	"absent_shift_scope",
	"absent_event_frequency",
	"absent_cron_format",
	"auto_supersede_absent",
)


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #


def run_absent_marking():
	"""Scheduler entry point, wired through Biometric Setting's own Scheduled
	Job Type row (see SCHEDULER_TASKS in biometric_setting.py)."""
	settings = _settings()
	if not settings.get("enable_absent"):
		return {"skipped": True, "reason": "enable_absent is off"}

	# Before marking anything, give back the scans a previous failed run parked.
	# Otherwise a day can sit Absent forever with the employee's own punches in
	# the table, flagged out of every future auto-attendance run.
	reclaimed = _reclaim_skipped_checkins(cint(settings.get("absent_lookback_days")))

	summary = process_absentees(settings=settings)
	summary["reclaimed_checkins"] = reclaimed.get("reclaimed", 0)
	return summary


@frappe.whitelist()
def mark_absentees_now(
	from_date: str | None = None,
	to_date: str | None = None,
	dry_run: str | int | bool = 0,
):
	"""Run the same pass on demand from the Attendance tab's button.

	``dry_run`` reports what would be marked and writes nothing, which is the
	only safe way to try out a new grace period on a live site.
	"""
	frappe.only_for(("System Manager", "HR Manager"))

	return process_absentees(
		from_date=from_date,
		to_date=to_date,
		dry_run=cint(dry_run),
	)


def process_absentees(from_date=None, to_date=None, dry_run=0, settings=None, now=None):
	"""Mark Absent for every due shift window in the range with no check-ins.

	Returns a summary of what was marked and, for every window that was passed
	over, why — a run that marks nothing should never be indistinguishable from
	a run that did not happen.

	`now` overrides the clock the "is this window due yet" test uses; it exists
	so that behaviour can be tested without waiting for a real shift to close.
	"""
	settings = settings or _settings()
	dry_run = cint(dry_run)

	# A blank grace period means "as soon as the shift ends", which is a real
	# answer, so it is honoured rather than replaced by a fallback.
	grace_minutes = cint(settings.get("absent_grace_minutes"))
	max_lookback = cint(settings.get("absent_max_lookback_days"))
	# At least the one day asked for; a lookback of 0 would process nothing.
	lookback = _capped_days(cint(settings.get("absent_lookback_days")), max_lookback)
	batch_size = cint(settings.get("absent_batch_size"))
	skip_idle_windows = cint(settings.get("absent_skip_when_no_device_activity"))
	all_shifts = (settings.get("absent_shift_scope") or "") == "All Shift Types"

	to_day = getdate(to_date or nowdate())
	from_day = getdate(from_date) if from_date else add_days(to_day, -(lookback - 1))
	if from_day > to_day:
		from_day, to_day = to_day, from_day
	# A range typed into the buttons is capped the same way the setting is.
	span = _capped_days((to_day - from_day).days + 1, max_lookback)
	from_day = add_days(to_day, -(span - 1))

	now = get_datetime(now) if now else now_datetime()
	summary = {
		"from_date": str(from_day),
		"to_date": str(to_day),
		"grace_minutes": grace_minutes,
		"batch_size": batch_size,
		"dry_run": 1 if dry_run else 0,
		"marked": 0,
		"windows": [],
	}

	shifts = _shift_types(all_shifts)
	if not shifts:
		summary["skipped"] = True
		summary["reason"] = "No Shift Type in scope"
		return summary

	day = from_day
	while day <= to_day:
		for shift in shifts:
			window = _process_window(
				shift,
				day,
				now,
				grace_minutes,
				skip_idle_windows,
				dry_run,
				batch_size,
			)
			if window:
				summary["windows"].append(window)
				summary["marked"] += len(window.get("marked_employees") or [])
		day = add_days(day, 1)

	return summary


def _capped_days(days, cap):
	"""`days` clamped to at least 1 and to `cap`, where a cap of 0 means none."""
	days = max(1, cint(days))
	if cap and cap > 0:
		return min(days, cap)
	return days


# --------------------------------------------------------------------------- #
# one (shift, date) window
# --------------------------------------------------------------------------- #


def _process_window(shift, day, now, grace_minutes, skip_idle_windows, dry_run, batch_size):
	"""Mark the absentees of a single shift on a single date, or explain why not."""
	timings = _shift_window(shift, day)
	if not timings:
		return {"shift": shift, "date": str(day), "skipped": "shift timings unavailable"}

	actual_start, actual_end, shift_end = timings

	# Measured from when the shift actually ENDS, not from the end of its
	# check-out allowance. Kaitet's shifts carry ±8 hours of check-in/check-out
	# slack (Cleaners: 06:30-14:00, window 22:30 to 22:00), so anchoring on
	# `actual_end` would put a 60 minute grace at 23:00 — most of a day after
	# the shift closed, which is the wait this whole module exists to remove.
	#
	# Scans are still looked for across the full check-in/check-out window
	# below, so nobody who did scan can be marked because of this. Someone
	# whose only scan lands after the grace has passed is marked and then put
	# right automatically: the Attendance override cancels the Absent when auto
	# attendance writes the real status from the log.
	due_at = shift_end + timedelta(minutes=grace_minutes)
	if now < due_at:
		# The shift has not closed (or its grace period has not elapsed) — this
		# is the normal case for today's shift, not a problem.
		return None

	window = {
		"shift": shift,
		"date": str(day),
		"shift_end": str(shift_end),
		"window_start": str(actual_start),
		"window_end": str(actual_end),
		"due_at": str(due_at),
	}

	candidates = _candidate_employees(shift, day)
	if not candidates:
		window["skipped"] = "no employees assigned"
		return window

	scanned = _employees_with_checkins(list(candidates), actual_start, actual_end)

	if skip_idle_windows and not scanned:
		# Not one person on this shift scanned in the whole window. On a working
		# day that is a device, network or poll failure far more often than it
		# is total absenteeism, and marking the entire shift Absent is the most
		# expensive way to be wrong. Checked per shift rather than site-wide so
		# that one farm's devices going down is caught while other shifts keep
		# reporting normally.
		#
		# The cost is a shift small enough that its only employee being absent
		# looks identical to an outage. That is why the skip is reported rather
		# than silent: it shows up in the preview dialog and in the job's return
		# value as "no device activity in window", to be marked by hand.
		window["assigned"] = len(candidates)
		window["skipped"] = "no device activity in window"
		return window

	already_marked = _employees_with_attendance(list(candidates), day)
	on_leave = _employees_on_leave(list(candidates), day)

	accounted = scanned | already_marked | on_leave
	pending = []
	rest_days = 0
	unknown_rest_days = 0
	other_shift = 0
	for employee in sorted(candidates - accounted):
		if not _shift_belongs_to(employee, shift, actual_start):
			other_shift += 1
			continue
		state = _rest_day_state(employee, day)
		if state == "work":
			pending.append(employee)
		elif state == "rest":
			rest_days += 1
		else:
			unknown_rest_days += 1

	window["assigned"] = len(candidates)
	window["scanned"] = len(scanned & candidates)
	window["already_marked"] = len(already_marked & candidates)
	window["on_leave"] = len(on_leave & candidates)
	window["rest_day"] = rest_days
	window["no_week_off_list"] = unknown_rest_days
	window["other_shift"] = other_shift
	window["marked_employees"] = []
	window["errors"] = []

	if dry_run:
		window["would_mark"] = pending
		return window

	marked, errors = _mark_absent(pending, day, shift, due_at, batch_size)
	window["marked_employees"] = marked
	window["errors"] = errors
	return window


def _mark_absent(employees, day, shift, due_at, batch_size):
	"""Insert and submit one Absent per employee, in committed batches.

	Batch size comes from Biometric Setting; 0, or a size larger than the list,
	means one commit at the end. Committing per batch is why a failure late in a
	large run cannot roll back the rows already written — the same reason HRMS
	commits per chunk in `process_auto_attendance`.
	"""
	from frappe.utils import create_batch
	from hrms.hr.doctype.attendance.attendance import mark_attendance

	if not employees:
		return [], []

	batch_size = cint(batch_size)
	if batch_size < 1:
		batch_size = len(employees)

	marked = []
	errors = []
	for batch in create_batch(employees, batch_size):
		for employee in batch:
			try:
				name = mark_attendance(employee, day, "Absent", shift)
			except Exception:
				errors.append(employee)
				frappe.log_error(
					title=f"Upande TA absent marking failed: {employee} {day}",
					message=frappe.get_traceback(),
				)
				continue

			if not name:
				# mark_attendance swallows duplicate / overlapping-shift errors
				# and returns None. Something already covers the date, which is
				# exactly what we want to leave alone.
				continue

			marked.append(employee)
			_stamp_reason(name, shift, due_at)
		if not frappe.in_test:
			frappe.db.commit()  # nosemgrep - each batch must survive a later failure

	return marked, errors


def _stamp_reason(attendance, shift, due_at):
	"""Record why the row exists, the way HRMS does for its own auto-Absents.

	Isolated from the marking itself: an Absent that is written but not
	annotated is still correct, and must not be reported as a failure. `str()`
	rather than `format_datetime`, which needs a user locale this can run
	without (a scheduled job on a site with no date format set raises inside
	frappe.locale).
	"""
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Attendance",
				"reference_name": attendance,
				"content": frappe._(
					"Marked Absent by Upande TA: no check-in logs for shift {0} by {1}."
				).format(shift, str(due_at)),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"Upande TA: could not comment on Absent {attendance}",
			message=frappe.get_traceback(),
		)


# --------------------------------------------------------------------------- #
# lookups
# --------------------------------------------------------------------------- #


def _settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)


def _shift_types(all_shifts):
	filters = {} if all_shifts else {"enable_auto_attendance": 1}
	return frappe.get_all("Shift Type", filters=filters, pluck="name", order_by="name")


def _shift_window(shift, day):
	"""(actual_start, actual_end, shift_end) of `shift` on `day`.

	`actual_start`/`actual_end` bound the period a scan can belong to (the
	shift's times widened by `begin_check_in_before_shift_start_time` and
	`allow_check_out_after_shift_end_time`); `shift_end` is the shift's own
	scheduled end, which is what the grace period is measured from.

	Delegates to HRMS so a shift crossing midnight resolves the same way it does
	everywhere else in attendance: `get_shift_details` anchors the window on the
	timestamp it is given, so it is given that day's own start time.
	"""
	from hrms.hr.doctype.shift_assignment.shift_assignment import get_shift_details

	start_time = frappe.db.get_value("Shift Type", shift, "start_time")
	if start_time is None:
		return None

	for_timestamp = datetime.combine(getdate(day), get_time(start_time))
	details = get_shift_details(shift, for_timestamp)
	if not details or not details.get("actual_end"):
		return None

	return (
		get_datetime(details["actual_start"]),
		get_datetime(details["actual_end"]),
		get_datetime(details["end_datetime"]),
	)


def _candidate_employees(shift, day):
	"""Active employees who work `shift` on `day`.

	Two sources, with the precedence ``get_employee_shift`` uses: a submitted
	Shift Assignment covering the date wins, and ``Employee.default_shift``
	applies only when no assignment covers the date at all. HRMS instead unions
	the two sets (``get_assigned_employees(consider_default_shift=True)``),
	which is how an employee assigned to one shift but carrying another as
	their default gets an Absent from one shift's run beside a Present from the
	other's.
	"""
	day = getdate(day)
	rows = frappe.db.sql(
		"""
		SELECT sa.employee
		FROM `tabShift Assignment` sa
		INNER JOIN `tabEmployee` e ON e.name = sa.employee
		WHERE sa.shift_type = %(shift)s
		  AND sa.docstatus = 1
		  AND sa.status = 'Active'
		  AND sa.start_date <= %(day)s
		  AND (sa.end_date IS NULL OR sa.end_date >= %(day)s)
		  AND e.status = 'Active'
		  AND (e.date_of_joining IS NULL OR e.date_of_joining <= %(day)s)
		  AND (e.relieving_date IS NULL OR e.relieving_date >= %(day)s)
		""",
		{"shift": shift, "day": day},
		pluck=True,
	)
	candidates = set(rows)

	default_shift_rows = frappe.db.sql(
		"""
		SELECT e.name
		FROM `tabEmployee` e
		WHERE e.default_shift = %(shift)s
		  AND e.status = 'Active'
		  AND (e.date_of_joining IS NULL OR e.date_of_joining <= %(day)s)
		  AND (e.relieving_date IS NULL OR e.relieving_date >= %(day)s)
		  AND NOT EXISTS (
			SELECT 1 FROM `tabShift Assignment` sa
			WHERE sa.employee = e.name
			  AND sa.docstatus = 1
			  AND sa.status = 'Active'
			  AND sa.start_date <= %(day)s
			  AND (sa.end_date IS NULL OR sa.end_date >= %(day)s)
		  )
		""",
		{"shift": shift, "day": day},
		pluck=True,
	)

	return candidates | set(default_shift_rows)


def _shift_belongs_to(employee, shift, at_timestamp):
	"""True when `shift` really is this employee's shift at `at_timestamp`.

	The candidate query above is set-based and cheap; this confirms each
	employee it produced against HRMS's own resolver, which is the authority on
	whose shift a moment belongs to (overlapping assignments, an assignment
	that starts or ends mid-window, `default_shift` only where no assignment
	covers the date). It is the same guard HRMS applies before writing its own
	Absents — `shift_details.shift_type.name == self.name` in
	`mark_absent_for_dates_with_no_attendance` — so nobody is ever marked
	Absent under a shift that is not theirs.

	`next_shift_direction` is left None on purpose: a nearby shift is not this
	day's shift, and searching for one is how an employee with no shift at all
	on the date ends up attached to somebody's window.
	"""
	from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift

	try:
		details = get_employee_shift(employee, at_timestamp, consider_default_shift=True)
	except Exception:
		# A broken shift setup for one employee must not stop the run; without a
		# resolvable shift there is nothing to be absent from.
		return False

	shift_type = (details or {}).get("shift_type") or {}
	return (shift_type.get("name") if hasattr(shift_type, "get") else None) == shift


def _employees_with_checkins(employees, actual_start, actual_end):
	"""Employees with any scan inside the window — IN or OUT, either way they
	were at work and their status is auto attendance's call, not ours."""
	if not employees:
		return set()

	return set(
		frappe.db.sql(
			"""
			SELECT DISTINCT employee
			FROM `tabEmployee Checkin`
			WHERE employee IN %(employees)s
			  AND time BETWEEN %(start)s AND %(end)s
			""",
			{"employees": tuple(employees), "start": actual_start, "end": actual_end},
			pluck=True,
		)
	)


def _employees_with_attendance(employees, day):
	"""Employees whose date is already accounted for, under any shift."""
	if not employees:
		return set()

	return set(
		frappe.db.sql(
			"""
			SELECT DISTINCT employee
			FROM `tabAttendance`
			WHERE employee IN %(employees)s
			  AND attendance_date = %(day)s
			  AND docstatus < 2
			""",
			{"employees": tuple(employees), "day": getdate(day)},
			pluck=True,
		)
	)


def _employees_on_leave(employees, day):
	"""Employees with an approved leave covering the date.

	Leave Application creates the Attendance itself, but not necessarily before
	this runs, so the leave is checked directly rather than inferred from a row
	that may not exist yet.
	"""
	if not employees:
		return set()

	return set(
		frappe.db.sql(
			"""
			SELECT DISTINCT employee
			FROM `tabLeave Application`
			WHERE employee IN %(employees)s
			  AND docstatus = 1
			  AND status = 'Approved'
			  AND from_date <= %(day)s
			  AND to_date >= %(day)s
			""",
			{"employees": tuple(employees), "day": getdate(day)},
			pluck=True,
		)
	)


_REST_DAY_CACHE_KEY = "upande_ta_absent_rest_days"


def _rest_day_state(employee, day):
	"""One of "work", "rest", "unknown" for `day`, from the employee's OWN list.

	Week off is Holiday-List driven on these sites: Bulk Week Off assigns each
	employee a list like "Sunday Week Off 2026" (52 `weekly_off` rows) through
	an Employee Transfer plus a linked Holiday List Assignment. So the only
	evidence that a given date is *not* someone's week off is a holiday list
	assigned to that employee.

	Hence "work" is returned only when the list came from the employee. A
	company default (Kaitet Ltd. and Karen Roses both point at "Local Holidays
	2026", which has zero `weekly_off` rows) proves public holidays and nothing
	whatsoever about week offs — trusting it would mark every employee without
	their own list Absent on their rest day, which is exactly the failure this
	guards. Those employees come back "unknown" and are skipped, counted in the
	window summary so the gap is visible rather than silent.

	Deliberately not ``Shift Type.get_holiday_list`` either: that prefers the
	shift's own list when one is set, ignoring the employee's week off, which is
	what the dashboard's ``off_absents`` cleanup mode exists to undo.
	"""
	cache = frappe.flags.setdefault(_REST_DAY_CACHE_KEY, {})
	day = getdate(day)

	list_key = ("list", employee, str(day))
	if list_key not in cache:
		cache[list_key] = _holiday_list_for(employee, day)
	holiday_list, source = cache[list_key]

	if not holiday_list:
		return "unknown"

	day_key = ("day", holiday_list, str(day))
	if day_key not in cache:
		cache[day_key] = bool(
			frappe.db.get_value(
				"Holiday",
				{"parent": holiday_list, "holiday_date": day},
				"name",
			)
		)

	if cache[day_key]:
		# A holiday of any kind — week off or public holiday — is never a day to
		# be absent from, whichever list it came from.
		return "rest"

	return "work" if source == "employee" else "unknown"


def _holiday_list_for(employee, day):
	"""(holiday_list, source) for `employee` on `day`; source is "employee" or "company".

	Resolved in the order that decides whether the answer may be trusted about
	week offs at all:

	  1. a submitted **Holiday List Assignment** for this employee — dated
	     (`from_date <= day`), so it answers for the day in question rather than
	     for today. This is what Bulk Week Off writes alongside the Employee
	     Transfer, and what HRMS itself reads through the
	     ``employee_holiday_list`` hook;
	  2. ``Employee.holiday_list``, for a site that still carries only the field;
	  3. the company's assignment or default — labelled "company", because a
	     company list carries public holidays, not anybody's week off.

	Steps 1 and 2 are asked directly rather than through
	``get_holiday_list_for_employee``, whose employee and company answers are
	indistinguishable in the return value: a company-wide list would come back
	looking employee-specific and authorise marking on a rest day.
	"""
	employee_row = (
		frappe.db.get_value("Employee", employee, ["holiday_list", "company"], as_dict=True) or frappe._dict()
	)

	assigned = _assigned_holiday_list(employee, day)
	if assigned:
		return assigned, "employee"

	if employee_row.get("holiday_list"):
		return employee_row["holiday_list"], "employee"

	company = employee_row.get("company")
	if company:
		company_assigned = _assigned_holiday_list(company, day)
		if company_assigned:
			return company_assigned, "company"
		return frappe.db.get_value("Company", company, "default_holiday_list"), "company"

	return None, "company"


def _assigned_holiday_list(assigned_to, day):
	"""The dated Holiday List Assignment for an employee or a company, if any.

	Returns None where the doctype does not exist (pre-v16 HRMS), leaving the
	`Employee.holiday_list` fallback to answer.
	"""
	try:
		from hrms.utils.holiday_list import get_assigned_holiday_list
	except ImportError:
		return None

	try:
		return get_assigned_holiday_list(assigned_to, day)
	except Exception:
		return None


# --------------------------------------------------------------------------- #
# check-ins parked by a failed duplicate check
# --------------------------------------------------------------------------- #


@frappe.whitelist()
def reclaim_skipped_checkins(days: str | int | None = None):
	"""Whitelisted wrapper for the backlog sweep below."""
	frappe.only_for(("System Manager", "HR Manager"))

	return _reclaim_skipped_checkins(days)


def _reclaim_skipped_checkins(days=None):
	"""Un-skip check-ins that auto attendance gave up on over an Absent.

	When ``mark_attendance_and_link_log`` hits DuplicateAttendanceError it calls
	``handle_attendance_exception``, which sets ``skip_auto_attendance = 1`` on
	the logs and comments the error onto them. Those scans are then excluded
	from every future run — permanently — even though the record that blocked
	them was an auto-marked Absent that should have given way.

	The Attendance override now cancels such an Absent instead of colliding
	with it, so clearing the flag lets HRMS's own run finish the job properly
	(hours, late entry, early exit and all). Backlog only; new failures should
	not occur.
	"""
	settings = _settings()
	lookback = _capped_days(
		cint(days) or cint(settings.get("absent_lookback_days")),
		cint(settings.get("absent_max_lookback_days")),
	)
	since = add_days(getdate(nowdate()), -lookback)

	rows = frappe.db.sql(
		"""
		SELECT ec.name
		FROM `tabEmployee Checkin` ec
		INNER JOIN `tabAttendance` a
		        ON a.employee = ec.employee
		       AND a.attendance_date = DATE(ec.time)
		       AND a.docstatus = 1
		       AND a.status = 'Absent'
		WHERE ec.skip_auto_attendance = 1
		  AND ec.attendance IS NULL
		  AND ec.time >= %(since)s
		""",
		{"since": since},
		pluck=True,
	)

	for name in rows:
		frappe.db.set_value("Employee Checkin", name, "skip_auto_attendance", 0, update_modified=False)

	if rows:
		frappe.db.commit()  # nosemgrep - the flag reset must land for the next run

	return {"since": str(since), "reclaimed": len(rows), "checkins": rows}


# --------------------------------------------------------------------------- #
# defaults for an existing install
# --------------------------------------------------------------------------- #


def ensure_absent_marking_defaults():
	"""Write each setting's shipped default for any field that has no row yet.

	A Single doctype materialises field defaults only when the document is
	saved, so on a site that already has a Biometric Setting every field added
	by an upgrade reads as empty until someone opens and saves the form. For
	these fields empty is not a harmless "not configured": it is a zero grace
	period and a supersede switch that reads as off.

	The values come from the doctype's own field defaults, so the form and this
	function can never disagree — the doctype is the single place a shipped
	default is set. Only missing fields are seeded, so an admin's own choice,
	including unticking a Check, is never overwritten on the next migrate.

	`enable_absent` is deliberately not in the list: the pass stays off until
	someone turns it on, so an upgrade never starts writing Absent rows on its
	own.
	"""
	# Not `table_exists`: a Single doctype has no table of its own, its values
	# live in `tabSingles`.
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return

	existing = set(
		frappe.db.sql(
			"""SELECT field FROM tabSingles WHERE doctype = %s""",
			(SETTINGS_DOCTYPE,),
			pluck=True,
		)
	)

	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	for fieldname in ABSENT_MARKING_FIELDS:
		if fieldname in existing:
			continue
		df = meta.get_field(fieldname)
		if not df:
			# The doctype JSON has not synced yet; the next migrate will.
			continue
		if df.default in (None, ""):
			continue
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, df.default)
