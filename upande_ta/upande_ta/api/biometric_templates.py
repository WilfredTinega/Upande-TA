# Copyright (c) 2026, Upande Limited and contributors
# For license information, please see license.txt
"""Biometric Templates panel of Attendance Insights.

Four questions, one payload:

1. Enrolment: which template each active employee holds on each reader, and
   whether it is theirs alone. The md5 of every live face / palm / fingerprint
   blob is compared across the whole estate (not just the filtered company), so
   a template shared with somebody elsewhere is still named. A user id held by
   two employees is a clash as well.
2. Per reader: who is on each reader and whether the template there matches
   their other readers. Multi-signature is judged per modality: a face plus a
   palm is normal, two different faces is not.
3. Not enrolled: nowhere at all, or missing from some readers.
4. Anomalies over a date range: a punch on an off day, a punch while on
   approved leave, and an enrolled employee with no scan on a working day.

The off day is read from the holiday list in force on each date (the
employee's submitted Holiday List Assignments, their company's filling the
gaps, then Employee.holiday_list), so a later week-off transfer does not
re-judge history.

Wire format: readers are referenced by index into ``devices``; a per-reader
enrolment is ``[reader index, 10-char signature]``; an "enrolled but absent"
row is ``[employee, date, attendance status]`` because everything else about
it is on the employee's template row.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, getdate, nowdate

from upande_ta.upande_ta.api.attendance_insights import (
	company_where,
	enforce_company_access,
	get_allowed_companies,
)

ALLOWED_ROLES = ("System Manager", "HR Manager", "HR User")
MAX_DAYS = 92
SIG_LEN = 10

OFF_DAY = "Off day"
ON_LEAVE = "On leave"
ABSENT = "Enrolled but absent"

STATUS_RANK = {"Duplicate": 0, "Multi-signature": 1, "Not enrolled": 2, "Unique": 3}


@frappe.whitelist(methods=["GET"])
def biometric_templates(from_date=None, to_date=None, company=None, farm=None, employment_type=None):
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)
	if not set(ALLOWED_ROLES) & set(frappe.get_roles()):
		frappe.throw(
			_("Biometric enrolment data is limited to the System Manager, HR Manager and HR User roles."),
			frappe.PermissionError,
		)

	company = (company or "").strip() or None
	farm = (farm or "").strip() or None
	enforce_company_access(company)
	allowed = get_allowed_companies()

	start, end, span, clamped = clamp_window(from_date, to_date)
	unit = unit_field()
	scope_sql, scope_params = employee_scope(company, farm, employment_type, allowed, unit)
	unscoped = not (company or farm or scope_params.get("ets") or allowed)

	people = frappe.db.sql(
		f"""
		SELECT e.name, e.employee_name, e.status, e.company, e.department, e.designation,
			e.`{unit}` AS farm, e.employment_type, e.holiday_list, e.date_of_joining
		FROM `tabEmployee` e
		WHERE 1=1 {scope_sql}
		ORDER BY e.name
		""",
		scope_params,
		as_dict=True,
	)
	active = [p for p in people if p.status == "Active"]
	inactive = {p.name: p for p in people if p.status != "Active"}
	names = {
		r.name: r
		for r in frappe.db.sql("SELECT name, employee_name, status, company FROM `tabEmployee`", as_dict=True)
	}

	readers = frappe.db.sql(
		"SELECT name, device_location, device_sn FROM `tabBiometric Template` ORDER BY name", as_dict=True
	)
	beats = {
		d.device_sn: d
		for d in frappe.db.sql("SELECT device_sn, status, last_seen FROM `tabBiometric Device`", as_dict=True)
	}
	bio = frappe.db.sql(
		"""
		SELECT parent AS device, employee, employee_name, user_id, deleted,
			MD5(NULLIF(face_template, '')) AS face_sig,
			MD5(NULLIF(palm_template, '')) AS palm_sig,
			MD5(NULLIF(fingerprint_template, '')) AS fp_sig
		FROM `tabBio Template`
		ORDER BY employee, parent, idx
		""",
		as_dict=True,
	)

	visible = set(allowed) if allowed else None
	enrol = judge_enrolment(
		active,
		inactive,
		readers,
		bio,
		names,
		visible_companies=visible,
		include_unknown=unscoped,
	)
	for d in enrol["devices"]:
		beat = beats.get(d["sn"]) or {}
		d["status"] = beat.get("status") or ""
		d["last_seen"] = str(beat.get("last_seen") or "")

	scope_ids = [p.name for p in people]
	days = effective_off_days(people, start, end)
	leaves = approved_leaves(start, end, scope_sql, scope_params)
	marked = attendance_marks(start, end, scope_sql, scope_params)
	punches = punch_days(start, end, scope_sql, scope_params)

	anomalies = find_anomalies(
		people={p.name: p for p in people},
		enrolled=enrol["enrolled"],
		punches=punches,
		off_days=days,
		leaves=leaves,
		marked=marked,
		start=start,
		end=min(end, getdate(nowdate())),
	)
	punch_devices(anomalies["off_day"] + anomalies["on_leave"])

	summary = enrol["summary"]
	summary.update(
		{
			"template_rows": len(bio),
			"punch_days": len({str(p["pdate"]) for p in punches}),
			"punched_employees": len({p["employee"] for p in punches}),
			"off_day_punches": len(anomalies["off_day"]),
			"on_leave_punches": len(anomalies["on_leave"]),
			"enrolled_absent": len(anomalies["absent"]),
		}
	)

	if company:
		companies = [company]
	elif allowed:
		companies = sorted(allowed)
	else:
		companies = sorted({p.company for p in active if p.company})

	return {
		"from_date": str(start),
		"to_date": str(end),
		"days": span,
		"range_clamped": clamped,
		"max_days": MAX_DAYS,
		"generated_on": str(frappe.utils.now()),
		"company": company or "",
		"companies": companies,
		"farm": farm or "",
		"unit_field": unit,
		"employees_in_scope": len(scope_ids),
		"templates": enrol["templates"],
		"not_enrolled": enrol["not_enrolled"],
		"partly_enrolled": enrol["partly_enrolled"],
		"devices": enrol["devices"],
		"collisions": enrol["collisions"],
		"orphans": enrol["orphans"],
		"anomalies": anomalies,
		"summary": summary,
	}


# ─────────────────────────────────────────────────────────────── scope helpers


def clamp_window(from_date=None, to_date=None):
	"""(start, end, days, clamped): a missing end is today, a missing start is
	the end, a reversed pair is swapped and anything over MAX_DAYS keeps its
	end and loses its oldest days."""
	end = getdate(to_date or nowdate())
	start = getdate(from_date) if from_date else end
	if start > end:
		start, end = end, start
	span = date_diff(end, start) + 1
	clamped = 0
	if span > MAX_DAYS:
		start = getdate(add_days(end, 1 - MAX_DAYS))
		span = MAX_DAYS
		clamped = 1
	return start, end, span, clamped


def unit_field():
	return "custom_farm" if frappe.db.has_column("Employee", "custom_farm") else "branch"


def employee_scope(company, farm, employment_type, allowed, unit):
	"""(" AND ..." condition on alias ``e``, params) for the page's filters."""
	sql, params = "", {}
	cond, cparams = company_where(company, allowed, "e", "company")
	if cond:
		sql += " AND " + cond
		params.update(cparams)
	if farm:
		sql += f" AND TRIM(e.`{unit}`) = TRIM(%(farm)s)"
		params["farm"] = farm
	ets = tuple(t.strip() for t in (employment_type or "").split(",") if t.strip())
	if ets:
		sql += " AND e.employment_type IN %(ets)s"
		params["ets"] = ets
	return sql, params


