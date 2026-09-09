# Copyright (c) 2026, Upande LTD and contributors

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, get_time, getdate, today


def prevent_duplicate(doc, method=None):
	if not doc.employee or not doc.time:
		return

	filters = {
		"employee": doc.employee,
		"time":     doc.time,
	}
	if doc.log_type:
		filters["log_type"] = doc.log_type
	else:
		filters["log_type"] = ["in", ["", None]]
	if not doc.is_new():
		filters["name"] = ["!=", doc.name]

	existing = frappe.db.get_value("Employee Checkin", filters, "name")
	if existing:
		frappe.throw(
			_("Duplicate Check-in: {0} already has a {1} entry at {2} ({3}).").format(
				frappe.bold(doc.employee),
				frappe.bold(doc.log_type or "no log type"),
				frappe.bold(frappe.utils.format_datetime(doc.time)),
				frappe.utils.get_link_to_form("Employee Checkin", existing)
			),
			title=_("Duplicate Check-in")
		)


def _overnight_shift_types():
	"""Shift Types whose window crosses midnight (start_time > end_time),
	e.g. Night Shift 17:00 → 01:00. Returns {shift_name: (start_time, end_time)}."""
	out = {}
	for s in frappe.get_all(
		"Shift Type",
		fields=["name", "start_time", "end_time"],
	):
		start_t, end_t = s.start_time, s.end_time
		if start_t is None or end_t is None:
			continue
		if get_time(start_t) > get_time(end_t):
			out[s.name] = (start_t, end_t)
	return out


def _employee_overnight_shift_on(employee, log_date, overnight_types):
	"""Return the overnight Shift Type assigned to `employee` covering `log_date`
	via a submitted, Active Shift Assignment, else None. An open-ended assignment
	(end_date NULL) is treated as ongoing."""
	if not overnight_types:
		return None
	assignment = frappe.db.sql(
		"""
		SELECT shift_type
		FROM `tabShift Assignment`
		WHERE employee = %(employee)s
		  AND docstatus = 1
		  AND status = 'Active'
		  AND shift_type IN %(shifts)s
		  AND start_date <= %(d)s
		  AND (end_date IS NULL OR end_date >= %(d)s)
		ORDER BY start_date DESC
		LIMIT 1
		""",
		{
			"employee": employee,
			"shifts":   tuple(overnight_types.keys()),
			"d":        log_date,
		},
	)
	return assignment[0][0] if assignment else None


def _set_log_type(checkin_name, log_type, context):
	"""Force a checkin's log_type. Returns True if it changed, False otherwise.

	Writes the field directly (frappe.db.set_value) rather than saving the doc, so
	nothing can block the flip — no validation, no linked-attendance guard, no
	duplicate check — and the record is only ever *updated*, never deleted."""
	try:
		if frappe.db.get_value("Employee Checkin", checkin_name, "log_type") == log_type:
			return False
		frappe.db.set_value(
			"Employee Checkin", checkin_name, "log_type", log_type, update_modified=False
		)
		return True
	except Exception as e:
		frappe.log_error(
			f"{context} flip failed for {checkin_name}: {e}",
			"Employee Checkin Direction Fix",
		)
		return False


def _flip_to_out(checkin_name):
	return _set_log_type(checkin_name, "OUT", "close-open-in")


def _flip_to_in(checkin_name):
	return _set_log_type(checkin_name, "IN", "open-closed-out")


