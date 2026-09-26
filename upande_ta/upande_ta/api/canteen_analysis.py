# Copyright (c) 2026, Upande Limited and contributors
# For license information, please see license.txt
"""Backend for the Canteen Analysis page (/canteen-analysis).

Predicts how many employees the kitchen should cook for on a date: every
Active employee, less those who are on their off day, on sick off or on leave
that day. The same prediction is worked out for the days around the chosen one
and set beside what actually happened (Meal Checkins, Present attendance and
distinct biometric scanners), so the forecast can be judged against reality.

How each exclusion is decided
-----------------------------
* **Off day** -- the employee's holiday list as HRMS v16 resolves it: submitted
  Holiday List Assignments to the employee (latest from_date on or before the
  day), with the company's assignment filling any gap. ``Employee.holiday_list``
  is ignored, exactly as HRMS ignores it. A Holiday row with ``weekly_off = 1``
  is a weekly off; ``weekly_off = 0`` is a public holiday. Both are an off day.
* **Sick off** -- a submitted, Approved Leave Application whose leave type is a
  sick type (``Sick Leave``, ``Sick Leave (Full Pay)``, ``Sick Leave (Half Pay)``
  -- any Leave Type with "sick" in its name). The clinic sick-off flow
  (Clinic Checkin -> cova_clinic_integration) raises exactly these. A submitted
  "On Leave" Attendance with a sick leave type counts too.
* **Leave** -- the same, for every other leave type.

A half-day leave does not exclude anyone: the employee works the other half and
is on site around lunch.

One employee lands in one bucket only. The order is off day, then sick off,
then leave: a sick off or annual leave running across a rest day is still an
off day for that date (sick types carry include_holiday, so the leave covers
the rest day too, but the canteen question is the same either way).
"""

from itertools import pairwise

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, getdate, nowdate

from upande_ta.upande_ta.api.attendance_insights import (
	company_where,
	enforce_company_access,
	get_allowed_companies,
)

MEALS = ("Breakfast", "Lunch", "Supper")
MAX_HISTORY = 31
MAX_AHEAD = 14

OFF_DAY = "off_day"
SICK = "sick"
LEAVE = "leave"


def is_sick_leave_type(leave_type):
	return "sick" in (leave_type or "").lower()


def classify(off_day, leave_type):
	"""The one bucket an employee falls in for a day (None = eats)."""
	if off_day:
		return OFF_DAY
	if leave_type:
		return SICK if is_sick_leave_type(leave_type) else LEAVE
	return None


# ─────────────────────────────────────────────────────────────────────────────
# data loaders
# ─────────────────────────────────────────────────────────────────────────────


def _employees(company, farm, allowed, start, end):
	"""Active employees in scope who are employed at some point in start..end."""
	conds = ["e.status = 'Active'"]
	params = {"start": start, "end": end}
	cond, cparams = company_where(company, allowed, "e", "company")
	if cond:
		conds.append(cond)
		params.update(cparams)
	if farm:
		conds.append("e.custom_farm = %(farm)s")
		params["farm"] = farm
	conds.append("(e.date_of_joining IS NULL OR e.date_of_joining <= %(end)s)")
	conds.append("(e.relieving_date IS NULL OR e.relieving_date >= %(start)s)")
	return frappe.db.sql(
		f"""
		SELECT e.name AS employee, e.employee_name, e.company,
			IFNULL(e.custom_farm, '') AS farm, e.designation, e.department,
			e.date_of_joining, e.relieving_date
		FROM `tabEmployee` e
		WHERE {" AND ".join(conds)}
		ORDER BY e.company, e.custom_farm, e.employee_name
		""",
		params,
		as_dict=True,
	)


def _off_days(employees, start, end):
	"""{employee: {date: description}} for every off day in start..end."""
	from upande_ta.upande_ta.holiday_ranges import (
		fill_employee_holiday_list_date_gaps_with_company_holiday_list,
		get_assigned_holiday_lists_to_employee_and_company,
	)

	if not employees:
		return {}
	companies = sorted({e.company for e in employees})
	assigned = get_assigned_holiday_lists_to_employee_and_company(
		[e.employee for e in employees] + companies, start, end
	)

	ranges_by_emp = {}
	lists = set()
	for e in employees:
		ranges = fill_employee_holiday_list_date_gaps_with_company_holiday_list(
			assigned.get(e.employee, []), assigned.get(e.company, []), start, end
		)
		ranges_by_emp[e.employee] = ranges
		lists.update(r["holiday_list"] for r in ranges)

	holidays = {}
	if lists:
		for h in frappe.get_all(
			"Holiday",
			filters={"parent": ["in", list(lists)], "holiday_date": ["between", [start, end]]},
			fields=["parent", "holiday_date", "weekly_off", "description"],
			limit_page_length=0,
		):
			holidays.setdefault(h.parent, {})[getdate(h.holiday_date)] = (
				"Weekly Off" if h.weekly_off else (h.description or "Public Holiday")
			)

	out = {}
	for emp, ranges in ranges_by_emp.items():
		days = {}
		for r in ranges:
			hl_days = holidays.get(r["holiday_list"])
			if not hl_days:
				continue
			lo, hi = getdate(r["from_date"]), getdate(r["to_date"])
			for d, desc in hl_days.items():
				if lo <= d <= hi:
					days[d] = desc
		if days:
			out[emp] = days
	return out


