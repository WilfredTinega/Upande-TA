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
   shift. On a working day that is worked hours minus the shift length; on a
   rest day or public holiday every worked hour is overtime.
2. :func:`settle` — what gets paid: the lower of requested and biometric.
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
	"settle",
	"shift_length_hours",
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


def biometric_overtime(working_hours, shift_hours, day_type: str, *, maximum_hours: float = 0) -> float:
	"""Overtime hours the attendance supports for one day.

	:param working_hours: Attendance.working_hours. Negative or missing is 0.
	:param shift_hours: the shift's length; only used on a working day.
	:param day_type: one of :data:`DAY_TYPES`.
	:param maximum_hours: cap per day, from the Overtime Type's Maximum
	        Overtime Hours Allowed; 0 means no cap.
	"""
	if day_type not in DAY_TYPES:
		raise ValueError(f"unknown day type {day_type!r}")

	worked = max(float(working_hours or 0), 0.0)
	if day_type == WORKING_DAY:
		raw = max(worked - float(shift_hours or 0), 0.0)
	else:
		raw = worked

	hours = raw
	if maximum_hours:
		hours = min(hours, float(maximum_hours))
	return round(hours, 2)


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
