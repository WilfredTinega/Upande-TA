# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""The overtime arithmetic behind Bulk Overtime.

Framework-free on purpose, like ``holiday_segments``: no ``frappe``, only the
standard library, so every rule that decides *how many hours get paid* can be
unit-tested without a site. Nothing here is a policy constant: the daily cap
and the shift length arrive as arguments, read by the caller from the run's
Overtime Type and from the Shift Type (or HR Settings) respectively. Rates are
not this module's business at all — they live on the Overtime Type.

Two steps per employee per date:

1. :func:`biometric_overtime` — what the attendance says was worked over the
   shift. On a working day that is worked hours minus the shift length,
   counted from the shift's start: time clocked before the shift begins is
   not overtime (:func:`hours_before_shift`), while a late arrival is made up
   out of the time stayed past the shift's end before any of it becomes
   overtime. On a rest day or public holiday every worked hour is overtime.
2. :func:`settle` — what gets paid: the lower of requested and biometric.

A Week request carries a weekly total rather than hours per day;
:func:`split_across_working_days` turns it into the per-day hours step 2 reads,
and :func:`settle_week` then pays the week against that total rather than day
by day, so a short day is made up by a long one.
"""

from __future__ import annotations

import datetime

__all__ = [
	"CAPPED",
	"DAY_TYPES",
	"MATCHED",
	"NO_ATTENDANCE",
	"NO_CLOCK_OUT",
	"NO_SHIFT",
	"PUBLIC_HOLIDAY",
	"REST_DAY",
	"WORKED_LESS",
	"WORKING_DAY",
	"biometric_overtime",
	"hours_before_shift",
	"settle",
	"settle_week",
	"shift_length_hours",
	"split_across_working_days",
	"swapped_rest_days",
]

WORKING_DAY = "Working Day"
REST_DAY = "Rest Day"
PUBLIC_HOLIDAY = "Public Holiday"
DAY_TYPES = (WORKING_DAY, REST_DAY, PUBLIC_HOLIDAY)

#: Biometric overtime equals the request.
MATCHED = "Matched"
#: Worked more overtime than requested; paid the request.
CAPPED = "Capped at Request"
#: Worked less overtime than requested; paid what was worked.
WORKED_LESS = "Worked Less"
#: Present, but no working hours on the attendance (a missing punch).
NO_CLOCK_OUT = "No Clock-Out"
#: No Present attendance on the date at all.
NO_ATTENDANCE = "No Attendance"
#: Attendance with no shift, and no standard working hours in HR Settings to
#: fall back on — there is nothing to measure "beyond the shift" against.
NO_SHIFT = "No Shift"


def _hours(value) -> float | None:
	"""A ``datetime.timedelta`` / ``datetime.time`` as hours since midnight."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime.timedelta):
		return value.total_seconds() / 3600
	if isinstance(value, datetime.time):
		return value.hour + value.minute / 60 + value.second / 3600
	if isinstance(value, str):
		parts = [float(p) for p in value.split(":")]
		parts += [0] * (3 - len(parts))
		return parts[0] + parts[1] / 60 + parts[2] / 3600
	raise TypeError(f"cannot read a time of day from {value!r}")


def shift_length_hours(start_time, end_time) -> float | None:
	"""Length of a shift in hours, wrapping past midnight for night shifts
	(22:00 -> 06:00 is 8). ``None`` when either end is missing or the two are
	equal — a zero-length shift is a data error, not a 24-hour one."""
	start, end = _hours(start_time), _hours(end_time)
	if start is None or end is None:
		return None
	length = (end - start) % 24
	return round(length, 4) or None


def hours_before_shift(first_in, shift_start) -> float:
	"""Hours clocked in before the shift began: ``first_in`` and
	``shift_start`` are datetimes; 0 when either is missing or the arrival
	was on time or late."""
	if not first_in or not shift_start:
		return 0.0
	return round(max((shift_start - first_in).total_seconds() / 3600, 0.0), 4)