def _leaves(employee_ids, start, end):
	"""{employee: {date: {leave_type, name, from_date, to_date}}} of full-day
	approved leave in start..end, from Leave Applications and On Leave
	attendance."""
	out = {}
	if not employee_ids:
		return out

	for la in frappe.db.sql(
		"""
		SELECT name, employee, leave_type, from_date, to_date, half_day, half_day_date
		FROM `tabLeave Application`
		WHERE docstatus = 1 AND status = 'Approved'
			AND from_date <= %(end)s AND to_date >= %(start)s
			AND employee IN %(emps)s
		ORDER BY from_date
		""",
		{"start": start, "end": end, "emps": tuple(employee_ids)},
		as_dict=True,
	):
		d = max(getdate(la.from_date), start)
		last = min(getdate(la.to_date), end)
		half = getdate(la.half_day_date) if la.half_day and la.half_day_date else None
		if la.half_day and not half and la.from_date == la.to_date:
			half = getdate(la.from_date)
		while d <= last:
			if d != half:
				out.setdefault(la.employee, {})[d] = {
					"leave_type": la.leave_type,
					"name": la.name,
					"from_date": la.from_date,
					"to_date": la.to_date,
				}
			d = add_days(d, 1)

	# On Leave attendance with no application behind it (imports, manual marks)
	for a in frappe.db.sql(
		"""
		SELECT name, employee, attendance_date, leave_type, leave_application
		FROM `tabAttendance`
		WHERE docstatus = 1 AND status = 'On Leave'
			AND attendance_date BETWEEN %(start)s AND %(end)s
			AND employee IN %(emps)s
		""",
		{"start": start, "end": end, "emps": tuple(employee_ids)},
		as_dict=True,
	):
		d = getdate(a.attendance_date)
		days = out.setdefault(a.employee, {})
		if d not in days:
			days[d] = {
				"leave_type": a.leave_type or "Leave",
				"name": a.leave_application or a.name,
				"from_date": d,
				"to_date": d,
			}
	return out


def _count_by_day(sql, params):
	return {getdate(r[0]): cint(r[1]) for r in frappe.db.sql(sql, params)}


def _actuals(scope_sql, scope_params, start, end, meal):
	"""Per-day counts of what really happened, in the same employee scope."""
	params = dict(scope_params, start=start, end=end, end_excl=add_days(end, 1), meal=meal)
	meals = _count_by_day(
		f"""
		SELECT DATE(m.time), COUNT(DISTINCT m.employee)
		FROM `tabMeal Checkin` m JOIN `tabEmployee` e ON e.name = m.employee
		WHERE m.docstatus < 2 AND m.log_type = %(meal)s
			AND m.time >= %(start)s AND m.time < %(end_excl)s {scope_sql}
		GROUP BY DATE(m.time)
		""",
		params,
	)
	present = _count_by_day(
		f"""
		SELECT a.attendance_date, COUNT(DISTINCT a.employee)
		FROM `tabAttendance` a JOIN `tabEmployee` e ON e.name = a.employee
		WHERE a.docstatus = 1 AND a.status IN ('Present', 'Half Day', 'Work From Home')
			AND a.attendance_date BETWEEN %(start)s AND %(end)s {scope_sql}
		GROUP BY a.attendance_date
		""",
		params,
	)
	return meals, present


# ─────────────────────────────────────────────────────────────────────────────
# forecast
# ─────────────────────────────────────────────────────────────────────────────


