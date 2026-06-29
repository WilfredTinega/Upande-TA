# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, get_time, getdate, today


def prevent_duplicate(doc, method=None):
	if not doc.employee or not doc.time:
		return

	filters = {
		"employee": doc.employee,
		"time":     doc.time,
		"log_type": doc.log_type or "",
	}
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


def _flip_to_out(checkin_name):
	"""Flip a checkin's log_type to OUT. Returns True if flipped, False on error."""
	try:
		doc = frappe.get_doc("Employee Checkin", checkin_name)
		if doc.log_type == "OUT":
			return False
		doc.flags.ignore_validate = True
		doc.log_type = "OUT"
		doc.save(ignore_permissions=True)
		return True
	except Exception as e:
		frappe.log_error(
			f"auto_close_open_ins flip failed for {checkin_name}: {e}",
			"Employee Checkin Auto-Close",
		)
		return False


def auto_close_open_ins(target_date=None, days=7):
	"""For each (employee, working day) with more than one IN scan, flip the
	trailing scan to OUT so the day has a clean IN/OUT pair. Records are never
	deleted — middle scans are left intact.

	Security/night-shift employees (assigned an overnight Shift Type whose
	start_time > end_time) are handled by *shift window*: an evening IN on day N
	is paired with the last scan on day N+1, rather than being grouped strictly by
	calendar day. Day-shift and unassigned employees keep the per-calendar-day
	behaviour."""
	end_date   = getdate(target_date) if target_date else getdate(today())
	start_date = add_days(end_date, -(days - 1))

	# Look one extra day past the window so an overnight shift starting on the
	# final scanned day can still find its next-morning OUT.
	range_start = get_datetime(f"{start_date} 00:00:00")
	range_end   = get_datetime(f"{add_days(end_date, 1)} 23:59:59")

	overnight_types = _overnight_shift_types()

	logs = frappe.db.get_all(
		"Employee Checkin",
		filters={
			"time":       ["between", [range_start, range_end]],
			"employee":   ["is", "set"],
			"attendance": ["in", ["", None]],
		},
		fields=["name", "employee", "time", "log_type"],
		order_by="employee asc, time asc",
	)

	# Group scans into shift windows keyed by (employee, working_date), where
	# working_date is the day the shift *opened*.
	#   - A scan at/after an overnight shift's start_time on day N opens that day's
	#     window (working_date = N).
	#   - An early-hours scan on day N+1 is the clock-out of the shift that opened
	#     the evening before, so it joins day N's window — but only if the employee
	#     actually had an overnight shift assigned on day N (the assignment may not
	#     extend to day N+1, e.g. a single-night assignment).
	#   - Day-shift and unassigned scans: window == the scan's own calendar day.
	windows = {}
	for log in logs:
		log_dt   = get_datetime(log.time)
		log_date = getdate(log_dt)
		working_date = log_date

		shift_today = _employee_overnight_shift_on(log.employee, log_date, overnight_types)
		if shift_today and get_time(log_dt) >= get_time(overnight_types[shift_today][0]):
			# evening (or later) scan of an overnight shift opening today:
			# working_date stays at today (the default set above).
			pass
		else:
			prev_date = add_days(log_date, -1)
			shift_prev = _employee_overnight_shift_on(log.employee, prev_date, overnight_types)
			# A scan in the morning portion (before the shift's own start_time)
			# belongs to yesterday's overnight shift — guards clock out whenever
			# their relief arrives, which can be well past the nominal end_time, so
			# we bound by start_time rather than end_time.
			if shift_prev and get_time(log_dt) < get_time(overnight_types[shift_prev][0]):
				working_date = prev_date
			else:
				working_date = log_date

		windows.setdefault((log.employee, str(working_date)), []).append(log)

	flipped = 0
	candidates = 0
	for (_employee, _working_date), scans in windows.items():
		in_scans = [s for s in scans if (s.log_type or "") == "IN"]
		if len(in_scans) < 2:
			continue
		candidates += 1
		# The trailing IN is the dangling clock-in; flip it to OUT. We pick the
		# last IN (not the last scan overall) so a window that already ends in an
		# OUT — e.g. [IN, IN, OUT] from a manual fix or an earlier run — still gets
		# its unpaired IN closed instead of no-op'ing on the already-OUT row.
		last_in = max(in_scans, key=lambda s: get_datetime(s.time))
		if _flip_to_out(last_in.name):
			flipped += 1

	frappe.db.commit()
	frappe.logger().info(
		f"auto_close_open_ins: scanned {start_date}..{end_date}, "
		f"flipped {flipped} IN→OUT across {candidates} (employee, working-day) windows "
		f"({len(overnight_types)} overnight shift type(s))"
	)
	return {
		"start_date":      str(start_date),
		"end_date":        str(end_date),
		"candidates":      candidates,
		"flipped":         flipped,
		"overnight_types": list(overnight_types.keys()),
	}