def biometric_overtime(
	working_hours, shift_hours, day_type: str, *, maximum_hours: float = 0, early_hours: float = 0
) -> float:
	"""Overtime hours the attendance supports for one day.

	:param working_hours: Attendance.working_hours. Negative or missing is 0.
	:param shift_hours: the shift's length; only used on a working day.
	:param day_type: one of :data:`DAY_TYPES`.
	:param maximum_hours: cap per day, from the Overtime Type's Maximum
	        Overtime Hours Allowed; 0 means no cap.
	:param early_hours: hours worked before the shift started
	        (:func:`hours_before_shift`). On a working day they are not
	        overtime, so the day is measured from the shift's start: someone
	        who came in an hour late must stay an hour past the end before
	        anything counts. Ignored on rest days and public holidays.
	"""
	if day_type not in DAY_TYPES:
		raise ValueError(f"unknown day type {day_type!r}")

	worked = max(float(working_hours or 0), 0.0)
	if day_type == WORKING_DAY:
		worked = max(worked - max(float(early_hours or 0), 0.0), 0.0)
		raw = max(worked - float(shift_hours or 0), 0.0)
	else:
		raw = worked

	hours = raw
	if maximum_hours:
		hours = min(hours, float(maximum_hours))
	return round(hours, 2)


def split_across_working_days(total, day_types) -> list[float]:
	"""Share a weekly total out over the working days of that week.

	One share per entry of ``day_types``, in order: rest days and public
	holidays get 0, every working day an equal share rounded to the hundredth,
	and the last working day takes the rounding difference so the shares add
	up to ``total`` exactly. All zeros when the week has no working day.
	"""
	for day_type in day_types:
		if day_type not in DAY_TYPES:
			raise ValueError(f"unknown day type {day_type!r}")

	total = max(float(total or 0), 0.0)
	working = [index for index, day_type in enumerate(day_types) if day_type == WORKING_DAY]
	shares = [0.0] * len(day_types)
	if not working or not total:
		return shares

	share = round(total / len(working), 2)
	for index in working:
		shares[index] = share
	shares[working[-1]] = round(total - share * (len(working) - 1), 2)
	return shares


def settle(
	requested, biometric, *, has_attendance: bool, has_hours: bool, has_shift: bool = True
) -> tuple[float, str]:
	"""``(hours to pay, status)`` for one requested day: the lower of the two.

	No attendance, attendance without working hours, and a working day with no
	shift length to measure against all pay nothing — HR can still pay such a
	row by hand, with a reason, on the Bulk Overtime form.
	"""
	requested = max(float(requested or 0), 0.0)
	biometric = max(float(biometric or 0), 0.0)

	if not has_attendance:
		return 0.0, NO_ATTENDANCE
	if not has_hours:
		return 0.0, NO_CLOCK_OUT
	if not has_shift:
		return 0.0, NO_SHIFT
	if biometric > requested:
		return requested, CAPPED
	if biometric == requested:
		return requested, MATCHED
	return biometric, WORKED_LESS


def settle_week(allowance, requested, biometric) -> list[tuple[float, str]]:
	"""``(hours to pay, status)`` for each payable day of one employee's week.

	A Week request approves a total for the week, not a fixed amount each day:
	3 hours on Monday and none on Tuesday is the same week as 1.5 on each. So
	the days are paid in date order, each up to the overtime the attendance
	supports, until ``allowance`` — the week's total less anything already paid
	in another batch — runs out.

	``requested`` is each day's share of the total and only decides the status;
	``biometric`` is what :func:`biometric_overtime` found. Days :func:`settle`
	refused (no attendance, no clock-out, no shift) are left out by the caller.
	"""
	if len(requested) != len(biometric):
		raise ValueError("requested and biometric must be the same length")

	left = max(float(allowance or 0), 0.0)
	settled = []
	for share, worked in zip(requested, biometric, strict=True):
		share = max(float(share or 0), 0.0)
		worked = max(float(worked or 0), 0.0)
		paid = round(min(worked, left), 2)
		left = max(round(left - paid, 2), 0.0)
		if paid < worked:
			status = CAPPED
		elif paid < share:
			status = WORKED_LESS
		else:
			status = MATCHED
		settled.append((paid, status))
	return settled


def swapped_rest_days(week) -> dict:
	"""``{rest day: day off}`` for one employee's Monday-to-Sunday week.

	``week`` is ``(date, day_type, worked, off)`` per day: ``worked`` when the
	attendance shows them at work, ``off`` when a working day they were due at
	work has no attendance and no leave. A rest day worked while a working day
	of the same week was taken off is a moved off, not overtime; each day off
	pairs with one worked rest day, in date order.
	"""
	rest_worked, days_off = [], []
	for date, day_type, worked, off in sorted(week, key=lambda day: day[0]):
		if day_type not in DAY_TYPES:
			raise ValueError(f"unknown day type {day_type!r}")
		if day_type == REST_DAY and worked:
			rest_worked.append(date)
		elif day_type == WORKING_DAY and off and not worked:
			days_off.append(date)
	return dict(zip(rest_worked, days_off, strict=False))