def build_forecast(on_date, company="", farm="", meal="Lunch", history_days=14, ahead_days=6):
	on_date = getdate(on_date or nowdate())
	history_days = max(0, min(cint(history_days), MAX_HISTORY))
	ahead_days = max(0, min(cint(ahead_days), MAX_AHEAD))
	meal = meal if meal in MEALS else "Lunch"
	start = add_days(on_date, -history_days)
	end = add_days(on_date, ahead_days)

	enforce_company_access(company)
	allowed = get_allowed_companies()

	# each company's payroll window around the date; data is loaded over the
	# union of those and the +/- days window
	from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import payroll_window_for

	windows = {co: payroll_window_for(co, on_date) for co in _scope_companies(company, allowed)}
	load_start = min([start] + [w[0] for w in windows.values()])
	load_end = max([end] + [w[1] for w in windows.values()])

	employees = _employees(company, farm, allowed, load_start, load_end)
	ids = [e.employee for e in employees]
	off = _off_days(employees, load_start, load_end)
	leave = _leaves(ids, load_start, load_end)

	def employed(e, d):
		return (not e.date_of_joining or getdate(e.date_of_joining) <= d) and (
			not e.relieving_date or getdate(e.relieving_date) >= d
		)

	# per-day series
	days = []
	d = start
	while d <= end:
		row = {"date": str(d), "active": 0, "expected": 0, OFF_DAY: 0, SICK: 0, LEAVE: 0}
		exp_by_co = {}
		for e in employees:
			if not employed(e, d):
				continue
			row["active"] += 1
			lv = leave.get(e.employee, {}).get(d)
			bucket = classify(off.get(e.employee, {}).get(d), lv and lv["leave_type"])
			if bucket:
				row[bucket] += 1
			else:
				row["expected"] += 1
				exp_by_co[e.company] = exp_by_co.get(e.company, 0) + 1
		row["_exp_by_co"] = exp_by_co
		days.append(row)
		d = add_days(d, 1)

	scope_sql = ""
	scope_params = {}
	cond, cparams = company_where(company, allowed, "e", "company")
	if cond:
		scope_sql += " AND " + cond
		scope_params.update(cparams)
	if farm:
		scope_sql += " AND e.custom_farm = %(farm)s"
		scope_params["farm"] = farm
	last_past = min(end, getdate(nowdate()))
	meals, present = (
		_actuals(scope_sql, scope_params, start, last_past, meal) if last_past >= start else ({}, {})
	)
	scans = _scan_sets(ids, start, last_past) if last_past >= start else {}
	by_id = {e.employee: e for e in employees}
	costs = daily_costs(load_start, load_end)
	eaten_by_co = {}
	if last_past >= start:
		for d, co, n in frappe.db.sql(
			f"""
			SELECT DATE(m.time), e.company, COUNT(DISTINCT m.employee)
			FROM `tabMeal Checkin` m JOIN `tabEmployee` e ON e.name = m.employee
			WHERE m.docstatus < 2 AND m.log_type = %(meal)s
				AND m.time >= %(start)s AND m.time < %(end_excl)s {scope_sql}
			GROUP BY DATE(m.time), e.company
			""",
			dict(scope_params, meal=meal, start=start, end_excl=add_days(last_past, 1)),
		):
			eaten_by_co.setdefault(getdate(d), {})[co] = cint(n)
	for row in days:
		dd = getdate(row["date"])
		row.update(
			cost_of_day(
				costs,
				dd,
				meal,
				row.pop("_exp_by_co"),
				None if dd > getdate(nowdate()) else eaten_by_co.get(dd, {}),
			)
		)
		row["menu"] = day_menu(costs, dd, company)
		dd = getdate(row["date"])
		future = dd > getdate(nowdate())
		row["meals"] = None if future else meals.get(dd, 0)
		row["present"] = None if future else present.get(dd, 0)
		# scanned: the Expected population (active, employed that day, in scope)
		# with at least one Employee Checkin that day
		row["punched"] = None if future else sum(1 for emp in scans.get(dd, ()) if employed(by_id[emp], dd))

	# the chosen day: groups and the excluded employees
	groups = {}
	excluded = {OFF_DAY: [], SICK: [], LEAVE: []}
	for e in employees:
		if not employed(e, on_date):
			continue
		g = groups.setdefault(
			(e.company, e.farm),
			{"company": e.company, "farm": e.farm, "active": 0, "expected": 0, OFF_DAY: 0, SICK: 0, LEAVE: 0},
		)
		g["active"] += 1
		off_desc = off.get(e.employee, {}).get(on_date)
		lv = leave.get(e.employee, {}).get(on_date)
		bucket = classify(off_desc, lv and lv["leave_type"])
		if not bucket:
			g["expected"] += 1
			continue
		g[bucket] += 1
		rec = {
			"employee": e.employee,
			"employee_name": e.employee_name,
			"company": e.company,
			"farm": e.farm,
			"designation": e.designation,
			"department": e.department,
		}
		if bucket == OFF_DAY:
			rec["detail"] = off_desc
		else:
			rec.update(
				detail=lv["leave_type"],
				reference=lv["name"],
				from_date=str(lv["from_date"]),
				to_date=str(lv["to_date"]),
			)
		excluded[bucket].append(rec)

	# actual meals on the chosen day, per group
	if on_date <= getdate(nowdate()):
		for company_, farm_, n in frappe.db.sql(
			f"""
			SELECT e.company, IFNULL(e.custom_farm, ''), COUNT(DISTINCT m.employee)
			FROM `tabMeal Checkin` m JOIN `tabEmployee` e ON e.name = m.employee
			WHERE m.docstatus < 2 AND m.log_type = %(meal)s
				AND m.time >= %(d)s AND m.time < %(d1)s {scope_sql}
			GROUP BY e.company, e.custom_farm
			""",
			dict(scope_params, meal=meal, d=on_date, d1=add_days(on_date, 1)),
		):
			if (company_, farm_) in groups:
				groups[(company_, farm_)]["meals"] = cint(n)
		for g in groups.values():
			g.setdefault("meals", 0)

	summary = next((r for r in days if r["date"] == str(on_date)), None)
	return {
		"period": _period_forecast(
			employees, windows, off, leave, employed, scope_sql, scope_params, meal, costs
		),
		"date": str(on_date),
		"meal": meal,
		"summary": summary,
		"groups": sorted(groups.values(), key=lambda g: (g["company"], g["farm"])),
		"excluded": excluded,
		"days": days,
	}


def distinct_totals(day_sets):
	"""Totals over several days of per-day sets of employees: ``days`` is the
	person-days (sum of each day's count), ``people`` the distinct employees
	across the whole range."""
	seen = set()
	days = 0
	for s in day_sets:
		days += len(s)
		seen.update(s)
	return {"days": days, "people": len(seen)}


def _scan_sets(employee_ids, start, end):
	"""{date: {employee}} of the given employees with at least one Employee
	Checkin on that date, start..end inclusive."""
	out = {}
	if not employee_ids:
		return out
	for d, emp in frappe.db.sql(
		"""
		SELECT DISTINCT DATE(time), employee FROM `tabEmployee Checkin`
		WHERE time >= %(start)s AND time < %(end_excl)s AND employee IN %(emps)s
		""",
		{"start": start, "end_excl": add_days(end, 1), "emps": tuple(employee_ids)},
	):
		out.setdefault(getdate(d), set()).add(emp)
	return out


PRESENT_STATUSES = ("Present", "Half Day", "Work From Home")