# ─────────────────────────────────────────────────────────────────── enrolment


def signatures(row):
	"""[(modality, md5)] of the templates one Bio Template row carries."""
	out = []
	for kind, key in (("Face", "face_sig"), ("Palm", "palm_sig"), ("Fingerprint", "fp_sig")):
		sig = row.get(key)
		if sig:
			out.append((kind, sig))
	return out


def judge_enrolment(active, inactive, readers, bio, names, visible_companies=None, include_unknown=True):
	"""Uniqueness, multi-signature and reader coverage for ``active``.

	``bio`` is every Bio Template row on the estate; ``names`` maps every
	employee id to (employee_name, status, company). Only live (not deleted)
	rows count. ``visible_companies`` (None = all) limits whose names are
	disclosed for a collision outside the caller's companies.
	"""
	emap = {e["name"]: e for e in active}
	reader_ids = [r["name"] for r in readers]
	reader_index = {name: i for i, name in enumerate(reader_ids)}

	def name_of(eid):
		rec = emap.get(eid) or names.get(eid) or {}
		if visible_companies is not None and rec.get("company") not in visible_companies:
			return ""
		return rec.get("employee_name") or ""

	sig_owners, uid_owners, per_emp = {}, {}, {}
	orphans = []
	device_live, device_seats = {}, {}

	for r in bio:
		eid = (r.get("employee") or "").strip()
		dev = r.get("device") or ""
		dead = int(r.get("deleted") or 0)
		kinds = signatures(r)
		per_emp.setdefault(eid, []).append(
			{"device": dev, "user_id": (r.get("user_id") or "").strip(), "deleted": dead, "kinds": kinds}
		)
		if dead or not eid:
			continue

		device_live[dev] = device_live.get(dev, 0) + 1
		device_seats.setdefault(dev, set()).add(eid)

		if eid not in emap:
			gone = inactive.get(eid)
			if gone or (include_unknown and eid not in names):
				orphans.append(
					{
						"employee": eid,
						"employee_name": (gone or {}).get("employee_name") or r.get("employee_name") or "",
						"status": (gone or {}).get("status") or "Unknown",
						"reader": reader_index.get(dev, 0),
						"user_id": (r.get("user_id") or "").strip(),
					}
				)

		for _kind, sig in kinds:
			owners = sig_owners.setdefault(sig, [])
			if eid not in owners:
				owners.append(eid)
		uid = (r.get("user_id") or "").strip()
		if uid:
			owners = uid_owners.setdefault(uid, [])
			if eid not in owners:
				owners.append(eid)

	templates, collisions, seen_collision = [], [], set()
	not_enrolled, partly = [], []
	counts = {"Duplicate": 0, "Multi-signature": 0, "Not enrolled": 0, "Unique": 0}

	for e in active:
		eid = e["name"]
		rows = per_emp.get(eid) or []
		live = [r for r in rows if not r["deleted"]]

		sigs, kind_names, kind_sigs = [], [], {}
		shared, uid_clash, seats, on = [], [], [], set()
		uid = ""
		for r in live:
			on.add(r["device"])
			first = r["kinds"][0][1] if r["kinds"] else ""
			seats.append([reader_index.get(r["device"], 0), first[:SIG_LEN]])
			if r["user_id"]:
				uid = r["user_id"]
			for kind, sig in r["kinds"]:
				if sig not in sigs:
					sigs.append(sig)
				if kind not in kind_names:
					kind_names.append(kind)
				ks = kind_sigs.setdefault(kind, [])
				if sig not in ks:
					ks.append(sig)
				others = [o for o in sig_owners.get(sig, []) if o != eid]
				for o in others:
					if o not in shared:
						shared.append(o)
				if others and sig not in seen_collision:
					seen_collision.add(sig)
					collisions.append(
						{
							"signature": sig[:SIG_LEN],
							"kind": kind,
							"employees": [
								{"employee": w, "employee_name": name_of(w)} for w in sig_owners.get(sig, [])
							],
						}
					)
		if uid:
			uid_clash = [o for o in uid_owners.get(uid, []) if o != eid]

		varied = [k for k, v in kind_sigs.items() if len(v) > 1]
		gaps = [reader_index[d] for d in reader_ids if d not in on]

		if not live:
			status = "Not enrolled"
			not_enrolled.append(eid)
		elif shared or uid_clash:
			status = "Duplicate"
		elif varied:
			status = "Multi-signature"
		else:
			status = "Unique"
		counts[status] += 1
		if live and gaps:
			partly.append(eid)

		templates.append(
			{
				"e": eid,
				"n": e.get("employee_name") or "",
				"co": e.get("company") or "",
				"dept": e.get("department") or "",
				"farm": e.get("farm") or "",
				"desig": e.get("designation") or "",
				"emp_type": e.get("employment_type") or "",
				"joined": str(e.get("date_of_joining") or ""),
				"uid": uid,
				"kinds": kind_names,
				"vk": varied,
				"sig": sigs[0][:SIG_LEN] if sigs else "",
				"sc": len(sigs),
				"dc": len(live),
				"seats": seats,
				"gaps": gaps,
				"rm": len(rows) - len(live),
				"sw": [[o, name_of(o)] for o in shared],
				"su": [[o, name_of(o)] for o in uid_clash],
				"st": status,
			}
		)

	orphan_ids = {o["employee"] for o in orphans}
	devices = []
	for r in readers:
		seats = device_seats.get(r["name"], set())
		devices.append(
			{
				"device": r["name"],
				"location": r.get("device_location") or "",
				"sn": r.get("device_sn") or "",
				"enrolled": device_live.get(r["name"], 0),
				"active_enrolled": len(seats & emap.keys()),
				"inactive_enrolled": len(seats & orphan_ids),
				"not_enrolled": len(emap.keys() - seats),
			}
		)

	summary = {
		"employees": len(active),
		"enrolled": len(active) - counts["Not enrolled"],
		"not_enrolled": counts["Not enrolled"],
		"partly_enrolled": len(partly),
		"readers": len(readers),
		"unique": counts["Unique"],
		"duplicate": counts["Duplicate"],
		"multi_signature": counts["Multi-signature"],
		"collisions": len(collisions),
		"orphan_enrolments": len(orphans),
		"orphan_employees": len(orphan_ids),
	}
	return {
		"templates": templates,
		"enrolled": {t["e"] for t in templates if t["dc"]},
		"not_enrolled": not_enrolled,
		"partly_enrolled": partly,
		"devices": devices,
		"collisions": collisions,
		"orphans": orphans,
		"summary": summary,
	}