def normalize_checkin_directions(target_date=None, days=7):
	"""Give each (employee, working day) window a clean IN ... OUT pair.

	Two passes, in this order:

	1. **Close a day that never closed** — a trailing IN means the employee
	   scanned in and the reader never recorded the exit, so the last scan is
	   flipped to OUT.
	2. **Open a day that never opened** — some readers stamp *every* scan OUT,
	   leaving a window with no IN at all. The earliest scan is flipped to IN.
	   Runs second on purpose: a window like OUT,IN becomes OUT,OUT in pass 1
	   and is then opened here, instead of being left with no entry direction.

	Both passes need at least two scans in the window, so a lone scan is never
	reinterpreted. Records are never deleted and middle scans are left intact.
	Blank log_types (readers reporting punch state 255) are not touched — there
	is no direction to correct, only one to invent.

	Windows are the employee's **assigned shift**, taken from the `shift` /
	`shift_actual_start` that HRMS stamps on each checkin — so every Shift Type
	is respected, the Shift Type's check-in/check-out margins are already
	applied, and a night worker's scans either side of midnight stay in one
	window. A scan with no shift resolved falls back to its calendar day, with
	an early-morning scan pulled back to the previous day only for employees on
	an overnight Shift Type."""
	end_date   = getdate(target_date) if target_date else getdate(today())
	start_date = add_days(end_date, -(days - 1))

	range_start = get_datetime(f"{start_date} 00:00:00")
	range_end   = get_datetime(f"{add_days(end_date, 1)} 23:59:59")

	overnight_types = _overnight_shift_types()

	logs = frappe.db.get_all(
		"Employee Checkin",
		filters={
			"time":       ["between", [range_start, range_end]],
			"employee":   ["is", "set"],
		},
		fields=[
			"name", "employee", "time", "log_type",
			"shift", "shift_actual_start",
		],
		order_by="employee asc, time asc",
	)

	windows = {}
	window_dates = {}
	shift_windowed = 0
	for log in logs:
		log_dt   = get_datetime(log.time)
		log_date = getdate(log_dt)

		# The assigned shift wins. HRMS stamps `shift` / `shift_actual_start`
		# on a checkin when it can resolve one, and that window is what the
		# employee was actually rostered for: it covers every Shift Type rather
		# than only overnight ones, it already honours the Shift Type's
		# check-in/check-out margins, and it keeps a night worker's scans on
		# both sides of midnight inside ONE window. Two employees on different
		# shifts the same calendar day get separate windows, and a scan that
		# falls outside anyone's roster is never pulled into a shift.
		if log.get("shift") and log.get("shift_actual_start"):
			key = (log.employee, "shift", log.shift, str(log.shift_actual_start))
			window_date = getdate(log.shift_actual_start)
			shift_windowed += 1
		else:
			# No shift resolved on the scan (~1 in 5): fall back to the calendar
			# day, pulling an early-morning scan back to the previous day only
			# when the employee is on an overnight Shift Type.
			working_date = log_date
			shift_today = _employee_overnight_shift_on(log.employee, log_date, overnight_types)
			if shift_today and get_time(log_dt) >= get_time(overnight_types[shift_today][0]):
				pass
			else:
				prev_date = add_days(log_date, -1)
				shift_prev = _employee_overnight_shift_on(log.employee, prev_date, overnight_types)
				if shift_prev and get_time(log_dt) < get_time(overnight_types[shift_prev][0]):
					working_date = prev_date
			key = (log.employee, "date", str(working_date))
			window_date = working_date

		windows.setdefault(key, []).append(log)
		window_dates[key] = window_date

	flipped_out = 0
	flipped_in = 0
	candidates = 0
	examined = 0
	for key, scans in windows.items():
		if not (start_date <= window_dates[key] <= end_date):
			continue
		if len(scans) < 2:
			continue

		examined += 1
		scans.sort(key=lambda s: get_datetime(s.time))
		needed = False

		# Pass 1 — close a day that never closed.
		last_scan = scans[-1]
		if (last_scan.log_type or "") == "IN":
			needed = True
			if _flip_to_out(last_scan.name):
				last_scan.log_type = "OUT"
				flipped_out += 1

		# Pass 2 — open a day that never opened. Only when nothing in the window
		# opens it, so a day that already has an IN somewhere is left alone.
		if not any((s.log_type or "") == "IN" for s in scans):
			first_scan = scans[0]
			if (first_scan.log_type or "") == "OUT":
				needed = True
				if _flip_to_in(first_scan.name):
					first_scan.log_type = "IN"
					flipped_in += 1

		if needed:
			candidates += 1

	frappe.db.commit()
	frappe.logger().info(
		f"normalize_checkin_directions: scanned {start_date}..{end_date}, "
		f"flipped {flipped_out} IN→OUT and {flipped_in} OUT→IN across "
		f"{candidates} of {examined} windows "
		f"({shift_windowed} of {len(logs)} scans grouped by assigned shift, "
		f"the rest by calendar day; {len(overnight_types)} overnight shift type(s))"
	)
	return {
		"start_date":      str(start_date),
		"end_date":        str(end_date),
		"windows":         examined,
		"shift_windowed_scans": shift_windowed,
		"total_scans":     len(logs),
		"candidates":      candidates,
		"flipped":         flipped_out + flipped_in,
		"flipped_to_out":  flipped_out,
		"flipped_to_in":   flipped_in,
		"overnight_types": list(overnight_types.keys()),
	}


# Historical name kept as an alias: the pass used to only close open INs.
auto_close_open_ins = normalize_checkin_directions