def cost_of_day(costs, day, meal, expected_by_company, eaten_by_company):
	"""Forecast and actual cost of `meal` on `day`: expected (and eaten, None
	for a day not yet over) heads per company times that company's cost for the
	day. Heads with no cost record are left out of the money and counted as
	unpriced."""
	out = {"forecast_cost": 0.0, "forecast_unpriced": 0, "actual_cost": None, "actual_unpriced": 0}
	for co, n in (expected_by_company or {}).items():
		rate = rate_for(costs, day, co, meal)
		if rate:
			out["forecast_cost"] += n * rate["cost"]
		else:
			out["forecast_unpriced"] += n
	if eaten_by_company is not None:
		out["actual_cost"] = 0.0
		for co, n in eaten_by_company.items():
			rate = rate_for(costs, day, co, meal)
			if rate:
				out["actual_cost"] += n * rate["cost"]
			else:
				out["actual_unpriced"] += n
	return out


def _period_forecast(employees, windows, off, leave, employed, scope_sql, scope_params, meal, costs):
	"""Expected vs ate `meal` for every day of the payroll period, each
	employee counted only inside their own company's window, plus one row per
	employee for the period. Days after today carry the forecast alone.
	Totals of people are distinct people; totals of days say so."""
	in_scope = {e.company for e in employees}
	windows = {co: w for co, w in windows.items() if co in in_scope}
	empty_totals = {
		"expected": 0,
		"ate": 0,
		"expected_to_date": 0,
		"ate_employees": 0,
		"scanned_employees": 0,
		"scanned_days": 0,
		"present_employees": 0,
		"meals": 0,
		"cost": 0.0,
		"unpriced_meals": 0,
		"forecast_cost": 0.0,
		"forecast_cost_to_date": 0.0,
		"actual_cost": 0.0,
		"cost_unpriced_days": 0,
	}
	if not windows:
		return {"windows": [], "days": [], "totals": empty_totals, "employees": [], "priced": False}
	start = min(w[0] for w in windows.values())
	end = max(w[1] for w in windows.values())
	today = getdate(nowdate())
	last = min(end, today)
	by_id = {e.employee: e for e in employees}
	menu_company = next(iter(windows)) if len(windows) == 1 else ""
	emp_cost = {}  # {employee: {"cost", "unpriced"}}

	def in_window(emp, d):
		w = windows.get(by_id[emp].company) if emp in by_id else None
		return bool(w) and w[0] <= d <= w[1]

	ate = {}  # {date: {employee}} for `meal`
	meal_counts = {}  # {employee: {meal type: meals}}
	present = {}  # {employee: days present}
	scans = {}
	if last >= start and by_id:
		params = {"start": start, "end_excl": add_days(last, 1), "end": last, "emps": tuple(by_id)}
		for d, emp, mtype in frappe.db.sql(
			"""
			SELECT DISTINCT DATE(time), employee, log_type FROM `tabMeal Checkin`
			WHERE docstatus < 2 AND time >= %(start)s AND time < %(end_excl)s AND employee IN %(emps)s
			""",
			params,
		):
			d = getdate(d)
			if not in_window(emp, d):
				continue
			counts = meal_counts.setdefault(emp, {})
			counts[mtype or ""] = counts.get(mtype or "", 0) + 1
			ec = emp_cost.setdefault(emp, {"cost": 0.0, "unpriced": 0})
			rate = rate_for(costs, d, by_id[emp].company, mtype)
			if rate:
				ec["cost"] += rate["cost"]
			else:
				ec["unpriced"] += 1
			if mtype == meal:
				ate.setdefault(d, set()).add(emp)
		for d, emp in frappe.db.sql(
			"""
			SELECT attendance_date, employee FROM `tabAttendance`
			WHERE docstatus = 1 AND status IN %(statuses)s
				AND attendance_date BETWEEN %(start)s AND %(end)s AND employee IN %(emps)s
			""",
			dict(params, statuses=PRESENT_STATUSES),
		):
			if in_window(emp, getdate(d)):
				present[emp] = present.get(emp, 0) + 1
		for d, emps in _scan_sets(list(by_id), start, last).items():
			scans[d] = {emp for emp in emps if in_window(emp, d) and employed(by_id[emp], d)}

	per = {}
	days = []
	totals = dict(empty_totals)
	d = start
	by_company = {}
	while d <= end:
		expected = 0
		exp_by_co = {}
		future = d > today
		eaters = ate.get(d, ())
		for e in employees:
			if not in_window(e.employee, d) or not employed(e, d):
				continue
			row = per.setdefault(e.employee, {"expected": 0, "missed": 0, "eaten": 0})
			if not future and e.employee in eaters:
				row["eaten"] += 1
			lv = leave.get(e.employee, {}).get(d)
			if classify(off.get(e.employee, {}).get(d), lv and lv["leave_type"]):
				continue
			expected += 1
			exp_by_co[e.company] = exp_by_co.get(e.company, 0) + 1
			if not future:
				row["expected"] += 1
				if d < today and e.employee not in eaters:
					row["missed"] += 1
		row = {
			"date": str(d),
			"expected": expected,
			"ate": None if future else len(eaters),
			"scanned": None if future else len(scans.get(d, ())),
			"menu": day_menu(costs, d, menu_company),
			"future": future,
		}
		eaten_by_co = None
		if not future:
			eaten_by_co = {}
			for emp in eaters:
				co = by_id[emp].company
				eaten_by_co[co] = eaten_by_co.get(co, 0) + 1
		row.update(cost_of_day(costs, d, meal, exp_by_co, eaten_by_co))
		for co in set(exp_by_co) | set(eaten_by_co or ()):
			bc = by_company.setdefault(
				co, {"forecast_cost": 0.0, "forecast_cost_to_date": 0.0, "actual_cost": 0.0}
			)
			part = cost_of_day(
				costs,
				d,
				meal,
				{co: exp_by_co.get(co, 0)},
				None if future else {co: (eaten_by_co or {}).get(co, 0)},
			)
			bc["forecast_cost"] += part["forecast_cost"]
			if not future:
				bc["forecast_cost_to_date"] += part["forecast_cost"]
				bc["actual_cost"] += part["actual_cost"]
		totals["forecast_cost"] += row["forecast_cost"]
		if row["forecast_unpriced"] or row["actual_unpriced"]:
			totals["cost_unpriced_days"] += 1
		if not future:
			totals["forecast_cost_to_date"] += row["forecast_cost"]
			totals["actual_cost"] += row["actual_cost"]
		totals["expected"] += expected
		if not future:
			totals["expected_to_date"] += expected
			totals["ate"] += row["ate"]
		days.append(row)
		d = add_days(d, 1)

	rows = []
	for emp, r in per.items():
		e = by_id[emp]
		counts = meal_counts.get(emp, {})
		ec = emp_cost.get(emp) or {"cost": 0.0, "unpriced": 0}
		cost = ec["cost"]
		totals["meals"] += sum(counts.values())
		totals["cost"] += cost
		totals["unpriced_meals"] += ec["unpriced"]
		rows.append(
			{
				"employee": emp,
				"employee_name": e.employee_name,
				"company": e.company,
				"farm": e.farm,
				"designation": e.designation,
				"expected": r["expected"],
				"eaten": r["eaten"],
				"missed": r["missed"],
				"present": present.get(emp, 0),
				"meals": counts,
				"meals_total": sum(counts.values()),
				"cost": cost,
				"unpriced": ec["unpriced"],
			}
		)
	rows.sort(key=lambda r: (-r["missed"], r["employee_name"] or ""))

	eaters_all = distinct_totals(ate.values())
	scanners = distinct_totals(scans.values())
	totals.update(
		ate_employees=eaters_all["people"],
		scanned_employees=scanners["people"],
		scanned_days=scanners["days"],
		present_employees=sum(1 for n in present.values() if n),
	)
	return {
		"windows": [
			{"company": co, "start": str(w[0]), "end": str(w[1])} for co, w in sorted(windows.items())
		],
		"days": days,
		"totals": totals,
		"employees": rows,
		"by_company": by_company,
		"priced": bool(costs),
	}