# ─────────────────────────────────────────────────────────────────── anomalies


def effective_off_days(people, start, end):
	"""{employee: {date: holiday row}} for the off days in ``start .. end``,
	judged by the holiday list in force on each date."""
	from hrms.utils.holiday_list import (
		fill_employee_holiday_list_date_gaps_with_company_holiday_list,
		get_assigned_holiday_lists_to_employee_and_company,
	)

	if not people:
		return {}
	companies = sorted({p.get("company") for p in people if p.get("company")})
	assigned = get_assigned_holiday_lists_to_employee_and_company(
		[p["name"] for p in people] + companies, start, end
	)

	ranges = {}
	for p in people:
		ranges[p["name"]] = fill_employee_holiday_list_date_gaps_with_company_holiday_list(
			assigned.get(p["name"], []), assigned.get(p.get("company"), []), start, end
		)

	lists = {r["holiday_list"] for rs in ranges.values() for r in rs}
	lists.update(p.get("holiday_list") for p in people if p.get("holiday_list"))
	holidays = {}
	if lists:
		for h in frappe.db.sql(
			"""
			SELECT parent, holiday_date, weekly_off, description FROM `tabHoliday`
			WHERE parent IN %(lists)s AND holiday_date BETWEEN %(start)s AND %(end)s
			""",
			{"lists": tuple(lists), "start": start, "end": end},
			as_dict=True,
		):
			holidays.setdefault(h.parent, {})[getdate(h.holiday_date)] = h

	return resolve_off_days(people, ranges, holidays, start, end)


