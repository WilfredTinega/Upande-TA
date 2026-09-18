# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Segment planner for Holiday Assignment Tool.

This module is deliberately framework-free: no ``frappe.db``, no
``frappe.throw``, no ``getdate``, no ``Document``. It works on stdlib
``datetime.date`` objects and plain strings, so the rule that decides *which
Holiday List an employee is on, from which date* can be unit-tested with
nothing but the standard library.

A "boundary" is a ``(date, holiday_list)`` pair meaning **from this date
onward, this list applies** — exactly the shape of an HRMS Holiday List
Assignment (``assigned_to`` + ``from_date`` + ``holiday_list``). A Holiday List
Assignment has no end date; it runs until the next one starts. That is why an
end date and a per-day exception are both expressed as *extra boundaries*
rather than as ranges.

``{assigned_to, from_date}`` is unique in Holiday List Assignment, so two
boundaries on the same date would throw on submit. Collapsing (below)
guarantees the returned list has one boundary per date, in ascending date
order, with no two consecutive boundaries naming the same list.
"""

from __future__ import annotations

import datetime

__all__ = ["as_date", "plan_segments"]

ONE_DAY = datetime.timedelta(days=1)


def as_date(value):
	"""Coerce ``value`` to a ``datetime.date``.

	Accepts ``datetime.date``, ``datetime.datetime``, an ISO ``YYYY-MM-DD``
	string (anything after the first 10 characters is ignored, so a database
	timestamp works too), or ``None``/``""`` which return ``None``.

	This exists so callers do not have to reach for ``frappe.utils.getdate``
	to use :func:`plan_segments`; it uses the standard library only.
	"""
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _exception_pair(exception):
	"""Normalise one exception into ``(date, holiday_list)``.

	Tolerates a 2-tuple/list, a mapping with ``exception_date`` /
	``holiday_list`` keys, or any object with those attributes (which is what a
	Holiday Assignment Tool Exception child row is) — without importing frappe.
	"""
	if isinstance(exception, dict):
		return as_date(exception.get("exception_date")), exception.get("holiday_list")
	if isinstance(exception, (list, tuple)):
		return as_date(exception[0]), exception[1]
	return (
		as_date(getattr(exception, "exception_date", None)),
		getattr(exception, "holiday_list", None),
	)


def plan_segments(base_list, from_date, to_date, exceptions, prior_list):
	"""Return an ordered list of ``(datetime.date, holiday_list)`` boundaries.

	:param base_list: the Holiday List the employee moves to for the period.
	:param from_date: first day of the period (date, datetime or ISO string).
	:param to_date: last day of the period, or ``None`` for open-ended — an
	        open-ended period emits no restore boundary, which is the
	        pre-existing Bulk Week Off behaviour.
	:param exceptions: iterable of ``(date, holiday_list)`` pairs (or mappings
	        or child rows, see :func:`_exception_pair`) — single dates inside the
	        period that use a different list. Order does not matter; they are
	        sorted here.
	:param prior_list: the list the employee was on before this document, used
	        for the restore boundary at ``to_date + 1``. ``None`` means the
	        employee had no previous assignment, so nothing is restored (a
	        boundary with a null list is never emitted).

	:returns: ``list[tuple[datetime.date, str]]`` — ascending by date, one
	        boundary per date, and never two consecutive boundaries naming the
	        same holiday list. May be empty. Each tuple maps 1:1 onto one
	        Holiday List Assignment to create.

	Construction, then collapse:

	1. ``(from_date, base_list)``.
	2. per exception, in date order: ``(exc_date, exc_list)`` then
	   ``(exc_date + 1, base_list)`` to resume the base list the next day.
	3. ``(to_date + 1, prior_list)`` to restore, when both are known.
	4. stable-sort by date, then keep only the **last** boundary generated for
	   any given date (later wins — the restore beats a resume-base boundary on
	   the same day, and an exception beats the period start).
	5. drop any boundary whose list equals the one immediately before it, so a
	   no-op exception (``exc_list == base_list``) emits no record at all.

	Note that step 5 never drops the *first* boundary even when
	``base_list == prior_list``; the function does not assume ``prior_list`` is
	in force on ``from_date`` (the caller may be re-asserting it deliberately).
	Exceptions outside ``[from_date, to_date]`` are *not* rejected here — this
	function sorts and processes whatever it is given; the range check belongs
	to the caller's ``validate``.
	"""
	from_date = as_date(from_date)
	to_date = as_date(to_date)

	if not base_list or from_date is None:
		return []

	pairs = [_exception_pair(exception) for exception in (exceptions or [])]
	pairs = [(date, holiday_list) for date, holiday_list in pairs if date and holiday_list]
	pairs.sort(key=lambda pair: pair[0])

	boundaries = [(from_date, base_list)]
	for exception_date, exception_list in pairs:
		boundaries.append((exception_date, exception_list))
		boundaries.append((exception_date + ONE_DAY, base_list))

	if to_date and prior_list:
		boundaries.append((to_date + ONE_DAY, prior_list))

	# Stable sort: boundaries sharing a date keep the order they were generated
	# in, so "the last one wins" below is well defined.
	boundaries.sort(key=lambda boundary: boundary[0])

	# One boundary per date. Equal dates are adjacent after the sort.
	deduped = []
	for boundary in boundaries:
		if deduped and deduped[-1][0] == boundary[0]:
			deduped[-1] = boundary
		else:
			deduped.append(boundary)

	# Drop boundaries that do not actually change the holiday list.
	collapsed = []
	for date, holiday_list in deduped:
		if collapsed and collapsed[-1][1] == holiday_list:
			continue
		collapsed.append((date, holiday_list))

	return collapsed