# ─────────────────────────────────────────────────────────────────────────────
# endpoints
# ─────────────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def canteen_forecast(date=None, company=None, farm=None, meal="Lunch", history_days=14, ahead_days=6):
	"""Expected diners for `date`, by company and unit, with the excluded
	employees and the predicted-vs-actual series around it."""
	return build_forecast(
		date or nowdate(),
		(company or "").strip(),
		(farm or "").strip(),
		meal or "Lunch",
		history_days,
		ahead_days,
	)


@frappe.whitelist()
def canteen_filters():
	"""Companies the caller may see, with the units under each."""
	allowed = get_allowed_companies()
	cond, params = company_where("", allowed, "e", "company")
	rows = frappe.db.sql(
		f"""
		SELECT e.company, IFNULL(e.custom_farm, '') AS farm, COUNT(*) AS n
		FROM `tabEmployee` e
		WHERE e.status = 'Active' {("AND " + cond) if cond else ""}
		GROUP BY e.company, e.custom_farm
		ORDER BY e.company, e.custom_farm
		""",
		params,
		as_dict=True,
	)
	companies = {}
	for r in rows:
		companies.setdefault(r.company, []).append({"farm": r.farm, "active": cint(r.n)})
	return {
		"companies": [{"company": c, "farms": f} for c, f in companies.items()],
		"today": nowdate(),
		"meals": list(MEALS),
	}


# ─────────────────────────────────────────────────────────────────────────────
# meals eaten: the chosen day's punches and the payroll period's totals + cost
# ─────────────────────────────────────────────────────────────────────────────
#
# A punch is one Meal Checkin row. A meal is one employee, one meal type, one
# day: a second punch for the same lunch is shown (and flagged) but not counted
# or costed twice. The period is the payroll window from Biometric Setting ->
# Payroll Dates that contains the chosen day, per company, so companies with
# different cycles (e.g. 20th-19th and 21st-20th) each total over their own window.
# A meal's cost is the Daily Meal Cost for its exact date: the company's record
# first, else the blank-company one. No record for that meal that day leaves the
# meal unpriced (shown as such, never guessed from another day).


def daily_costs(start, end):
	"""{(date, company or "", meal type): {"cost", "menu"}} from Daily Meal Cost
	for start..end, in one query."""
	if not frappe.db.table_exists("Daily Meal Cost Item"):
		return {}
	out = {}
	for d, co, meal, cost, menu in frappe.db.sql(
		"""
		SELECT p.date, IFNULL(p.company, ''), i.meal_type, i.cost_per_meal, i.menu
		FROM `tabDaily Meal Cost` p
		JOIN `tabDaily Meal Cost Item` i ON i.parent = p.name AND i.parenttype = 'Daily Meal Cost'
		WHERE p.date BETWEEN %(start)s AND %(end)s
		""",
		{"start": start, "end": end},
	):
		out[(getdate(d), co, meal)] = {"cost": flt(cost), "menu": menu or ""}
	return out