def resolve_off_days(people, ranges, holidays, start, end):
	"""Pure part of effective_off_days: ``ranges`` = {employee: [{holiday_list,
	from_date, to_date}]}, ``holidays`` = {list: {date: row}}. A date no range
	covers falls back to Employee.holiday_list."""
	out = {}
	for p in people:
		eid = p["name"]
		spans = [
			(getdate(r["from_date"]), getdate(r["to_date"]), r["holiday_list"]) for r in ranges.get(eid) or []
		]
		fallback = p.get("holiday_list")
		mine = {}
		# holidays are sparse: test each candidate list's own dates rather
		# than every day of the window
		for hl in {s[2] for s in spans} | ({fallback} if fallback else set()):
			for day, h in (holidays.get(hl) or {}).items():
				if not start <= day <= end:
					continue
				in_force = next((s[2] for s in spans if s[0] <= day <= s[1]), fallback)
				if in_force == hl:
					mine[day] = dict(h, holiday_list=hl)
		if mine:
			out[eid] = mine
	return out


def approved_leaves(start, end, scope_sql, scope_params):
	rows = frappe.db.sql(
		f"""
		SELECT la.name, la.employee, la.leave_type, la.from_date, la.to_date, la.half_day
		FROM `tabLeave Application` la
		JOIN `tabEmployee` e ON e.name = la.employee
		WHERE la.docstatus = 1 AND la.status = 'Approved'
			AND la.from_date <= %(end)s AND la.to_date >= %(start)s {scope_sql}
		""",
		dict(scope_params, start=start, end=end),
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r.employee, []).append(r)
	return out


