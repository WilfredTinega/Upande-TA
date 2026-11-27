# Copyright (c) 2026, Upande LTD and contributors

"""IN / OUT for external guards' gate punches (Security Checkin, upande_security).

Guards press whichever key is under their finger, so a gate device reports every
guard punch as IN (Karen's TDBD260900258: 303 punches, not one OUT). The same
two passes normalize_checkin_directions runs for Employee Checkin give each
guard's shift a clean IN ... OUT: a trailing IN is flipped to OUT, and a window
with no IN at all has its earliest scan flipped to IN. Lone scans and middle
scans are left alone, and nothing is deleted.

A shift window is the guard's Security Guard Shift Assignment the punch falls in
(from an hour before it starts until it ends, the nearest start winning), so a
night guard's scans either side of midnight stay together. A punch outside any
assignment falls back to its calendar day."""

from datetime import timedelta

import frappe
from frappe.utils import add_days, get_datetime, get_timedelta, getdate, today

EARLY = timedelta(minutes=60)


def _shift_windows(guards, range_start, range_end):
	"""{guard: [(start, end, name)]} from Security Guard Shift Assignment."""
	if not guards or not frappe.db.exists("DocType", "Security Guard Shift Assignment"):
		return {}
	rows = frappe.get_all(
		"Security Guard Shift Assignment",
		filters={
			"external_guard": ["in", list(guards)],
			"status": ["!=", "Cancelled"],
			"start_date": ["between", [getdate(range_start) - timedelta(days=1), getdate(range_end)]],
		},
		fields=["name", "external_guard", "start_date", "start_time", "end_date", "end_time"],
	)
	out = {}
	for r in rows:
		if not (r.start_date and r.end_date) or r.start_time is None or r.end_time is None:
			continue
		start = get_datetime(r.start_date) + get_timedelta(r.start_time)
		end = get_datetime(r.end_date) + get_timedelta(r.end_time)
		if end <= start:
			end = end + timedelta(days=1)
		out.setdefault(r.external_guard, []).append((start, end, r.name))
	return out


def _pick(windows, punch):
	best = None
	for start, end, name in windows or ():
		if start - EARLY <= punch <= end:
			gap = abs((punch - start).total_seconds())
			if best is None or gap < best[0]:
				best = (gap, name, start)
	return best


def _set(name, log_type):
	try:
		if frappe.db.get_value("Security Checkin", name, "log_type") == log_type:
			return False
		frappe.db.set_value("Security Checkin", name, "log_type", log_type, update_modified=False)
		return True
	except Exception as e:
		frappe.log_error(f"flip failed for {name}: {e}", "Security Checkin Direction Fix")
		return False


def normalize_security_checkin_directions(target_date=None, days=7, hours=0):
	"""Give each guard's shift (or day) a clean IN ... OUT pair."""
	if not frappe.db.exists("DocType", "Security Checkin"):
		return {"skipped": True, "reason": "no Security Checkin"}
	lookback = timedelta(days=int(days or 0), hours=int(hours or 0))
	if lookback <= timedelta(0):
		lookback = timedelta(days=1)
	end_date = getdate(target_date) if target_date else getdate(today())
	start_date = getdate(get_datetime(f"{end_date} 23:59:59") - lookback)
	range_start = get_datetime(f"{start_date} 00:00:00")
	range_end = get_datetime(f"{add_days(end_date, 1)} 23:59:59")

	logs = frappe.get_all(
		"Security Checkin",
		filters={"time": ["between", [range_start, range_end]], "security_guard": ["is", "set"]},
		fields=["name", "security_guard", "time", "log_type"],
		order_by="security_guard asc, time asc",
	)
	shifts = _shift_windows({log.security_guard for log in logs}, range_start, range_end)

	windows, window_dates = {}, {}
	for log in logs:
		punch = get_datetime(log.time)
		hit = _pick(shifts.get(log.security_guard), punch)
		if hit:
			key = (log.security_guard, "shift", hit[1])
			window_dates[key] = getdate(hit[2])
		else:
			key = (log.security_guard, "date", str(getdate(punch)))
			window_dates[key] = getdate(punch)
		windows.setdefault(key, []).append(log)

	flipped_out = flipped_in = examined = 0
	for key, scans in windows.items():
		if not (start_date <= window_dates[key] <= end_date) or len(scans) < 2:
			continue
		examined += 1
		scans.sort(key=lambda s: get_datetime(s.time))
		last = scans[-1]
		if (last.log_type or "") == "IN" and _set(last.name, "OUT"):
			last.log_type = "OUT"
			flipped_out += 1
		if not any((s.log_type or "") == "IN" for s in scans):
			first = scans[0]
			if (first.log_type or "") == "OUT" and _set(first.name, "IN"):
				first.log_type = "IN"
				flipped_in += 1

	frappe.db.commit()
	return {
		"windows": examined,
		"total_scans": len(logs),
		"flipped_to_out": flipped_out,
		"flipped_to_in": flipped_in,
	}