def rate_for(costs, day, company, meal):
	"""The cost row for `meal` on exactly `day`: the company's own record, else
	the blank-company record. None means the meal is unpriced -- a day's price
	never carries over to another day, every day is a different dish."""
	day = getdate(day)
	return costs.get((day, company or "", meal)) or costs.get((day, "", meal))


def day_menu(costs, day, company):
	"""{meal type: menu} served on `day`."""
	out = {}
	for m in MEALS:
		rate = rate_for(costs, day, company, m)
		if rate:
			out[m] = rate["menu"]
	return out


def _scope_companies(company, allowed):
	if company:
		return [company]
	names = frappe.get_all("Company", pluck="name", order_by="name asc")
	return [c for c in names if allowed is None or c in allowed]


def _meal_rows(company, farm, start, end):
	"""Meal Checkin rows of `company`'s employees (optionally one unit), with
	the employee's details, for start..end inclusive."""
	params = {"company": company, "start": start, "end_excl": add_days(end, 1)}
	farm_sql = ""
	if farm:
		farm_sql = " AND e.custom_farm = %(farm)s"
		params["farm"] = farm
	return frappe.db.sql(
		f"""
		SELECT m.name, m.employee, e.employee_name, e.company, IFNULL(e.custom_farm, '') AS farm,
			m.payroll_number, m.log_type AS meal, m.time
		FROM `tabMeal Checkin` m JOIN `tabEmployee` e ON e.name = m.employee
		WHERE m.docstatus < 2 AND m.time >= %(start)s AND m.time < %(end_excl)s
			AND e.company = %(company)s {farm_sql}
		ORDER BY m.time
		""",
		params,
		as_dict=True,
	)


def build_meals(on_date, company="", farm=""):
	from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import payroll_window_for

	on_date = getdate(on_date or nowdate())
	enforce_company_access(company)
	allowed = get_allowed_companies()
	wins = {co: payroll_window_for(co, on_date) for co in _scope_companies(company, allowed)}
	costs = daily_costs(min(w[0] for w in wins.values()), max(w[1] for w in wins.values())) if wins else {}

	punches = []
	day_meals = {m: {"meal": m, "eaten": 0, "punches": 0, "repeats": 0} for m in MEALS}
	day_seen = {}
	day_employees = set()

	periods = []
	series = {}
	by_meal = []
	tot = {"meals": 0, "punches": 0, "employees": 0, "cost": 0.0, "unpriced_meals": 0, "unpriced_days": 0}

	for co, (start, end) in wins.items():
		# a checkin dated after today has not happened yet: the period runs to its
		# end, the actuals only to today
		rows = _meal_rows(co, farm, start, min(end, getdate(nowdate())))
		# a company with no meals yet still shows its period, as long as it has
		# staff in scope; companies with neither are left out of "All Companies"
		if not rows and co != company:
			scope = {"company": co, "status": "Active"}
			if farm:
				scope["custom_farm"] = farm
			if not frappe.db.exists("Employee", scope):
				continue
		periods.append({"company": co, "start": str(start), "end": str(end)})

		seen = set()
		employees = set()
		per_meal = {}
		for r in rows:
			d = getdate(r.time)
			key = (r.employee, d, r.meal)
			day = series.setdefault(
				str(d),
				{
					"date": str(d),
					"meals": 0,
					"punches": 0,
					"employees": set(),
					"cost": 0.0,
					"unpriced": 0,
					"by_meal": {},
				},
			)
			day["punches"] += 1
			tot["punches"] += 1
			pm = per_meal.setdefault(r.meal or "", {"meals": 0, "punches": 0, "cost": 0.0, "unpriced": 0})
			pm["punches"] += 1
			employees.add(r.employee)
			day["employees"].add(r.employee)
			if key not in seen:
				seen.add(key)
				pm["meals"] += 1
				day["meals"] += 1
				day["by_meal"][r.meal or ""] = day["by_meal"].get(r.meal or "", 0) + 1
				rate = rate_for(costs, d, co, r.meal)
				if rate:
					day["cost"] += rate["cost"]
					pm["cost"] += rate["cost"]
				else:
					day["unpriced"] += 1
					pm["unpriced"] += 1

			if d == on_date:
				n = day_seen.get(key, 0) + 1
				day_seen[key] = n
				dm = day_meals.setdefault(
					r.meal or "", {"meal": r.meal or "", "eaten": 0, "punches": 0, "repeats": 0}
				)
				dm["punches"] += 1
				if n == 1:
					dm["eaten"] += 1
				elif n == 2:
					dm["repeats"] += 1
				day_employees.add(r.employee)
				punches.append(
					{
						"name": r.name,
						"employee": r.employee,
						"employee_name": r.employee_name,
						"company": r.company,
						"farm": r.farm,
						"payroll_number": r.payroll_number,
						"meal": r.meal,
						"time": str(r.time),
					}
				)

		tot["employees"] += len(employees)
		for meal, pm in sorted(per_meal.items(), key=lambda kv: MEALS.index(kv[0]) if kv[0] in MEALS else 9):
			priced = pm["meals"] - pm["unpriced"]
			tot["meals"] += pm["meals"]
			tot["unpriced_meals"] += pm["unpriced"]
			tot["cost"] += pm["cost"]
			by_meal.append(
				{
					"company": co,
					"meal": meal,
					"meals": pm["meals"],
					"punches": pm["punches"],
					# the price changes daily, so this is the average of the priced meals
					"cost_per_meal": pm["cost"] / priced if priced else None,
					"cost": pm["cost"] if priced else None,
					"unpriced": pm["unpriced"],
				}
			)

	# how many punches each employee made for that meal on the day
	for p in punches:
		p["count"] = day_seen[(p["employee"], on_date, p["meal"])]

	days = []
	if periods:
		d = min(getdate(p["start"]) for p in periods)
		last = max(getdate(p["end"]) for p in periods)
		while d <= last:
			row = series.get(str(d)) or {
				"date": str(d),
				"meals": 0,
				"punches": 0,
				"employees": set(),
				"cost": 0.0,
				"unpriced": 0,
				"by_meal": {},
			}
			row["employees"] = len(row["employees"])
			row["future"] = d > getdate(nowdate())
			row["menu"] = day_menu(costs, d, company)
			if row["unpriced"]:
				tot["unpriced_days"] += 1
			days.append(row)
			d = add_days(d, 1)

	day_rows = [day_meals[m] for m in day_meals if day_meals[m]["punches"] or m in MEALS]
	return {
		"date": str(on_date),
		"currency": frappe.db.get_default("currency") or "",
		"day": {
			"eaten": len({(k[0], k[2]) for k in day_seen}),
			"employees": len(day_employees),
			"punches": len(punches),
			"repeats": sum(1 for n in day_seen.values() if n > 1),
			"by_meal": day_rows,
		},
		"punches": punches,
		"by_employee": punches_by_employee(punches),
		"period": {
			"windows": periods,
			"totals": tot,
			"by_meal": by_meal,
			"days": days,
			"priced": bool(costs),
		},
	}