def attendance_marks(start, end, scope_sql, scope_params):
	# over a few weeks the optimiser prefers a full scan to the date index,
	# which is several times slower on a large Attendance table
	hint = " FORCE INDEX (attendance_date)" if frappe.db.has_index("tabAttendance", "attendance_date") else ""
	rows = frappe.db.sql(
		f"""
		SELECT a.employee, a.attendance_date, a.status
		FROM `tabAttendance` a{hint}
		JOIN `tabEmployee` e ON e.name = a.employee
		WHERE a.docstatus < 2 AND a.attendance_date BETWEEN %(start)s AND %(end)s {scope_sql}
		""",
		dict(scope_params, start=start, end=end),
		as_dict=True,
	)
	return {(r.employee, getdate(r.attendance_date)): r.status for r in rows}


def punch_days(start, end, scope_sql, scope_params):
	"""One row per employee-day. Served from the (time, employee) index; the
	reader ids are fetched separately, and only for the days that are
	reported (punch_devices), because reading them touches every row."""
	return frappe.db.sql(
		f"""
		SELECT c.employee, DATE(c.time) AS pdate, MIN(c.time) AS first_in, MAX(c.time) AS last_out,
			COUNT(*) AS punches
		FROM `tabEmployee Checkin` c
		JOIN `tabEmployee` e ON e.name = c.employee
		WHERE c.time >= %(start)s AND c.time < %(after)s {scope_sql}
		GROUP BY c.employee, DATE(c.time)
		""",
		dict(scope_params, start=start, after=add_days(end, 1)),
		as_dict=True,
	)