@frappe.whitelist()
def canteen_meals(date=None, company=None, farm=None):
	"""Meals eaten on `date` (every punch, with its time) and over the payroll
	period containing it, with the estimated cost."""
	return build_meals(date or nowdate(), (company or "").strip(), (farm or "").strip())


# ─────────────────────────────────────────────────────────────────────────────
# per employee: punch sequence on a day, and lunch over the payroll period
# ─────────────────────────────────────────────────────────────────────────────

PUNCHES_SHOWN = 4


def punch_sequence(times, keep=PUNCHES_SHOWN):
	"""The first `keep` punches of a day, the seconds between each consecutive
	pair of them, and how many more there were."""
	times = sorted(get_datetime(t) for t in times)
	shown = times[:keep]
	return {
		"count": len(times),
		"times": [str(t) for t in shown],
		"intervals": [int((b - a).total_seconds()) for a, b in pairwise(shown)],
		"more": max(0, len(times) - keep),
	}


def punches_by_employee(punches):
	"""One row per employee from a day's punch rows, ordered by first punch."""
	groups = {}
	for p in punches:
		g = groups.setdefault(
			p["employee"],
			{
				"employee": p["employee"],
				"employee_name": p["employee_name"],
				"company": p["company"],
				"farm": p["farm"],
				"meals": [],
				"times": [],
				"rows": [],
			},
		)
		g["times"].append(p["time"])
		g["rows"].append({"time": p["time"], "meal": p["meal"]})
		if p["meal"] not in g["meals"]:
			g["meals"].append(p["meal"])
	rows = []
	for g in groups.values():
		times = g.pop("times")
		g.update(punch_sequence(times))
		g["rows"].sort(key=lambda r: r["time"])
		rows.append(g)
	rows.sort(key=lambda r: (r["times"][0] if r["times"] else "", r["employee"]))
	return rows


def employee_days(start, end, today, off, leave, ate, joined=None, relieved=None):
	"""Day-by-day lunch record for one employee.

	`off` is {date: description} of off days, `leave` {date: {leave_type, name}}
	and `ate` a set of dates with a punch for the meal. A day the employee was
	expected (employed, not off, not on leave or sick off) with no punch is
	**missed** once it is over; today with no punch yet is "today", later days
	"upcoming". Off days and leave keep their own status and still show a punch
	if there was one.
	"""
	days = []
	d = getdate(start)
	end = getdate(end)
	today = getdate(today)
	while d <= end:
		if (joined and d < getdate(joined)) or (relieved and d > getdate(relieved)):
			status, detail = "not_employed", None
		else:
			lv = leave.get(d)
			bucket = classify(off.get(d), lv and lv["leave_type"])
			if bucket == OFF_DAY:
				status, detail = OFF_DAY, off.get(d)
			elif bucket:
				status, detail = bucket, lv["leave_type"]
			else:
				status, detail = "expected", None
		ate_today = d in ate
		if status != "expected":
			outcome = None
		elif ate_today:
			outcome = "ate"
		elif d < today:
			outcome = "missed"
		elif d == today:
			outcome = "today"
		else:
			outcome = "upcoming"
		days.append(
			{"date": str(d), "status": status, "detail": detail, "ate": ate_today, "outcome": outcome}
		)
		d = add_days(d, 1)

	past = [x for x in days if getdate(x["date"]) <= today]
	return {
		"days": days,
		"summary": {
			"expected": sum(1 for x in past if x["status"] == "expected"),
			"ate_expected": sum(1 for x in past if x["outcome"] == "ate"),
			"missed": sum(1 for x in days if x["outcome"] == "missed"),
			"ate": sum(1 for x in past if x["ate"]),
			"off_day": sum(1 for x in past if x["status"] == OFF_DAY),
			"sick": sum(1 for x in past if x["status"] == SICK),
			"leave": sum(1 for x in past if x["status"] == LEAVE),
			"upcoming": sum(1 for x in days if x["outcome"] == "upcoming"),
		},
	}


def build_employee(employee, on_date, meal="Lunch"):
	from upande_ta.upande_ta.doctype.biometric_setting.biometric_setting import payroll_window_for

	emp = frappe.db.get_value(
		"Employee",
		employee,
		[
			"name as employee",
			"employee_name",
			"company",
			"custom_farm as farm",
			"designation",
			"department",
			"date_of_joining",
			"relieving_date",
			"status",
		],
		as_dict=True,
	)
	if not emp:
		frappe.throw(frappe._("Employee {0} not found").format(employee), frappe.DoesNotExistError)
	allowed = get_allowed_companies()
	if allowed and emp.company not in allowed:
		frappe.throw(
			frappe._("You are not permitted to view attendance data for {0}").format(emp.company),
			frappe.PermissionError,
		)
	meal = meal if meal in MEALS else "Lunch"
	on_date = getdate(on_date or nowdate())
	start, end = payroll_window_for(emp.company, on_date)

	off = _off_days([frappe._dict(employee=emp.employee, company=emp.company)], start, end).get(
		emp.employee, {}
	)
	leave = _leaves([emp.employee], start, end).get(emp.employee, {})
	rows = frappe.db.sql(
		"""
		SELECT name, log_type AS meal, time FROM `tabMeal Checkin`
		WHERE docstatus < 2 AND employee = %(employee)s AND time >= %(start)s AND time < %(end_excl)s
		ORDER BY time
		""",
		{"employee": emp.employee, "start": start, "end_excl": add_days(min(end, getdate(nowdate())), 1)},
		as_dict=True,
	)

	costs = daily_costs(start, end)
	ate = set()
	punches_by_day = {}
	per_meal = {}
	seen = set()
	for r in rows:
		d = getdate(r.time)
		punches_by_day.setdefault(str(d), []).append({"time": str(r.time), "meal": r.meal})
		pm = per_meal.setdefault(r.meal or "", {"meals": 0, "punches": 0, "cost": 0.0, "unpriced": 0})
		pm["punches"] += 1
		if (d, r.meal) not in seen:
			seen.add((d, r.meal))
			pm["meals"] += 1
			rate = rate_for(costs, d, emp.company, r.meal)
			if rate:
				pm["cost"] += rate["cost"]
			else:
				pm["unpriced"] += 1
		if r.meal == meal:
			ate.add(d)

	record = employee_days(start, end, nowdate(), off, leave, ate, emp.date_of_joining, emp.relieving_date)
	attendance = {
		str(getdate(d)): st
		for d, st in frappe.db.sql(
			"""
			SELECT attendance_date, status FROM `tabAttendance`
			WHERE docstatus = 1 AND employee = %(employee)s AND attendance_date BETWEEN %(start)s AND %(end)s
			""",
			{"employee": emp.employee, "start": start, "end": end},
		)
	}
	bio = {
		str(getdate(d)): {"count": cint(n), "first": str(first), "last": str(last)}
		for d, n, first, last in frappe.db.sql(
			"""
			SELECT DATE(time), COUNT(*), MIN(time), MAX(time) FROM `tabEmployee Checkin`
			WHERE employee = %(employee)s AND time >= %(start)s AND time < %(end_excl)s
			GROUP BY DATE(time)
			""",
			{"employee": emp.employee, "start": start, "end_excl": add_days(end, 1)},
		)
	}
	for day in record["days"]:
		day["punches"] = punches_by_day.get(day["date"], [])
		day["attendance"] = attendance.get(day["date"])
		day["biometric"] = bio.get(day["date"]) or {"count": 0, "first": None, "last": None}
		day["menu"] = day_menu(costs, day["date"], emp.company)

	by_meal = []
	cost_total = 0.0
	for m in [x for x in MEALS if x in per_meal] + [x for x in per_meal if x not in MEALS]:
		pm = per_meal[m]
		priced = pm["meals"] - pm["unpriced"]
		cost_total += pm["cost"]
		by_meal.append(
			{
				"meal": m,
				"meals": pm["meals"],
				"punches": pm["punches"],
				"cost_per_meal": pm["cost"] / priced if priced else None,
				"cost": pm["cost"] if priced else None,
				"unpriced": pm["unpriced"],
			}
		)

	summary = record["summary"]
	summary.update(
		days_in_period=len(record["days"]),
		working_days=sum(1 for x in record["days"] if x["status"] == "expected"),
		present=sum(1 for st in attendance.values() if st in PRESENT_STATUSES),
		biometric_punches=sum(b["count"] for b in bio.values()),
		biometric_days=len(bio),
		meals=sum(b["meals"] for b in by_meal),
		meal_punches=len(rows),
		unpriced_meals=sum(b["unpriced"] for b in by_meal),
	)
	return {
		"employee": emp.employee,
		"employee_name": emp.employee_name,
		"company": emp.company,
		"farm": emp.farm or "",
		"designation": emp.designation,
		"department": emp.department,
		"status": emp.status,
		"date": str(on_date),
		"meal": meal,
		"period": {"start": str(start), "end": str(end)},
		"summary": summary,
		"days": record["days"],
		"by_meal": by_meal,
		"cost": cost_total,
		"priced": bool(costs),
		"currency": frappe.db.get_default("currency") or "",
	}


@frappe.whitelist()
def canteen_employee(employee, date=None, meal="Lunch"):
	"""One employee's meals over the payroll period containing `date`: the days
	they ate, the days they were expected and missed, and what it cost."""
	return build_employee(employee, date or nowdate(), meal or "Lunch")