def punch_devices(rows):
	"""Fill ``dev`` on the anomaly rows with the readers punched that day."""
	if not rows:
		return
	by_day = {}
	for r in rows:
		by_day.setdefault(r["date"], set()).add(r["e"])
	found = {}
	# a long IN list tempts the optimiser onto the employee index, which reads
	# each person's whole history; the day's slice of (time, employee) is small
	hint = (
		" FORCE INDEX (checkin_time_employee)"
		if frappe.db.has_index("tabEmployee Checkin", "checkin_time_employee")
		else ""
	)
	# one day at a time, so only the reported employee-days are read
	for day, ids in by_day.items():
		for r in frappe.db.sql(
			f"""
			SELECT employee,
				GROUP_CONCAT(DISTINCT IFNULL(device_id, '') ORDER BY device_id SEPARATOR ', ') AS devices
			FROM `tabEmployee Checkin`{hint}
			WHERE time >= %(day)s AND time < %(after)s AND employee IN %(ids)s
			GROUP BY employee
			""",
			{"day": day, "after": add_days(day, 1), "ids": tuple(ids)},
			as_dict=True,
		):
			found[(r.employee, day)] = r.devices or ""
	for r in rows:
		r["dev"] = found.get((r["e"], r["date"]), "")


def find_anomalies(people, enrolled, punches, off_days, leaves, marked, start, end):
	"""Off-day punches, on-leave punches and enrolled-but-absent days.

	``people`` {id: employee} in scope (any status); ``enrolled`` ids with a
	live enrolment; ``punches`` rows of (employee, pdate, first_in, last_out,
	punches, devices); ``off_days`` {id: {date: holiday}}; ``leaves`` {id:
	[leave]}; ``marked`` {(id, date): attendance status}. Absent days are only
	judged up to ``end``.
	"""

	spans = {
		eid: [(getdate(lv["from_date"]), getdate(lv["to_date"]), lv) for lv in rows]
		for eid, rows in leaves.items()
	}

	def on_leave(eid, day):
		return [lv for a, b, lv in spans.get(eid) or () if a <= day <= b]

	off_rows, leave_rows, punched = [], [], set()
	for c in punches:
		eid = c["employee"]
		day = getdate(c["pdate"])
		punched.add((eid, day))
		hol = (off_days.get(eid) or {}).get(day)
		here = on_leave(eid, day)
		if not hol and not here:
			continue
		e = people.get(eid) or {}
		base = {
			"e": eid,
			"n": e.get("employee_name") or "",
			"date": str(day),
			"farm": e.get("farm") or "",
			"dept": e.get("department") or "",
			"in": str(c.get("first_in") or "")[11:16],
			"out": str(c.get("last_out") or "")[11:16],
			"p": int(c.get("punches") or 0),
			"dev": c.get("devices") or "",
			"att": marked.get((eid, day)) or "",
			"active": 1 if e.get("status") == "Active" else 0,
		}
		if hol:
			off_rows.append(
				dict(
					base,
					type=OFF_DAY,
					list=hol.get("holiday_list") or "",
					why="Week Off"
					if int(hol.get("weekly_off") or 0)
					else (hol.get("description") or "Holiday"),
				)
			)
		for lv in here:
			leave_rows.append(
				dict(
					base,
					type=ON_LEAVE,
					doc=lv["name"],
					why=(lv.get("leave_type") or "Leave")
					+ (" (half day)" if int(lv.get("half_day") or 0) else ""),
					lv_from=str(lv["from_date"]),
					lv_to=str(lv["to_date"]),
				)
			)

	absent = []
	roster = []
	for eid in sorted(enrolled):
		e = people.get(eid) or {}
		if e.get("status") == "Active":
			joined = e.get("date_of_joining")
			roster.append((eid, getdate(joined) if joined else None, off_days.get(eid) or {}))
	day, end, step = getdate(start), getdate(end), datetime.timedelta(days=1)
	while day <= end:
		iso = str(day)
		for eid, joined, off in roster:
			if (joined and joined > day) or (eid, day) in punched or day in off or on_leave(eid, day):
				continue
			absent.append([eid, iso, marked.get((eid, day)) or ""])
		day += step

	return {"off_day": off_rows, "on_leave": leave_rows, "absent": absent}
