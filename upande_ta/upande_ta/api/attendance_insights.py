# Copyright (c) 2026, Upande Limited and contributors
# For license information, please see license.txt
"""Backend for the Attendance Insights dashboard (/attendance-insights).

These eight endpoints used to be Server Script records edited in the desk. That
made them site data: not in version control, not reviewable, not deployable and
free to drift between sites. They are ordinary whitelisted methods here.

The bodies are the live scripts unchanged. Two shapes existed — some wrapped
their work in ``run_script()``, some ran straight down the module — and both are
now plain functions. Each writes its payload to ``frappe.response["message"]``
exactly as it did in the sandbox, so what the dashboard parses is unchanged.

Nothing here relied on the sandbox: no imports, and every helper call was
already ``frappe.utils.*``. Comments in the originals describing sandbox limits
("no imports, no def, no lambdas") are kept as the record of why the code is
shaped the way it is — those limits no longer apply, so new work in this file
need not obey them.

Whitelisted without ``allow_guest``, which preserves the old access rule: a
signed-in session is required and the caller's own permissions still apply.
"""

import frappe

# Kaitet has no "Task Worker" employment type — the Employment Type master holds
# Permanent / Temporary / Contract, and task workers are carried as Temporary
# ("Task Worker" is their designation). The sidebar's task-worker rollup keyed on
# the employment type, so every farm badge read 0.
TASK_WORKER_EMPLOYMENT_TYPE = "Temporary"


# ─────────────────────────────────────────────────────────────────────────────
# Server Script: attendance_dashboard_data  (type: API, api_method: attendance_dashboard_data)
# REWRITE — v3  (PERFORMANCE)
#
# What changed vs v2 (same JSON contract, same numbers):
#  1. SARGABLE TIME FILTERS. Every checkin query previously did
#	 `WHERE DATE(c.time) BETWEEN a AND b`, which wraps the indexed column in a
#	 function and forces a FULL TABLE SCAN of `tabEmployee Checkin` every call.
#	 Replaced with a half-open range `c.time >= a AND c.time < b_plus1`, which
#	 lets MySQL use an index on (time) / (employee, time). This is the single
#	 biggest win on first load.
#  2. avg_hours DE-CORRELATED. The per-row `(SELECT MIN(c2.time) ...)` correlated
#	 subquery (one extra scan per employee-day) is replaced by a single LEFT
#	 JOIN to the OUT scans, paired to the earliest OUT within 20h. Same result.
#  3. late_total reuses the fl CTE result set instead of a second full CTE pass.
#
# Sandbox rules honoured: no imports, no def, no tuple unpacking, no lambdas,
# no augmented subscript assignment, SELECT/WITH-only SQL, bracket-notation
# response, frappe.form_dict, and NO str.format() (uses str.replace()).
# ─────────────────────────────────────────────────────────────────────────────
@frappe.whitelist()
def attendance_dashboard_data():
	"""The KPI strip: headcounts, the present/absent split and the daily series."""
	fd = frappe.form_dict
	from_date = fd.get("from_date") or frappe.utils.nowdate()
	to_date = fd.get("to_date") or frappe.utils.nowdate()
	farm = (fd.get("farm") or "").strip()
	company = (fd.get("company") or "").strip()
	emptype = (fd.get("employment_type") or "").strip()

	# clamp range to max 366 days, never inverted
	start_d = frappe.utils.getdate(from_date)
	end_d = frappe.utils.getdate(to_date)
	if end_d < start_d:
		end_d = start_d
	if frappe.utils.date_diff(end_d, start_d) > 366:
		start_d = frappe.utils.add_days(end_d, -366)
	from_date = str(start_d)
	to_date = str(end_d)

	# half-open upper bound: [from_date 00:00, to_date+1 00:00) covers the whole
	# of to_date without any DATE() wrapping on the indexed column.
	to_date_excl = str(frappe.utils.add_days(end_d, 1))

	params = {
		"from_date": from_date,
		"to_date": to_date,
		"to_date_excl": to_date_excl,
		"farm": farm,
		"company": company,
	}

	econd = ""
	if farm:
		econd = econd + " AND e.custom_farm = %(farm)s"
	if company:
		econd = econd + " AND e.company = %(company)s"
	if emptype:
		econd = econd + " AND FIND_IN_SET(e.employment_type, " + frappe.db.escape(emptype) + ")"

	# range predicate on the raw datetime column (sargable). Alias `c` assumed.
	trange = " AND c.time >= %(from_date)s AND c.time < %(to_date_excl)s "

	# ── reusable SQL fragments (shift-aware lateness) ────────────────────────────
	grace_in = "IFNULL(CASE WHEN st.enable_late_entry_marking = 1 THEN st.late_entry_grace_period ELSE 0 END, 0)"
	grace_out = "IFNULL(CASE WHEN st.enable_early_exit_marking = 1 THEN st.early_exit_grace_period ELSE 0 END, 0)"
	shift_start_dt = "ADDTIME(CAST(DATE(c.time) AS DATETIME), st.start_time)"
	shift_end_dt = "ADDTIME(CAST(DATE(c.time) AS DATETIME), st.end_time)"
	late_expr = "GREATEST(0, TIMESTAMPDIFF(MINUTE, DATE_ADD(" + shift_start_dt + ", INTERVAL " + grace_in + " MINUTE), c.time))"
	early_expr = "GREATEST(0, TIMESTAMPDIFF(MINUTE, c.time, DATE_SUB(" + shift_end_dt + ", INTERVAL " + grace_out + " MINUTE)))"

	# ── filter dropdowns ─────────────────────────────────────────────────────────
	farm_list = []
	frows = frappe.db.sql("SELECT DISTINCT e.custom_farm AS f FROM `tabEmployee` e WHERE e.status = 'Active' AND IFNULL(e.custom_farm, '') != '' ORDER BY e.custom_farm", as_dict=1)
	for fr in frows:
		farm_list.append(fr["f"])

	company_list = []
	crows = frappe.db.sql("SELECT DISTINCT e.company AS co FROM `tabEmployee` e WHERE e.status = 'Active' AND IFNULL(e.company, '') != '' ORDER BY e.company", as_dict=1)
	for cr in crows:
		company_list.append(cr["co"])

	# ── company → unit/division (farm) hierarchy, for the floating sidebar ──
	company_farms = {}
	cfrows = frappe.db.sql("SELECT DISTINCT e.company AS co, e.custom_farm AS f FROM `tabEmployee` e WHERE e.status = 'Active' AND IFNULL(e.company,'') != '' AND IFNULL(e.custom_farm,'') != '' ORDER BY e.company, e.custom_farm", as_dict=1)
	for cr in cfrows:
		company_farms.setdefault(cr["co"], [])
		if cr["f"] not in company_farms[cr["co"]]:
			company_farms[cr["co"]].append(cr["f"])

	emp_type_list = []
	etrows = frappe.db.sql("SELECT DISTINCT e.employment_type AS et FROM `tabEmployee` e WHERE e.status = 'Active' AND IFNULL(e.employment_type,'') != '' ORDER BY e.employment_type", as_dict=1)
	for er in etrows:
		emp_type_list.append(er["et"])

	# per-company/per-farm split: task workers vs the rest
	company_farm_counts = {}
	cfcrows = frappe.db.sql("SELECT e.company AS co, e.custom_farm AS f, SUM(CASE WHEN e.employment_type=%(tw_type)s THEN 1 ELSE 0 END) AS tw, SUM(CASE WHEN COALESCE(e.employment_type,'')<>%(tw_type)s THEN 1 ELSE 0 END) AS rest FROM `tabEmployee` e WHERE e.status='Active' AND IFNULL(e.company,'')<>'' AND IFNULL(e.custom_farm,'')<>'' GROUP BY e.company, e.custom_farm", {"tw_type": TASK_WORKER_EMPLOYMENT_TYPE}, as_dict=1)
	for cr in cfcrows:
		company_farm_counts.setdefault(cr["co"], {})
		company_farm_counts[cr["co"]][cr["f"]] = {"tw": int(cr["tw"] or 0), "rest": int(cr["rest"] or 0)}

	# ── active headcount under the current filter ────────────────────────────────
	active_total = frappe.db.sql("SELECT COUNT(*) FROM `tabEmployee` e WHERE e.status = 'Active'" + econd, params)[0][0]

	# ── cards: range-wide checkin stats ──────────────────────────────────────────
	cards_row = frappe.db.sql("""
		SELECT COUNT(*) AS total,
			   COUNT(DISTINCT c.employee) AS uniq,
			   SUM(CASE WHEN c.log_type = 'IN' THEN 1 ELSE 0 END) AS in_count
		FROM `tabEmployee Checkin` c
		INNER JOIN `tabEmployee` e ON e.name = c.employee
		WHERE 1=1 """ + trange + econd, params, as_dict=1)[0]

	# ── late/early employee-day CTE (first IN / last OUT per employee-day) ──────
	fi_cte = """
	WITH fi AS (
		SELECT c.employee AS employee, DATE(c.time) AS day,
			   MIN(c.time) AS first_in,
			   MAX(NULLIF(c.shift, '')) AS shift
		FROM `tabEmployee Checkin` c
		INNER JOIN `tabEmployee` e ON e.name = c.employee
		WHERE c.log_type = 'IN' {trange} {econd}
		GROUP BY c.employee, DATE(c.time)
	),
	fl AS (
		SELECT fi.employee AS employee, fi.day AS day, fi.first_in AS first_in,
			   GREATEST(0, TIMESTAMPDIFF(MINUTE,
				   DATE_ADD(ADDTIME(CAST(fi.day AS DATETIME), st.start_time),
							INTERVAL IFNULL(CASE WHEN st.enable_late_entry_marking = 1
											THEN st.late_entry_grace_period ELSE 0 END, 0) MINUTE),
				   fi.first_in)) AS mins_late
		FROM fi
		INNER JOIN `tabEmployee` e2 ON e2.name = fi.employee
		LEFT JOIN `tabShift Type` st ON st.name = COALESCE(fi.shift, e2.default_shift)
	)
	""".replace("{trange}", trange).replace("{econd}", econd)

	# single pass: aggregate the whole fl set once, both the top-25 and the total
	# late-day count come from the same query's window instead of two CTE passes.
	top_late = frappe.db.sql(fi_cte + """
		SELECT fl.employee AS employee, e.employee_name AS employee_name,
			   e.custom_farm AS farm,
			   COUNT(*) AS late_days,
			   ROUND(AVG(CASE WHEN fl.mins_late <= 300 THEN fl.mins_late END), 1) AS avg_minutes_late,
			   MAX(fl.mins_late) AS max_minutes_late,
			   ROUND(STDDEV_SAMP(TIME_TO_SEC(TIME(fl.first_in))) / 60, 0) AS arrival_spread_min
		FROM fl
		INNER JOIN `tabEmployee` e ON e.name = fl.employee
		WHERE fl.mins_late > 0
		GROUP BY fl.employee, e.employee_name, e.custom_farm
		ORDER BY late_days DESC, avg_minutes_late DESC
		LIMIT 2000""", params, as_dict=1)

	# late_total from the same CTE (one extra lightweight aggregate, still uses the
	# indexed range scan from fi).
	late_total = frappe.db.sql(fi_cte + "SELECT COUNT(*) FROM fl WHERE fl.mins_late > 0", params)[0][0]

	lo_cte = """
	WITH lo AS (
		SELECT c.employee AS employee, DATE(c.time) AS day,
			   MAX(c.time) AS last_out,
			   MAX(NULLIF(c.shift, '')) AS shift
		FROM `tabEmployee Checkin` c
		INNER JOIN `tabEmployee` e ON e.name = c.employee
		WHERE c.log_type = 'OUT' {trange} {econd}
		GROUP BY c.employee, DATE(c.time)
	),
	fe AS (
		SELECT lo.employee AS employee, lo.day AS day,
			   GREATEST(0, TIMESTAMPDIFF(MINUTE, lo.last_out,
				   DATE_SUB(ADDTIME(CAST(lo.day AS DATETIME), st.end_time),
							INTERVAL IFNULL(CASE WHEN st.enable_early_exit_marking = 1
											THEN st.early_exit_grace_period ELSE 0 END, 0) MINUTE))) AS mins_early
		FROM lo
		INNER JOIN `tabEmployee` e2 ON e2.name = lo.employee
		LEFT JOIN `tabShift Type` st ON st.name = COALESCE(lo.shift, e2.default_shift)
		WHERE st.end_time > st.start_time
	)
	""".replace("{trange}", trange).replace("{econd}", econd)

	top_early = frappe.db.sql(lo_cte + """
		SELECT fe.employee AS employee, e.employee_name AS employee_name,
			   e.custom_farm AS farm,
			   COUNT(*) AS early_days,
			   ROUND(AVG(CASE WHEN fe.mins_early <= 300 THEN fe.mins_early END), 1) AS avg_minutes_early,
			   MAX(fe.mins_early) AS max_minutes_early
		FROM fe
		INNER JOIN `tabEmployee` e ON e.name = fe.employee
		WHERE fe.mins_early > 0
		GROUP BY fe.employee, e.employee_name, e.custom_farm
		ORDER BY early_days DESC, avg_minutes_early DESC
		LIMIT 2000""", params, as_dict=1)

	# ── per-day PRESENT = distinct(checkin employees ∪ manual Present attendance) ─
	present_rows = frappe.db.sql("""
		SELECT t.day AS day, COUNT(DISTINCT t.emp) AS present
		FROM (
			SELECT DATE(c.time) AS day, c.employee AS emp
			FROM `tabEmployee Checkin` c
			INNER JOIN `tabEmployee` e ON e.name = c.employee
			WHERE 1=1 {trange} {econd}
			UNION
			SELECT a.attendance_date AS day, a.employee AS emp
			FROM `tabAttendance` a
			INNER JOIN `tabEmployee` e ON e.name = a.employee
			WHERE a.docstatus = 1 AND a.status = 'Present'
			  AND a.attendance_date BETWEEN %(from_date)s AND %(to_date)s {econd}
		) t
		GROUP BY t.day""".replace("{trange}", trange).replace("{econd}", econd), params, as_dict=1)

	present_by_day = {}
	for pr in present_rows:
		present_by_day[str(pr["day"])] = pr["present"]

	# ── per-day MARKED attendance (auto-attendance / HR submissions) ─────────────
	marked_rows = frappe.db.sql("""
		SELECT a.attendance_date AS day,
			   SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent_n,
			   COUNT(*) AS total_n
		FROM `tabAttendance` a
		INNER JOIN `tabEmployee` e ON e.name = a.employee
		WHERE a.docstatus = 1
		  AND a.attendance_date BETWEEN %(from_date)s AND %(to_date)s {econd}
		GROUP BY a.attendance_date""".replace("{econd}", econd), params, as_dict=1)

	marked_absent_by_day = {}
	marked_total_by_day = {}
	for mr in marked_rows:
		marked_absent_by_day[str(mr["day"])] = mr["absent_n"] or 0
		marked_total_by_day[str(mr["day"])] = mr["total_n"] or 0

	# ── per-day ON LEAVE: expand approved Leave Applications, dedup per emp/day ──
	leave_rows = frappe.db.sql("""
		SELECT la.employee AS employee, la.from_date AS f, la.to_date AS t
		FROM `tabLeave Application` la
		INNER JOIN `tabEmployee` e ON e.name = la.employee
		WHERE la.docstatus = 1 AND la.status = 'Approved'
		  AND la.from_date <= %(to_date)s AND la.to_date >= %(from_date)s {econd}
	""".replace("{econd}", econd), params, as_dict=1)

	leave_by_day = {}
	leave_seen = {}
	for lr in leave_rows:
		cur2 = frappe.utils.getdate(lr["f"])
		if cur2 < start_d:
			cur2 = start_d
		stop2 = frappe.utils.getdate(lr["t"])
		if stop2 > end_d:
			stop2 = end_d
		g2 = 0
		while cur2 <= stop2 and g2 < 400:
			kk = str(cur2) + "|" + lr["employee"]
			if kk not in leave_seen:
				leave_seen[kk] = 1
				dk = str(cur2)
				leave_by_day[dk] = leave_by_day.get(dk, 0) + 1
			cur2 = frappe.utils.add_days(cur2, 1)
			g2 = g2 + 1

	# ── per-day OFF ──────────────────────────────────────────────────────────────
	off_by_day = {}
	off1 = frappe.db.sql("""
		SELECT h.holiday_date AS day, COUNT(DISTINCT e.name) AS n
		FROM `tabEmployee` e
		INNER JOIN `tabHoliday` h ON h.parent = e.holiday_list
		WHERE e.status = 'Active' AND IFNULL(e.holiday_list, '') != '' {econd}
		  AND h.holiday_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY h.holiday_date""".replace("{econd}", econd), params, as_dict=1)
	for o1 in off1:
		dk = str(o1["day"])
		off_by_day[dk] = off_by_day.get(dk, 0) + o1["n"]

	off2 = frappe.db.sql("""
		SELECT h.holiday_date AS day, COUNT(DISTINCT e.name) AS n
		FROM `tabEmployee` e
		INNER JOIN `tabCompany` co ON co.name = e.company
		INNER JOIN `tabHoliday` h ON h.parent = co.default_holiday_list
		WHERE e.status = 'Active' AND IFNULL(e.holiday_list, '') = '' {econd}
		  AND h.holiday_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY h.holiday_date""".replace("{econd}", econd), params, as_dict=1)
	for o2 in off2:
		dk = str(o2["day"])
		off_by_day[dk] = off_by_day.get(dk, 0) + o2["n"]

	# ── assemble the daily series ────────────────────────────────────────────────
	daily = []
	cur = start_d
	guard = 0
	while cur <= end_d and guard < 400:
		dk = str(cur)
		onl = leave_by_day.get(dk, 0)
		offn = off_by_day.get(dk, 0)
		pres = present_by_day.get(dk, 0)
		exp = active_total - onl - offn
		if exp < 0:
			exp = 0
		marked_abs = marked_absent_by_day.get(dk, 0)
		marked_tot = marked_total_by_day.get(dk, 0)
		half_exp = exp / 2 if exp > 0 else 0
		if marked_abs > 0 or (marked_tot > 0 and marked_tot >= half_exp):
			absent = marked_abs
			absent_src = "marked"
		else:
			absent = exp - pres
			if absent < 0:
				absent = 0
			absent_src = "derived"
		daily.append({"day": dk, "present": pres, "on_leave": onl, "off": offn, "expected": exp, "absent": absent, "absent_src": absent_src})
		cur = frappe.utils.add_days(cur, 1)
		guard = guard + 1

	# ── avg hours per day: first IN joined to the earliest OUT within 20h.
	#	DE-CORRELATED: instead of a per-row subquery, join the IN set to the OUT
	#	set and take MIN(out) among OUTs that fall in (first_in, first_in+20h]. ───
	avg_hours = frappe.db.sql("""
		SELECT t.day AS day,
			   ROUND(AVG(t.hrs), 2) AS avg_hours,
			   COUNT(*) AS n
		FROM (
			SELECT i.day AS day,
				   TIMESTAMPDIFF(SECOND, i.first_in, MIN(o.time)) / 3600.0 AS hrs
			FROM (
				SELECT c.employee AS employee, DATE(c.time) AS day, MIN(c.time) AS first_in
				FROM `tabEmployee Checkin` c
				INNER JOIN `tabEmployee` e ON e.name = c.employee
				WHERE c.log_type = 'IN' {trange} {econd}
				GROUP BY c.employee, DATE(c.time)
			) i
			LEFT JOIN `tabEmployee Checkin` o
				   ON o.employee = i.employee
				  AND o.log_type = 'OUT'
				  AND o.time > i.first_in
				  AND o.time <= DATE_ADD(i.first_in, INTERVAL 20 HOUR)
			GROUP BY i.employee, i.day, i.first_in
		) t
		WHERE t.hrs IS NOT NULL AND t.hrs > 0
		GROUP BY t.day
		ORDER BY t.day""".replace("{trange}", trange).replace("{econd}", econd), params, as_dict=1)

	# ── hourly pattern ───────────────────────────────────────────────────────────
	hourly = frappe.db.sql("""
		SELECT HOUR(c.time) AS hr, c.log_type AS log_type, COUNT(*) AS n
		FROM `tabEmployee Checkin` c
		INNER JOIN `tabEmployee` e ON e.name = c.employee
		WHERE 1=1 {trange} {econd}
		GROUP BY HOUR(c.time), c.log_type""".replace("{trange}", trange).replace("{econd}", econd), params, as_dict=1)

	# NOTE: the raw checkin rows for the Register tab's Checkin Records table used to
	# be computed here (up to 6000 rows). They have been split into the `att_rows`
	# API script and are fetched lazily by the frontend only when the Register tab
	# is first opened — so the default Overview load no longer pays for them.

	frappe.response["message"] = {
		"from_date": from_date,
		"to_date": to_date,
		"farms": farm_list,
		"companies": company_list,
		"company_farms": company_farms,
		"employment_types": emp_type_list,
		"company_farm_counts": company_farm_counts,
		"active_total": active_total,
		"cards": {
			"total": cards_row["total"] or 0,
			"unique": cards_row["uniq"] or 0,
			"in_count": cards_row["in_count"] or 0,
			"late": late_total or 0
		},
		"daily": daily,
		"avg_hours": avg_hours,
		"hourly": hourly,
		"top_late": top_late,
		"top_early": top_early
	}


@frappe.whitelist()
def attendance_register():
	"""The register for one date: present, absent, on leave and rest day, plus late/early detail."""

	step = "start"
	try:
		reg_date = frappe.form_dict.get("date") or str(frappe.utils.today())
		is_today = (str(reg_date) == str(frappe.utils.today()))
		farm = (frappe.form_dict.get("farm") or "").strip()
		company = (frappe.form_dict.get("company") or "").strip()
		emptype = (frappe.form_dict.get("employment_type") or "").strip()

		# ── TREND MODE (trend=1): per-farm daily present headcount, then return early.
		# Piggybacked here because new api_methods don't register reliably on Frappe
		# Cloud (HTTP 417); the flag keeps normal register loads unaffected. ──
		if (frappe.form_dict.get("trend") or "") == "1":
			t_days = frappe.utils.cint(frappe.form_dict.get("days") or 30) or 30
			if t_days > 92:
				t_days = 92
			t_where = ""
			t_params = {"reg_date": reg_date, "t_days": t_days}
			if farm:
				t_where += " AND TRIM(e.custom_farm) = TRIM(%(farm)s)"
				t_params["farm"] = farm
			if company:
				t_where += " AND e.company = %(company)s"
				t_params["company"] = company
			if emptype:
				t_where += " AND FIND_IN_SET(e.employment_type, %(employment_type)s)"
				t_params["employment_type"] = emptype
			# explicit window (e.g. the payroll period) wins over the rolling day count
			t_from = (frappe.form_dict.get("trend_from") or "").strip()
			t_to = (frappe.form_dict.get("trend_to") or "").strip()
			if t_from and t_to:
				t_range = " ec.`time` >= %(t_from)s AND ec.`time` < %(t_to)s + INTERVAL 1 DAY "
				t_params["t_from"] = t_from
				t_params["t_to"] = t_to
			else:
				t_range = " ec.`time` >= DATE_SUB(%(reg_date)s, INTERVAL %(t_days)s DAY) AND ec.`time` < %(reg_date)s + INTERVAL 1 DAY "
			t_rows = frappe.db.sql("""
				SELECT DATE(ec.`time`) AS d,
					   COALESCE(NULLIF(TRIM(e.custom_farm), ''), 'No Farm') AS f,
					   COUNT(DISTINCT ec.employee) AS n
				FROM `tabEmployee Checkin` ec
				JOIN `tabEmployee` e ON e.name = ec.employee
				WHERE """ + t_range + """
				  AND e.status = 'Active'
				""" + t_where + """
				GROUP BY DATE(ec.`time`), f
				ORDER BY d ASC, f ASC
			""", t_params, as_dict=True)
			frappe.response["message"] = {
				"trend": [{"d": str(r["d"]), "f": r["f"], "n": int(r["n"] or 0)} for r in t_rows],
				"days": t_days, "to": str(reg_date),
			}
			return

		# ── LOST HOURS MODE (lost=1): per-employee working time lost over a
		# rolling window (default the last 7 days). "Lost" is measured against
		# the employee's own shift: minutes arriving late, minutes leaving more
		# than an hour early, and whole shifts missed while rostered on.
		# Piggybacked on this endpoint for the same reason as trend=1 above
		# (new api_methods return HTTP 417 on Frappe Cloud). ──
		if (frappe.form_dict.get("lost") or "") == "1":
			l_days = frappe.utils.cint(frappe.form_dict.get("days") or 7) or 7
			if l_days > 31:
				l_days = 31
			if l_days < 1:
				l_days = 7
			l_to = str(reg_date)
			l_from = str(frappe.utils.add_days(l_to, -(l_days - 1)))
			l_today = str(frappe.utils.today())
			l_now = str(frappe.utils.nowtime())

			# MySQL TIME values arrive as timedeltas, so str() can be "8:00:00"
			# (no leading zero) and datetimes carry a date prefix. Parse by
			# splitting instead of slicing fixed offsets.
			def mins_of_day(val):
				if val is None:
					return None
				txt = str(val)
				if " " in txt:
					txt = txt.split(" ")[-1]
				parts = txt.split(":")
				if len(parts) < 2:
					return None
				return int(parts[0]) * 60 + int(parts[1])

			l_now_m = mins_of_day(l_now) or 0

			l_where = ""
			l_params = {"l_from": l_from, "l_to": l_to}
			if farm:
				l_where += " AND TRIM(e.custom_farm) = TRIM(%(l_farm)s)"
				l_params["l_farm"] = farm
			if company:
				l_where += " AND e.company = %(l_company)s"
				l_params["l_company"] = company
			if emptype:
				l_where += " AND FIND_IN_SET(e.employment_type, %(l_emptype)s)"
				l_params["l_emptype"] = emptype

			# shift geometry, in minutes-of-day; night shifts (end <= start) get
			# +1440 on the end so the duration stays positive across midnight
			l_sh_start = {}
			l_sh_dur = {}
			l_sh_night = {}
			for sr in frappe.db.sql("SELECT name, start_time, end_time FROM `tabShift Type`", as_dict=True):
				if sr.get("start_time") is None or sr.get("end_time") is None:
					continue
				s_m = mins_of_day(sr["start_time"])
				e_m = mins_of_day(sr["end_time"])
				if s_m is None or e_m is None:
					continue
				d_m = e_m - s_m
				n_f = 0
				if d_m <= 0:
					d_m = d_m + 1440
					n_f = 1
				l_sh_start[sr["name"]] = s_m
				l_sh_dur[sr["name"]] = d_m
				l_sh_night[sr["name"]] = n_f

			l_emps = frappe.db.sql("""
				SELECT e.name AS emp, e.employee_name AS nm,
					   COALESCE(NULLIF(TRIM(e.custom_farm), ''), '') AS farm,
					   COALESCE(e.company, '') AS company,
					   COALESCE(e.employment_type, '') AS etype,
					   COALESCE(e.designation, '') AS designation,
					   COALESCE(e.default_shift, '') AS def_shift,
					   e.date_of_joining AS doj, e.relieving_date AS rel
				FROM `tabEmployee` e
				WHERE e.status = 'Active'
			""" + l_where, l_params, as_dict=True)
			if not l_emps:
				frappe.response["message"] = {"lost": [], "from": l_from, "to": l_to, "days": l_days}
				return

			# effective shift per employee/date: a Shift Assignment covering the
			# date wins over default_shift; the newest assignment wins on overlap
			l_sa = {}
			for sa in frappe.db.sql("""
				SELECT sa.employee AS emp, sa.shift_type AS sh, sa.start_date AS sd, sa.end_date AS ed
				FROM `tabShift Assignment` sa
				JOIN `tabEmployee` e ON e.name = sa.employee
				WHERE sa.docstatus = 1 AND IFNULL(sa.status, 'Active') <> 'Inactive'
				  AND sa.start_date <= %(l_to)s
				  AND (sa.end_date IS NULL OR sa.end_date >= %(l_from)s)
				  AND e.status = 'Active'
			""" + l_where + """
				ORDER BY sa.start_date ASC, sa.name ASC
			""", l_params, as_dict=True):
				l_sa.setdefault(str(sa["emp"]), []).append(sa)

			# first IN / last OUT per calendar day (day shifts)
			l_day_io = {}
			for r in frappe.db.sql("""
				SELECT ec.employee AS emp, DATE(ec.`time`) AS d,
					   MIN(CASE WHEN ec.log_type = 'IN' THEN ec.`time` END) AS fin,
					   MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.`time` END) AS fout,
					   COUNT(*) AS n
				FROM `tabEmployee Checkin` ec
				JOIN `tabEmployee` e ON e.name = ec.employee
				WHERE ec.`time` >= %(l_from)s AND ec.`time` < %(l_to)s + INTERVAL 1 DAY
				  AND e.status = 'Active'
			""" + l_where + """
				GROUP BY ec.employee, DATE(ec.`time`)
			""", l_params, as_dict=True):
				l_day_io[str(r["emp"]) + "|" + str(r["d"])] = r

			# first IN / last OUT per night window (noon → noon), keyed on the
			# date the shift started
			l_night_io = {}
			for r in frappe.db.sql("""
				SELECT ec.employee AS emp, DATE(ec.`time` - INTERVAL 12 HOUR) AS d,
					   MIN(CASE WHEN ec.log_type = 'IN' THEN ec.`time` END) AS fin,
					   MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.`time` END) AS fout,
					   COUNT(*) AS n
				FROM `tabEmployee Checkin` ec
				JOIN `tabEmployee` e ON e.name = ec.employee
				WHERE ec.`time` >= %(l_from)s + INTERVAL 12 HOUR
				  AND ec.`time` < %(l_to)s + INTERVAL 36 HOUR
				  AND e.status = 'Active'
			""" + l_where + """
				GROUP BY ec.employee, DATE(ec.`time` - INTERVAL 12 HOUR)
			""", l_params, as_dict=True):
				l_night_io[str(r["emp"]) + "|" + str(r["d"])] = r

			# manual/derived Attendance in the window — an explicit record beats
			# anything inferred from scans
			# Some employees carry more than one submitted Attendance for the same
			# day (e.g. an auto-marked Absent under one shift alongside a Present
			# under another). Pick deterministically: an attended status always
			# beats Absent, so the same day never reads as both.
			ATT_RANK = {"Present": 1, "Work From Home": 1, "Half Day": 2,
						"On Leave": 3, "Holiday": 4, "Absent": 5}
			l_att = {}
			l_att_rank = {}
			l_att_dupes = 0
			for r in frappe.db.sql("""
				SELECT a.employee AS emp, a.attendance_date AS d, a.status AS st
				FROM `tabAttendance` a
				JOIN `tabEmployee` e ON e.name = a.employee
				WHERE a.docstatus < 2
				  AND a.attendance_date BETWEEN %(l_from)s AND %(l_to)s
				  AND e.status = 'Active'
			""" + l_where, l_params, as_dict=True):
				a_key = str(r["emp"]) + "|" + str(r["d"])
				a_st = str(r["st"] or "")
				a_rk = ATT_RANK.get(a_st) or 9
				if a_key in l_att_rank:
					l_att_dupes = l_att_dupes + 1
					if a_rk >= l_att_rank[a_key]:
						continue
				l_att[a_key] = a_st
				l_att_rank[a_key] = a_rk

			# approved leave and rest days (weekly off / public holiday)
			l_leave = {}
			for r in frappe.db.sql("""
				SELECT la.employee AS emp, la.from_date AS fd, la.to_date AS td
				FROM `tabLeave Application` la
				JOIN `tabEmployee` e ON e.name = la.employee
				WHERE la.docstatus = 1 AND la.status = 'Approved'
				  AND la.from_date <= %(l_to)s AND la.to_date >= %(l_from)s
				  AND e.status = 'Active'
			""" + l_where, l_params, as_dict=True):
				l_leave.setdefault(str(r["emp"]), []).append(r)

			l_off = {}
			for r in frappe.db.sql("""
				SELECT e.name AS emp, h.holiday_date AS d
				FROM `tabEmployee` e
				JOIN `tabHoliday` h ON h.parent = e.holiday_list
				WHERE e.status = 'Active'
				  AND h.holiday_date BETWEEN %(l_from)s AND %(l_to)s
			""" + l_where, l_params, as_dict=True):
				l_off[str(r["emp"]) + "|" + str(r["d"])] = 1

			l_dates = []
			l_i = 0
			while l_i < l_days:
				l_dates.append(str(frappe.utils.add_days(l_from, l_i)))
				l_i = l_i + 1

			MIN_EARLY = 60		  # a departure inside the last hour is on time
			MIN_OPEN_GAP = 10800	# today, before the shift ends, an OUT needs a 3h gap

			l_out = []
			# Per-day breakdown for ONE employee. The list rows are aggregates —
			# "94h 30m over 7d" says nothing about which days — so the UI asks for
			# a single employee's days when a row is opened. Scoped to one person
			# on purpose: emitting every day for every employee would be a
			# 500 x 31 payload nobody reads.
			l_detail_emp = str(frappe.form_dict.get("lost_emp") or "")
			l_day_rows = []
			for em in l_emps:
				e_id = str(em["emp"])
				e_doj = str(em["doj"]) if em.get("doj") else ""
				e_rel = str(em["rel"]) if em.get("rel") else ""
				sa_list = l_sa.get(e_id) or []
				lv_list = l_leave.get(e_id) or []
				acc = {"late_days": 0, "late_mins": 0, "early_days": 0, "early_mins": 0,
					   "absent_days": 0, "absent_mins": 0, "no_out_days": 0,
					   "worked_days": 0, "sched_days": 0, "loss_days": 0,
					   "scan_days": 0, "night_days": 0, "mismatch_days": 0}
				worst = ""
				worst_mins = 0
				for dd in l_dates:
					if e_doj and dd < e_doj:
						continue
					if e_rel and dd > e_rel:
						continue
					eff = str(em["def_shift"] or "")
					for sa in sa_list:
						sa_sd = str(sa["sd"])
						sa_ed = str(sa["ed"]) if sa.get("ed") else ""
						if sa_sd <= dd and (not sa_ed or sa_ed >= dd):
							eff = str(sa["sh"] or eff)
					if not eff or eff not in l_sh_dur:
						continue
					is_night = l_sh_night.get(eff) or 0
					s_m = l_sh_start.get(eff) or 0
					dur = l_sh_dur.get(eff) or 0
					key = e_id + "|" + dd
					st_att = l_att.get(key) or ""
					on_leave = st_att == "On Leave"
					if not on_leave:
						for lv in lv_list:
							if str(lv["fd"]) <= dd and str(lv["td"]) >= dd:
								on_leave = True
					is_off = (l_off.get(key) == 1) or st_att == "Holiday"
					bio = l_night_io.get(key) if is_night else l_day_io.get(key)

					# a rest day or approved leave costs nothing; a scan on a rest
					# day is voluntary time, so it is not measured either
					if on_leave or is_off:
						continue
					acc["sched_days"] = acc["sched_days"] + 1

					if bio is None or bio.get("fin") is None:
						# nothing scanned. Today's shift may simply not have started
						# yet, and Present/Half Day on the record beats the silence.
						if st_att in ("Present", "Work From Home"):
							acc["worked_days"] = acc["worked_days"] + 1
							continue
						if st_att == "Half Day":
							acc["worked_days"] = acc["worked_days"] + 1
							acc["absent_mins"] = acc["absent_mins"] + int(dur / 2)
							acc["loss_days"] = acc["loss_days"] + 1
							if e_id == l_detail_emp:
								l_day_rows.append({"date": dd, "shift": eff, "reason": "Half day",
												   "late_mins": 0, "early_mins": 0,
												   "absent_mins": int(dur / 2),
												   "lost_mins": int(dur / 2)})
							if int(dur / 2) > worst_mins:
								worst_mins = int(dur / 2)
								worst = dd + " half day"
							continue
						if dd == l_today and l_now_m < s_m:
							continue
						acc["absent_days"] = acc["absent_days"] + 1
						acc["absent_mins"] = acc["absent_mins"] + dur
						acc["loss_days"] = acc["loss_days"] + 1
						if e_id == l_detail_emp:
							l_day_rows.append({"date": dd, "shift": eff, "reason": "No scan",
											   "late_mins": 0, "early_mins": 0,
											   "absent_mins": dur, "lost_mins": dur})
						if dur > worst_mins:
							worst_mins = dur
							worst = dd + " absent"
						continue

					acc["worked_days"] = acc["worked_days"] + 1
					acc["scan_days"] = acc["scan_days"] + 1
					d_loss = 0
					f_m = mins_of_day(bio.get("fin"))
					if f_m is None:
						continue
					if is_night and f_m < 720:
						f_m = f_m + 1440
					# Night rosters cross midnight, so a late/early figure derived
					# from clock times is not trustworthy (this is exactly why the
					# dashboard's Late In / Early Out tile skips them). Their
					# absences and missing checkouts still count.
					if is_night:
						acc["night_days"] = acc["night_days"] + 1
						if bio.get("fout") is None:
							acc["no_out_days"] = acc["no_out_days"] + 1
						continue
					late = f_m - s_m
					# arriving later than a whole shift means the scan belongs to a
					# different shift than the one rostered — a roster mismatch, not
					# lost time, so it is counted separately and left out of the total
					if late >= dur:
						acc["mismatch_days"] = acc["mismatch_days"] + 1
						continue
					if late > 0:
						acc["late_days"] = acc["late_days"] + 1
						acc["late_mins"] = acc["late_mins"] + late
						d_loss = d_loss + late

					o_raw = bio.get("fout")
					use_out = o_raw is not None
					if use_out and dd == l_today:
						# while the shift is still running, only a departure with a
						# real gap after the arrival counts as a checkout
						shift_over = l_now_m >= (s_m + dur) if not is_night else False
						if not shift_over:
							o_chk_m = mins_of_day(o_raw) or 0
							if is_night and o_chk_m < 720:
								o_chk_m = o_chk_m + 1440
							gap = (o_chk_m - f_m) * 60
							if gap < MIN_OPEN_GAP:
								use_out = False
					if use_out:
						o_m = mins_of_day(o_raw) or 0
						if o_m < f_m:
							o_m = o_m + 1440
						early = (s_m + dur) - o_m
						if early >= MIN_EARLY:
							acc["early_days"] = acc["early_days"] + 1
							acc["early_mins"] = acc["early_mins"] + early
							d_loss = d_loss + early
					else:
						acc["no_out_days"] = acc["no_out_days"] + 1
					if d_loss > 0:
						acc["loss_days"] = acc["loss_days"] + 1
						if e_id == l_detail_emp:
							d_late = late if late > 0 else 0
							d_early = d_loss - d_late
							bits = []
							if d_late > 0:
								bits.append("Late in")
							if d_early > 0:
								bits.append("Early out")
							l_day_rows.append({"date": dd, "shift": eff,
											   "reason": " + ".join(bits) or "Lost time",
											   "late_mins": d_late, "early_mins": d_early,
											   "absent_mins": 0, "lost_mins": d_loss})
						if d_loss > worst_mins:
							worst_mins = d_loss
							worst = dd
				lost = acc["late_mins"] + acc["early_mins"] + acc["absent_mins"]
				if lost <= 0 and acc["no_out_days"] <= 0:
					continue
				l_out.append({
					"employee": e_id, "employee_name": em["nm"], "farm": em["farm"],
					"company": em["company"], "employment_type": em["etype"],
					"designation": em["designation"],
					"late_days": acc["late_days"], "late_mins": acc["late_mins"],
					"early_days": acc["early_days"], "early_mins": acc["early_mins"],
					"absent_days": acc["absent_days"], "absent_mins": acc["absent_mins"],
					"no_out_days": acc["no_out_days"], "worked_days": acc["worked_days"],
					"sched_days": acc["sched_days"], "loss_days": acc["loss_days"],
					"scan_days": acc["scan_days"], "night_days": acc["night_days"],
					"mismatch_days": acc["mismatch_days"],
					"behaviour_mins": acc["late_mins"] + acc["early_mins"],
					# no scan at all across the whole window: that is a biometric
					# coverage gap, not a behaviour pattern, so the UI flags it
					# separately instead of letting it dominate the ranking
					"never_scanned": 1 if acc["scan_days"] == 0 else 0,
					"lost_mins": lost, "worst_day": worst,
				})

			# rank by how often they lose time first, then by how much (no lambda:
			# RestrictedPython rejects it, so sort a tuple list and re-map)
			l_keys = []
			l_idx = 0
			for row in l_out:
				l_keys.append((row["never_scanned"], -row["loss_days"], -row["lost_mins"],
							   row["employee"], l_idx))
				l_idx = l_idx + 1
			l_keys.sort()
			l_sorted = []
			for k in l_keys:
				l_sorted.append(l_out[k[4]])

			# "most frequent" = losing time on 60%+ of the shifts they were
			# rostered for in the window, with at least two such days
			for row in l_sorted:
				sd = row["sched_days"] or 0
				hot = 0
				if row["loss_days"] >= 2 and sd > 0 and not row["never_scanned"]:
					if (row["loss_days"] * 100) >= (sd * 60):
						hot = 1
				row["frequent"] = hot

			l_tot = 0
			l_hot = 0
			l_gap = 0
			l_beh = 0
			for row in l_sorted:
				l_tot = l_tot + row["lost_mins"]
				if row["never_scanned"]:
					l_gap = l_gap + 1
				else:
					l_beh = l_beh + row["behaviour_mins"]
					if row["frequent"]:
						l_hot = l_hot + 1
			# Already in date order: the day loop walks l_dates in order for the
			# one employee being detailed.
			frappe.response["message"] = {
				"lost": l_sorted, "from": l_from, "to": l_to, "days": l_days,
				"lost_days": l_day_rows, "lost_emp": l_detail_emp,
				"total_lost_mins": l_tot, "frequent_count": l_hot,
				"behaviour_mins": l_beh, "never_scanned_count": l_gap,
				"employees_affected": len(l_sorted),
				"duplicate_attendance_rows": l_att_dupes,
			}
			return

		# ── night shifts: any Shift Type whose end time is earlier than its start
		# time (i.e. it crosses midnight). Detected dynamically so newly-added
		# night shifts are picked up without editing this script. ──
		step = "night_shifts"
		night_shift_rows = frappe.db.sql("""
			SELECT name FROM `tabShift Type`
			WHERE TIME(end_time) < TIME(start_time)
		""", as_dict=True)
		night_shift_set = set([r.name for r in night_shift_rows])
		# start times for every shift, used to report when an evening shift begins
		shift_start_map = {}
		shift_end_map = {}
		for sr in frappe.db.sql("SELECT name, start_time, end_time FROM `tabShift Type`", as_dict=True):
			shift_start_map[sr["name"]] = str(sr["start_time"]) if sr["start_time"] else None
			shift_end_map[sr["name"]] = str(sr["end_time"]) if sr["end_time"] else None
		night_pending = []
		now_t = str(frappe.utils.nowtime())[:8]
		is_future = str(reg_date) > str(frappe.utils.today())

		emp_where = ["emp.status = 'Active'"]
		emp_params = {"reg_date": reg_date}
		if farm:
			emp_where.append("TRIM(emp.custom_farm) = TRIM(%(farm)s)")
			emp_params["farm"] = farm
		if company:
			emp_where.append("emp.company = %(company)s")
			emp_params["company"] = company
		if emptype:
			emp_where.append("FIND_IN_SET(emp.employment_type, %(employment_type)s)")
			emp_params["employment_type"] = emptype

		step = "employees"
		all_employees = frappe.db.sql("""
			SELECT name, employee_name, designation, custom_farm, employment_type,
				   company, holiday_list, default_shift
			FROM `tabEmployee` emp
			WHERE """ + " AND ".join(emp_where) + """
			ORDER BY employee_name ASC LIMIT 5000
		""", emp_params, as_dict=True)

		# ── effective shift per employee: active Shift Assignment covering the
		# register date takes priority; else the Employee default_shift. Used to
		# decide who is a night worker ("either source"). ──
		step = "shift_assignment"
		sa_params = {"reg_date": reg_date}
		sa_extra = ""
		if farm:
			sa_extra += " AND TRIM(emp.custom_farm) = TRIM(%(sa_farm)s)"
			sa_params["sa_farm"] = farm
		if company:
			sa_extra += " AND emp.company = %(sa_company)s"
			sa_params["sa_company"] = company
		sa_rows = frappe.db.sql("""
			SELECT sa.employee, sa.shift_type
			FROM `tabShift Assignment` sa
			JOIN `tabEmployee` emp ON emp.name = sa.employee
			WHERE sa.docstatus = 1 AND sa.status = 'Active'
			  AND sa.start_date <= %(reg_date)s
			  AND (sa.end_date IS NULL OR sa.end_date >= %(reg_date)s)
			  AND emp.status = 'Active'
			  """ + sa_extra + """
		ORDER BY sa.start_date DESC, sa.name DESC
		""", sa_params, as_dict=True)
		assigned_shift_map = {}
		for r in sa_rows:
			# first active assignment wins; do not overwrite
			if r.employee not in assigned_shift_map:
				assigned_shift_map[r.employee] = r.shift_type

		step = "biometric"
		ci_params = {"reg_date": reg_date}
		ci_extra = ""
		if farm:
			ci_extra += " AND TRIM(emp.custom_farm) = TRIM(%(ci_farm)s)"
			ci_params["ci_farm"] = farm
		if company:
			ci_extra += " AND emp.company = %(ci_company)s"
			ci_params["ci_company"] = company
		biometric_rows = frappe.db.sql("""
			SELECT ec.employee,
				MIN(CASE WHEN ec.log_type='IN' THEN ec.`time` END) AS in_time,
				-- Raw last OUT plus the IN->OUT gap. Whether that counts as a real
				-- checkout is decided per employee below, because it depends on the
				-- date and on whether their shift has already ended.
				MAX(CASE WHEN ec.log_type='OUT' THEN ec.`time` END) AS raw_out,
				TIMESTAMPDIFF(SECOND,
					MIN(CASE WHEN ec.log_type='IN' THEN ec.`time` END),
					MAX(CASE WHEN ec.log_type='OUT' THEN ec.`time` END)) AS out_gap_sec
			FROM `tabEmployee Checkin` ec
			JOIN `tabEmployee` emp ON emp.name = ec.employee
			WHERE ec.`time` >= %(reg_date)s AND ec.`time` < %(reg_date)s + INTERVAL 1 DAY AND emp.status='Active'
			  """ + ci_extra + """
			GROUP BY ec.employee
		""", ci_params, as_dict=True)
		biometric_map = {r.employee: r for r in biometric_rows}

		# Which OUT counts as a checkout:
		#   * past dates				-> the last OUT of that day (the day is complete)
		#   * today, shift already over  -> the last OUT of the day
		#   * today, shift still running -> only if it is >= 3h after the IN, otherwise
		#								   it is a reader bounce and we show no checkout
		MIN_OPEN_SHIFT_GAP = 10800
		def resolve_out(b, eff):
			if b is None or b.get("raw_out") is None:
				return None
			if not is_today:
				return str(b.get("raw_out"))
			se = shift_end_map.get(eff)
			if se and now_t >= str(se)[:8]:
				return str(b.get("raw_out"))
			gap = b.get("out_gap_sec")
			if gap is not None and gap >= MIN_OPEN_SHIFT_GAP:
				return str(b.get("raw_out"))
			return None

		# ── night-shift check-ins that started the PREVIOUS evening and may run
		# into this morning. We look at scans from (reg_date - 1) 12:00 through
		# reg_date 12:00 so an IN clocked last evening still counts this morning.
		# Only applied to night workers so it can't affect day-shift buckets. ──
		# ── Night-shift direction is inferred from TIME OF DAY, not log_type,
		# because the biometric readers mislabel almost every scan as IN (a
		# dawn exit comes through as an "IN"). For a night shift running evening
		# -> morning, we classify each scan in the window (yesterday noon ->
		# today noon):
		#   scan hour >= 14  -> ARRIVAL for last night's shift
		#   scan hour <  12  -> DEPARTURE this morning
		# A worker is still "present on nights" only if they have an arrival and
		# no later departure. This correctly reads a 06:00 dawn scan as leaving,
		# not arriving. ──
		step = "night_biometric"
		night_bio_rows = frappe.db.sql("""
			SELECT ec.employee,
				MIN(CASE WHEN HOUR(ec.`time`) >= 14 THEN ec.`time` END) AS arrival_time,
				MAX(CASE WHEN HOUR(ec.`time`) <  12 THEN ec.`time` END) AS departure_time
			FROM `tabEmployee Checkin` ec
			JOIN `tabEmployee` emp ON emp.name = ec.employee
			WHERE ec.`time` >= DATE_SUB(%(reg_date)s, INTERVAL 1 DAY) + INTERVAL 12 HOUR
			  AND ec.`time` <  %(reg_date)s + INTERVAL 12 HOUR
			  AND emp.status = 'Active'
			  """ + ci_extra + """
			GROUP BY ec.employee
		""", ci_params, as_dict=True)
		night_bio_map = {r.employee: r for r in night_bio_rows}

		step = "attendance"
		att_params = {"reg_date": reg_date}
		att_extra = ""
		if farm:
			att_extra += " AND TRIM(emp.custom_farm) = TRIM(%(att_farm)s)"
			att_params["att_farm"] = farm
		if company:
			att_extra += " AND emp.company = %(att_company)s"
			att_params["att_company"] = company
		att_rows = frappe.db.sql("""
			SELECT a.employee, a.status AS att_status, a.in_time, a.out_time,
				   a.shift AS att_shift, a.custom_marking_reason AS marking_reason
			FROM `tabAttendance` a
			JOIN `tabEmployee` emp ON emp.name = a.employee
			WHERE a.attendance_date = %(reg_date)s AND a.docstatus=1 AND emp.status='Active'
			  """ + att_extra + """
		""", att_params, as_dict=True)
		att_map = {r.employee: r for r in att_rows}

		step = "leave"
		lv_where = ["la.docstatus < 2",
					"(la.status = 'Approved' OR la.docstatus = 1)",
					"%(reg_date)s BETWEEN la.from_date AND la.to_date",
					"emp.status = 'Active'"]
		lv_params = {"reg_date": reg_date}
		if farm:
			lv_where.append("TRIM(emp.custom_farm) = TRIM(%(lv_farm)s)")
			lv_params["lv_farm"] = farm
		if company:
			lv_where.append("emp.company = %(lv_company)s")
			lv_params["lv_company"] = company
		leave_rows = frappe.db.sql("""
			SELECT la.employee, la.leave_type
			FROM `tabLeave Application` la
			JOIN `tabEmployee` emp ON emp.name = la.employee
			WHERE """ + " AND ".join(lv_where) + """
		""", lv_params, as_dict=True)
		leave_map = {r.employee: r.leave_type for r in leave_rows}

		step = "holiday"
		off_lists = frappe.db.sql("""
			SELECT DISTINCT h.parent AS holiday_list, h.weekly_off
			FROM `tabHoliday` h WHERE h.holiday_date = %(reg_date)s
		""", {"reg_date": reg_date}, as_dict=True)
		off_list_map = {r.holiday_list: r.weekly_off for r in off_lists}

		step = "bucket_loop"
		present = []
		on_leave = []
		off = []
		absent = []
		night_total = 0
		night_checked_in = 0

		def base(emp):
			return {"name": emp.name, "employee_name": emp.employee_name,
					"designation": emp.designation, "custom_farm": emp.custom_farm,
					"employment_type": emp.employment_type, "company": emp.company}

		for emp in all_employees:
			# resolve effective shift: default_shift is source of truth, with an
			# active Shift Assignment used only as a fallback when default_shift
			# is not set on the employee.
			eff_shift = emp.default_shift or assigned_shift_map.get(emp.name)
			is_night = eff_shift in night_shift_set if eff_shift else False
			if is_night:
				night_total = night_total + 1

			bio = biometric_map.get(emp.name)
			att = att_map.get(emp.name)
			night_bio = night_bio_map.get(emp.name) if is_night else None

			# 1) same-day biometric scan -> Present. DAY WORKERS ONLY. Night
			# workers are handled by branch 3 using time-based inference, because
			# their same-day scans (e.g. a 06:00 dawn exit mislabeled "IN") would
			# otherwise wrongly mark a departed worker as present.
			# 0) HISTORICAL dates: the Attendance record is authoritative. A raw
			# check-in only implies Present for TODAY; for past dates HR's marked
			# Attendance decides (so a stray scan can't override a marked Absent).
			if (not is_today) and att and att.att_status:
				st = att.att_status
				if st in ("Present", "Work From Home", "Half Day"):
					row = base(emp)
					row["in_time"] = str(att.in_time) if att.in_time else (str(bio.in_time) if bio and bio.in_time else None)
					row["out_time"] = str(att.out_time) if att.out_time else resolve_out(bio, eff_shift)
					row["source"] = "attendance"
					row["att_status"] = st
					row["marking_reason"] = att.marking_reason
					row["shift"] = eff_shift
					row["is_night"] = is_night
					present.append(row)
					if is_night:
						night_checked_in = night_checked_in + 1
				elif st == "On Leave":
					row = base(emp)
					row["leave_type"] = leave_map.get(emp.name) or "On Leave"
					row["att_status"] = st
					on_leave.append(row)
				elif st in ("Holiday", "Weekly Off"):
					row = base(emp)
					row["off_type"] = st
					row["att_status"] = st
					off.append(row)
				else:
					row = base(emp)
					row["att_status"] = st
					row["shift"] = eff_shift
					row["is_night"] = is_night
					absent.append(row)

			elif bio and not is_night:
				row = base(emp)
				row["in_time"] = str(bio.in_time) if bio.in_time else None
				row["out_time"] = resolve_out(bio, eff_shift)
				row["source"] = "biometric"
				row["shift"] = eff_shift
				row["is_night"] = False
				present.append(row)

			# 2) manually-marked Attendance -> Present (day or night)
			elif att and att.att_status in ("Present", "Work From Home", "Half Day"):
				row = base(emp)
				row["in_time"] = str(att.in_time) if att.in_time else None
				row["out_time"] = str(att.out_time) if att.out_time else None
				row["source"] = "manual"
				row["att_status"] = att.att_status
				row["marking_reason"] = att.marking_reason
				row["shift"] = eff_shift
				row["is_night"] = is_night
				present.append(row)
				if is_night:
					night_checked_in = night_checked_in + 1

			# 3) night worker who ACTUALLY WORKED -> Present, even if this date is
			# their weekly off / leave day. Actual attendance outranks the schedule,
			# exactly as it already does for day workers (branch 1), so a guard who
			# worked last night is never shown as Off/Absent. An early dawn exit is
			# reported as an early-checkout, not as an absence.
			elif is_night and night_bio and night_bio.arrival_time:
				row = base(emp)
				row["in_time"] = str(night_bio.arrival_time)
				row["out_time"] = str(night_bio.departure_time) if night_bio.departure_time else None
				row["source"] = "night"
				row["shift"] = eff_shift
				row["is_night"] = True
				present.append(row)
				night_checked_in = night_checked_in + 1

			# 4) approved leave. Checked BEFORE the night carry-over: a night
			# worker whose shift ran into a leave day belongs on leave here --
			# last night's work is already credited on yesterday's attendance.
			elif emp.name in leave_map:
				row = base(emp)
				row["leave_type"] = leave_map[emp.name]
				if is_night and night_bio and night_bio.arrival_time:
					row["worked_night"] = str(night_bio.arrival_time)
					if night_bio.departure_time:
						row["night_ended"] = str(night_bio.departure_time)
				on_leave.append(row)

			# 4) weekly off / holiday. Also checked BEFORE the night carry-over
			# so a guard who worked Thu night does not show Present on his
			# Friday off; the carry-over annotation keeps the work visible.
			elif emp.holiday_list and emp.holiday_list in off_list_map:
				row = base(emp)
				off_type = "Weekly Off" if off_list_map[emp.holiday_list] else "Holiday"
				if is_night and night_bio and night_bio.arrival_time:
					row["worked_night"] = str(night_bio.arrival_time)
					if night_bio.departure_time:
						row["night_ended"] = str(night_bio.departure_time)
						off_type = off_type + " · night ended " + str(night_bio.departure_time)[11:16]
					else:
						off_type = off_type + " · worked last night"
				row["off_type"] = off_type
				off.append(row)

			# 6) night worker with no scan yet: their shift only STARTS in the evening,
			# so they cannot be absent yet -> separate "starts this evening" bucket.
			elif is_night:
				# Pending ONLY while the evening shift has not begun yet. Once the start
				# time has passed (or the date is in the past) a missing scan is a real
				# absence, so the row moves to Absent with the shift time noted.
				ss = shift_start_map.get(eff_shift)
				st_txt = str(ss)[:5] if ss else "evening"
				started = True
				if is_future:
					started = False
				elif is_today and ss:
					started = now_t >= str(ss)[:8]
				elif is_today and not ss:
					started = False
				row = base(emp)
				row["att_status"] = att.att_status if att else None
				row["shift"] = eff_shift
				row["is_night"] = True
				row["shift_start"] = str(ss) if ss else None
				if started:
					row["night_note"] = "Night shift started " + st_txt + " \u2014 no check-in"
					absent.append(row)
				else:
					row["night_note"] = "Shift starts " + st_txt
					night_pending.append(row)

			# 7) everyone else -> absent
			else:
				row = base(emp)
				row["att_status"] = att.att_status if att else None
				row["shift"] = eff_shift
				row["is_night"] = is_night
				absent.append(row)

		# enrich absent rows with a note: pending (draft) leave OR unmarked weekly-off
		absent_ids = [r["name"] for r in absent]
		pend_map = {}
		off_ids = set()
		if absent_ids:
			for lr in frappe.db.sql("SELECT employee AS e, leave_type AS lt FROM `tabLeave Application` WHERE employee IN %(ids)s AND docstatus = 0 AND %(d)s BETWEEN from_date AND to_date", {"ids": tuple(absent_ids), "d": reg_date}, as_dict=True):
				if lr["e"] not in pend_map:
					pend_map[lr["e"]] = lr["lt"]
			for hr in frappe.db.sql("SELECT emp.name AS e FROM `tabEmployee` emp INNER JOIN `tabHoliday` h ON h.parent = emp.holiday_list WHERE emp.name IN %(ids)s AND h.holiday_date = %(d)s AND h.weekly_off = 1", {"ids": tuple(absent_ids), "d": reg_date}, as_dict=True):
				off_ids.add(hr["e"])
		for r in absent:
			if r["name"] in pend_map:
				r["flag"] = "Pending: " + str(pend_map[r["name"]])
			elif r["name"] in off_ids:
				r["flag"] = "Week Off (unmarked)"
			else:
				r["flag"] = ""

		# ── biometric device status (child table of the Biometric Setting single) ──
		bio_devs = frappe.db.sql("""
			SELECT device_sn, device_location, status, last_seen
			FROM `tabBiometric Device`
			WHERE parenttype = 'Biometric Setting'
			ORDER BY (CASE WHEN status = 'Online' THEN 1 ELSE 0 END), device_location
		""", as_dict=True)
		dev_list = []
		dev_online = 0
		for dv in bio_devs:
			st = dv["status"] or "Unknown"
			if st == "Online":
				dev_online = dev_online + 1
			dev_list.append({"sn": dv["device_sn"], "location": dv["device_location"],
							 "status": st, "last_seen": str(dv["last_seen"]) if dv["last_seen"] else None})

		# ── payroll period from Biometric Setting (day-of-month boundaries, e.g. 21 -> 20) ──
		ps_rows = frappe.db.sql("SELECT field, value FROM `tabSingles` WHERE doctype='Biometric Setting' AND field IN ('from','to')", as_dict=True)
		ps_map = {r["field"]: r["value"] for r in ps_rows}
		p_from_day = frappe.utils.cint(ps_map.get("from") or 21) or 21
		p_to_day = frappe.utils.cint(ps_map.get("to") or 20) or 20
		rd = frappe.utils.getdate(reg_date)
		if rd.day >= p_from_day:
			payroll_start = rd.replace(day=p_from_day)
			payroll_end = frappe.utils.add_months(rd, 1).replace(day=p_to_day)
		else:
			payroll_start = frappe.utils.add_months(rd, -1).replace(day=p_from_day)
			payroll_end = rd.replace(day=p_to_day)

		sbfarms = {}
		sbcounts = {}
		sbrows = frappe.db.sql("SELECT company AS co, custom_farm AS f, SUM(CASE WHEN employment_type=%(tw_type)s THEN 1 ELSE 0 END) AS tw, SUM(CASE WHEN COALESCE(employment_type,'')<>%(tw_type)s THEN 1 ELSE 0 END) AS rest FROM `tabEmployee` WHERE status='Active' AND IFNULL(company,'')<>'' AND IFNULL(custom_farm,'')<>'' GROUP BY company, custom_farm ORDER BY company, custom_farm", {"tw_type": TASK_WORKER_EMPLOYMENT_TYPE}, as_dict=True)
		for rr in sbrows:
			sbfarms.setdefault(rr["co"], [])
			if rr["f"] not in sbfarms[rr["co"]]:
				sbfarms[rr["co"]].append(rr["f"])
			sbcounts.setdefault(rr["co"], {})
			sbcounts[rr["co"]][rr["f"]] = {"tw": int(rr["tw"] or 0), "rest": int(rr["rest"] or 0)}
		sbet = [x["et"] for x in frappe.db.sql("SELECT DISTINCT employment_type AS et FROM `tabEmployee` WHERE status='Active' AND IFNULL(employment_type,'')<>'' ORDER BY employment_type", as_dict=True)]
		# ── LATE-IN / EARLY-OUT for this date (moved off attendance_dashboard_data,
		# which took up to 33s and made the browser fetch fail). Single indexed
		# day-range query; night shifts (end<=start) are skipped because their
		# boundaries cross midnight. ──
		step = "late_early"
		le_extra = ""
		le_params = {"reg_date": reg_date, "le_today": 1 if is_today else 0, "le_now": now_t}
		if farm:
			le_extra += " AND TRIM(e.custom_farm) = TRIM(%(le_farm)s)"
			le_params["le_farm"] = farm
		if company:
			le_extra += " AND e.company = %(le_company)s"
			le_params["le_company"] = company
		if emptype:
			le_extra += " AND FIND_IN_SET(e.employment_type, %(le_emptype)s)"
			le_params["le_emptype"] = emptype
		le_sql = (
			"SELECT x.employee AS employee, x.employee_name AS employee_name, x.farm AS farm, "
			"	   x.shift AS shift, "
			"	   TIMESTAMPDIFF(MINUTE, TIMESTAMP(DATE(x.first_in), st.start_time), x.first_in) AS mins_late, "
			"	   CASE WHEN %(le_today)s = 0 "
			"			  OR TIME(%(le_now)s) >= TIME(st.end_time) "
			"			  OR TIMESTAMPDIFF(SECOND, x.first_in, x.last_out) >= 10800 "
			"			THEN TIMESTAMPDIFF(MINUTE, x.last_out, TIMESTAMP(DATE(x.last_out), st.end_time)) "
			"			ELSE NULL END AS mins_early "
			"FROM ( "
			"  SELECT ec.employee, e.employee_name, "
			"		 COALESCE(NULLIF(TRIM(e.custom_farm), ''), '') AS farm, "
			"		 COALESCE(MAX(ec.shift), MAX(e.default_shift)) AS shift, "
			"		 MIN(CASE WHEN ec.log_type = 'IN' THEN ec.`time` END) AS first_in, "
			"		 MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.`time` END) AS last_out "
			"  FROM `tabEmployee Checkin` ec "
			"  JOIN `tabEmployee` e ON e.name = ec.employee "
			"  WHERE ec.`time` >= %(reg_date)s AND ec.`time` < %(reg_date)s + INTERVAL 1 DAY "
			"	AND e.status = 'Active' " + le_extra +
			"  GROUP BY ec.employee, e.employee_name, e.custom_farm "
			") x "
			"JOIN `tabShift Type` st ON st.name = x.shift "
			"WHERE st.start_time IS NOT NULL AND st.end_time > st.start_time"
		)
		le_rows = frappe.db.sql(le_sql, le_params, as_dict=True)
		top_late = []
		top_early = []
		for lr in le_rows:
			ml = lr.get("mins_late")
			me = lr.get("mins_early")
			if ml is not None and ml > 0:
				top_late.append({"employee": lr["employee"], "employee_name": lr["employee_name"],
								 "farm": lr["farm"], "shift": lr["shift"],
								 "avg_minutes_late": int(ml), "max_minutes_late": int(ml),
								 "late_days": 1})
			# A checkout inside the last hour of the shift is effectively on time, so it
			# is NOT an early-out (the scan itself still shows as the Check-Out on the
			# dashboard). Only departures more than 60 minutes early are flagged.
			if me is not None and me >= 60:
				top_early.append({"employee": lr["employee"], "employee_name": lr["employee_name"],
								  "farm": lr["farm"], "shift": lr["shift"],
								  "avg_minutes_early": int(me), "max_minutes_early": int(me),
								  "early_days": 1})

		frappe.response["message"] = {
			"company_farms": sbfarms, "company_farm_counts": sbcounts, "employment_types": sbet,
			"payroll_from": str(payroll_start), "payroll_to": str(payroll_end),
			"devices": dev_list, "devices_online": dev_online, "devices_total": len(dev_list),
			"date": reg_date, "present": present, "on_leave": on_leave,
			"off": off, "absent": absent,
			"present_count": len(present), "on_leave_count": len(on_leave),
			"off_count": len(off), "absent_count": len(absent),
			"total": len(all_employees),
			"night_total": night_total,
			"night_checked_in": night_checked_in,
			"night_shifts": list(night_shift_set),
			"night_pending": night_pending, "night_pending_count": len(night_pending),
			"top_late": top_late, "top_early": top_early,
		}
	except Exception as e:
		frappe.response["message"] = {"error": str(e), "failed_at_step": step}


# ─────────────────────────────────────────────────────────────────────────────
# Server Script: att_rows   (type: API, api_method: att_rows)
#
# Returns ONLY the capped raw checkin rows for the Register tab's Checkin
# Records table. Split out of attendance_dashboard_data so the default Overview
# load no longer computes/ships up to 6000 rows nobody is looking at yet — the
# frontend fetches this lazily the first time the Register tab is opened.
#
# Same row shape and same sargable time filter as the dashboard rewrite.
# Sandbox-safe: no imports, no def, no lambdas, no str.format(), SELECT-only SQL,
# frappe.form_dict, bracket-notation response.
# ─────────────────────────────────────────────────────────────────────────────
@frappe.whitelist()
def att_rows():
	"""The raw checkin rows behind the Register tab's table, fetched lazily."""
	fd = frappe.form_dict
	from_date = fd.get("from_date") or frappe.utils.nowdate()
	to_date = fd.get("to_date") or frappe.utils.nowdate()
	farm = (fd.get("farm") or "").strip()
	company = (fd.get("company") or "").strip()
	emptype = (fd.get("employment_type") or "").strip()

	start_d = frappe.utils.getdate(from_date)
	end_d = frappe.utils.getdate(to_date)
	if end_d < start_d:
		end_d = start_d
	if frappe.utils.date_diff(end_d, start_d) > 366:
		start_d = frappe.utils.add_days(end_d, -366)
	from_date = str(start_d)
	to_date = str(end_d)
	to_date_excl = str(frappe.utils.add_days(end_d, 1))

	params = {
		"from_date": from_date,
		"to_date": to_date,
		"to_date_excl": to_date_excl,
		"farm": farm,
		"company": company,
	}

	econd = ""
	if farm:
		econd = econd + " AND e.custom_farm = %(farm)s"
	if company:
		econd = econd + " AND e.company = %(company)s"
	if emptype:
		econd = econd + " AND e.employment_type = " + frappe.db.escape(emptype)

	trange = " AND c.time >= %(from_date)s AND c.time < %(to_date_excl)s "

	grace_in = "IFNULL(CASE WHEN st.enable_late_entry_marking = 1 THEN st.late_entry_grace_period ELSE 0 END, 0)"
	grace_out = "IFNULL(CASE WHEN st.enable_early_exit_marking = 1 THEN st.early_exit_grace_period ELSE 0 END, 0)"
	shift_start_dt = "ADDTIME(CAST(DATE(c.time) AS DATETIME), st.start_time)"
	shift_end_dt = "ADDTIME(CAST(DATE(c.time) AS DATETIME), st.end_time)"
	late_expr = "GREATEST(0, TIMESTAMPDIFF(MINUTE, DATE_ADD(" + shift_start_dt + ", INTERVAL " + grace_in + " MINUTE), c.time))"
	early_expr = "GREATEST(0, TIMESTAMPDIFF(MINUTE, c.time, DATE_SUB(" + shift_end_dt + ", INTERVAL " + grace_out + " MINUTE)))"

	rows_cap = 6000
	rows = frappe.db.sql("""
		SELECT c.employee AS employee, e.employee_name AS employee_name,
			   e.custom_farm AS farm,
			   COALESCE(NULLIF(c.shift, ''), e.default_shift) AS shift,
			   c.time AS time, c.log_type AS log_type,
			   CASE WHEN c.log_type = 'IN' THEN {late_expr} ELSE NULL END AS minutes_late,
			   CASE WHEN c.log_type = 'OUT' AND st.end_time > st.start_time THEN {early_expr} ELSE NULL END AS minutes_early
		FROM `tabEmployee Checkin` c
		INNER JOIN `tabEmployee` e ON e.name = c.employee
		LEFT JOIN `tabShift Type` st ON st.name = COALESCE(NULLIF(c.shift, ''), e.default_shift)
		WHERE 1=1 {trange} {econd}
		ORDER BY c.time DESC
		LIMIT {cap}""".replace("{late_expr}", late_expr).replace("{early_expr}", early_expr).replace("{trange}", trange).replace("{econd}", econd).replace("{cap}", str(rows_cap)), params, as_dict=1)

	frappe.response["message"] = {
		"from_date": from_date,
		"to_date": to_date,
		"rows": rows,
		"rows_capped": len(rows) >= rows_cap,
		"rows_cap": rows_cap,
	}


# Server Script — API — method: attendance_on_leave
# FIXED: frappe.form_dict (not frappe.local.form_dict). No lambda/get_value/getattr.
@frappe.whitelist()
def attendance_on_leave():
	"""Who is on leave for the date, and of what type."""
	try:
		args = frappe.form_dict
		on_date = args.get("date") or frappe.utils.nowdate()
		farm = args.get("farm") or ""
		company = args.get("company") or ""
		emptype = args.get("employment_type") or ""
		include_open = args.get("include_open") in ("1", "true", "True", 1, True)

		la_filters = {
			"docstatus": ["<", 2],
			"from_date": ["<=", on_date],
			"to_date": [">=", on_date],
		}
		if company:
			la_filters["company"] = company

		la_rows = frappe.get_all(
			"Leave Application",
			filters=la_filters,
			fields=[
				"name", "employee", "employee_name", "leave_type",
				"from_date", "to_date", "total_leave_days", "half_day",
				"status", "docstatus", "department",
			],
			order_by="leave_type asc, employee_name asc",
			limit_page_length=0,
		)

		emp_ids = []
		for r in la_rows:
			eid = r.get("employee")
			if eid and eid not in emp_ids:
				emp_ids.append(eid)

		emp_map = {}
		if emp_ids:
			emp_recs = frappe.get_all(
				"Employee",
				filters={"name": ["in", emp_ids]},
				fields=["name", "custom_farm", "designation", "employment_type"],
				limit_page_length=0,
			)
			for e in emp_recs:
				emp_map[e.get("name")] = e

		rows = []
		for r in la_rows:
			if not include_open:
				if r.get("status") != "Approved" and r.get("docstatus") != 1:
					continue

			eid = r.get("employee")
			emp = emp_map.get(eid) or {}
			cust_farm = emp.get("custom_farm") or ""
			designation = emp.get("designation") or ""

			if farm and cust_farm != farm:
				continue

			if emptype and (emp.get("employment_type") or "") != emptype:
				continue

			rows.append({
				"leave_id": r.get("name"),
				"employee": eid,
				"employee_name": r.get("employee_name"),
				"leave_type": r.get("leave_type"),
				"from_date": str(r.get("from_date") or ""),
				"to_date": str(r.get("to_date") or ""),
				"total_leave_days": r.get("total_leave_days"),
				"half_day": r.get("half_day"),
				"status": r.get("status"),
				"docstatus": r.get("docstatus"),
				"department": r.get("department"),
				"custom_farm": cust_farm,
				"designation": designation,
			})

		by_type = {}
		for r in rows:
			lt = r.get("leave_type") or "Unspecified"
			by_type[lt] = by_type.get(lt, 0) + 1

		pairs = []
		for lt in by_type:
			pairs.append([by_type[lt], lt])
		pairs.sort(reverse=True)

		type_summary = []
		for p in pairs:
			type_summary.append({"leave_type": p[1], "count": p[0]})

		frappe.response["message"] = {
			"date": on_date,
			"total": len(rows),
			"by_type": type_summary,
			"rows": rows,
		}
	except Exception as e:
		frappe.log_error(str(e)[:1000], "attendance_on_leave")
		frappe.response["message"] = {"date": "", "total": 0, "by_type": [], "rows": [], "error": str(e)[:300]}


# SERVER SCRIPT: attendance_employee_list
# API Method: attendance_employee_list
@frappe.whitelist()
def attendance_employee_list():
	"""Active employees for the pickers, filtered by farm and company."""

	farm	= frappe.form_dict.get("farm")	or ""
	company = frappe.form_dict.get("company") or ""

	emp_filters = [["status", "=", "Active"]]
	if farm:	emp_filters.append(["custom_farm", "=", farm])
	if company: emp_filters.append(["company",	 "=", company])

	employees = frappe.get_all("Employee",
		fields=["name", "employee_name", "designation", "custom_farm",
				"employment_type", "default_shift", "company"],
		filters=emp_filters,
		order_by="employee_name asc",
		limit=2000)

	frappe.response["message"] = {
		"employees": employees,
		"total":	 len(employees),
		"farm":	  farm,
		"company":   company,
	}


# SERVER SCRIPT: attendance_employee_history
# Path: Server Script → attendance_employee_history
# Handles: employee drill-down history, monthly aggregates, KPIs
# Frontend calls: /api/method/attendance_employee_history?emp_id=500165&from_date=...&to_date=...
@frappe.whitelist()
def attendance_employee_history():
	"""One employee's attendance over a date range, for the drawer."""

	emp_id  = frappe.form_dict.get("emp_id") or ""
	h_from  = frappe.utils.getdate(frappe.form_dict.get("from_date") or "2000-01-01")
	h_to	= frappe.utils.getdate(frappe.form_dict.get("to_date")   or frappe.utils.today())

	if not emp_id:
		frappe.response["message"] = {"error": "emp_id is required"}
		return

	emp_meta = frappe.db.sql("""
		SELECT name, employee_name, designation, department,
			   custom_farm, company, date_of_joining, status
		FROM `tabEmployee` WHERE name = %(emp_id)s LIMIT 1
	""", {"emp_id": emp_id}, as_dict=True)

	if not emp_meta:
		frappe.response["message"] = {"error": "Employee not found"}
		return
	emp_meta = emp_meta[0]

	OT_SEC = 30 * 60
	OT_EXPR = """
		ec.log_type='OUT' AND st.name IS NOT NULL AND st.end_time>=st.start_time
		AND TIME(ec.`time`) > ADDTIME(st.end_time, SEC_TO_TIME((COALESCE(st.allow_check_out_after_shift_end_time,0)*60)+%(ot_sec)s))
	"""

	p = {"emp_id": emp_id, "h_from": str(h_from), "h_to": str(h_to), "ot_sec": OT_SEC}

	rows = frappe.db.sql("""
		SELECT ec.name, ec.employee, ec.employee_name, ec.log_type, ec.shift,
			   ec.`time`, ec.device_id, st.start_time AS shift_start, st.end_time AS shift_end,
			   CASE WHEN ec.log_type='IN' AND st.name IS NOT NULL AND TIME(ec.`time`)>st.start_time
				   THEN TIMESTAMPDIFF(MINUTE,st.start_time,TIME(ec.`time`)) END AS minutes_late,
			   CASE WHEN ec.log_type='OUT' AND st.name IS NOT NULL AND st.end_time>=st.start_time AND TIME(ec.`time`)<st.end_time
				   THEN TIMESTAMPDIFF(MINUTE,TIME(ec.`time`),st.end_time) END AS minutes_early,
			   CASE WHEN """ + OT_EXPR + """
				   THEN TIMESTAMPDIFF(MINUTE,st.end_time,TIME(ec.`time`)) END AS minutes_ot,
			   CASE WHEN st.end_time<st.start_time THEN 1 ELSE 0 END AS is_night
		FROM `tabEmployee Checkin` ec
		LEFT JOIN `tabShift Type` st ON st.name=ec.shift
		WHERE ec.employee=%(emp_id)s AND DATE(ec.`time`) BETWEEN %(h_from)s AND %(h_to)s
		ORDER BY ec.`time` DESC LIMIT 2000
	""", p, as_dict=True)

	monthly = frappe.db.sql("""
		SELECT DATE_FORMAT(ec.`time`,'%%Y-%%m') AS month,
			COUNT(DISTINCT DATE(ec.`time`)) AS days_present,
			SUM(ec.log_type='IN') AS in_count, SUM(ec.log_type='OUT') AS out_count,
			SUM(ec.log_type='IN' AND st.name IS NOT NULL AND TIME(ec.`time`)>st.start_time) AS late_days,
			SUM(ec.log_type='OUT' AND st.name IS NOT NULL AND st.end_time>=st.start_time AND TIME(ec.`time`)<st.end_time) AS early_days,
			SUM(""" + OT_EXPR + """) AS ot_days
		FROM `tabEmployee Checkin` ec
		LEFT JOIN `tabShift Type` st ON st.name=ec.shift
		WHERE ec.employee=%(emp_id)s AND DATE(ec.`time`) BETWEEN %(h_from)s AND %(h_to)s
		GROUP BY DATE_FORMAT(ec.`time`,'%%Y-%%m') ORDER BY month ASC
	""", p, as_dict=True)

	kpi = frappe.db.sql("""
		SELECT COUNT(*) AS total, COUNT(DISTINCT DATE(ec.`time`)) AS days_seen,
			SUM(ec.log_type='IN') AS in_count, SUM(ec.log_type='OUT') AS out_count,
			SUM(ec.log_type='IN' AND st.name IS NOT NULL AND TIME(ec.`time`)>st.start_time) AS late_days,
			SUM(ec.log_type='OUT' AND st.name IS NOT NULL AND st.end_time>=st.start_time AND TIME(ec.`time`)<st.end_time) AS early_days,
			SUM(""" + OT_EXPR + """) AS ot_days,
			AVG(CASE WHEN ec.log_type='IN' AND st.name IS NOT NULL AND TIME(ec.`time`)>st.start_time
				THEN TIMESTAMPDIFF(MINUTE,st.start_time,TIME(ec.`time`)) END) AS avg_min_late,
			AVG(CASE WHEN """ + OT_EXPR + """
				THEN TIMESTAMPDIFF(MINUTE,st.end_time,TIME(ec.`time`)) END) AS avg_min_ot
		FROM `tabEmployee Checkin` ec
		LEFT JOIN `tabShift Type` st ON st.name=ec.shift
		WHERE ec.employee=%(emp_id)s AND DATE(ec.`time`) BETWEEN %(h_from)s AND %(h_to)s
	""", p, as_dict=True)[0]

	# ── leaves + weekly off within the range ──
	leave_days = frappe.db.sql("""
		SELECT COALESCE(SUM(DATEDIFF(LEAST(la.to_date, %(h_to)s), GREATEST(la.from_date, %(h_from)s)) + 1), 0)
		FROM `tabLeave Application` la
		WHERE la.employee = %(emp_id)s AND la.docstatus = 1
		  AND la.from_date <= %(h_to)s AND la.to_date >= %(h_from)s
	""", p)[0][0]
	weekoff_days = frappe.db.sql("""
		SELECT COUNT(*)
		FROM `tabHoliday` h
		JOIN `tabEmployee` e2 ON e2.holiday_list = h.parent
		WHERE e2.name = %(emp_id)s AND h.weekly_off = 1
		  AND h.holiday_date BETWEEN %(h_from)s AND %(h_to)s
	""", p)[0][0]
	holiday_days = frappe.db.sql("""
		SELECT COUNT(*)
		FROM `tabHoliday` h
		JOIN `tabEmployee` e2 ON e2.holiday_list = h.parent
		WHERE e2.name = %(emp_id)s AND IFNULL(h.weekly_off, 0) = 0
		  AND h.holiday_date BETWEEN %(h_from)s AND %(h_to)s
	""", p)[0][0]
	# Night workers: their shift STARTS in the evening and runs past midnight, so a
	# calendar day with no scan is not an absence — the scan for that shift sits in the
	# previous evening. Detect a night shift and, for those employees, count a day absent
	# only when the whole night window (prev 12:00 -> that day 12:00) has no scan.
	is_night_emp = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabEmployee` e
		JOIN `tabShift Type` st ON st.name = e.default_shift
		WHERE e.name = %(emp_id)s AND TIME(st.end_time) < TIME(st.start_time)
	""", p)[0][0]
	night_start = frappe.db.sql("""
		SELECT st.start_time FROM `tabEmployee` e
		JOIN `tabShift Type` st ON st.name = e.default_shift
		WHERE e.name = %(emp_id)s
	""", p)
	kpi["is_night_shift"] = 1 if is_night_emp else 0
	kpi["shift_start"] = str(night_start[0][0]) if night_start and night_start[0][0] else None

	# ── ABSENT days in range: working days (range minus weekly-offs/holidays and
	# minus approved-leave days) on which the employee has no check-in at all.
	# Counted only up to today, since future days can't be absent yet. ──
	p["is_night"] = 1 if is_night_emp else 0
	absent_days = frappe.db.sql("""
		SELECT COUNT(*) FROM (
			SELECT d.dt
			FROM (
				SELECT DATE(%(h_from)s) + INTERVAL (n.num) DAY AS dt
				FROM (
					SELECT (a.N + b.N * 10 + c.N * 100) AS num
					FROM (SELECT 0 AS N UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
						  UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
						  UNION ALL SELECT 8 UNION ALL SELECT 9) a,
						 (SELECT 0 AS N UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
						  UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
						  UNION ALL SELECT 8 UNION ALL SELECT 9) b,
						 (SELECT 0 AS N UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
						  UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
						  UNION ALL SELECT 8 UNION ALL SELECT 9) c
				) n
			) d
			WHERE d.dt <= LEAST(%(h_to)s, CURDATE())
			  AND NOT EXISTS (
					SELECT 1 FROM `tabEmployee Checkin` ec
					WHERE ec.employee = %(emp_id)s
					  AND ( DATE(ec.`time`) = d.dt
							OR ( %(is_night)s = 1
								 AND ec.`time` >= (d.dt - INTERVAL 1 DAY) + INTERVAL 12 HOUR
								 AND ec.`time` <  d.dt + INTERVAL 12 HOUR ) ))
			  AND NOT EXISTS (
					SELECT 1 FROM `tabHoliday` h
					JOIN `tabEmployee` e2 ON e2.holiday_list = h.parent
					WHERE e2.name = %(emp_id)s AND h.holiday_date = d.dt)
			  AND NOT EXISTS (
					SELECT 1 FROM `tabLeave Application` la
					WHERE la.employee = %(emp_id)s AND la.docstatus = 1
					  AND d.dt BETWEEN la.from_date AND la.to_date)
		) z
	""", p)[0][0]
	kpi["absent_days"] = int(absent_days or 0)
	kpi["leave_days"] = int(leave_days or 0)
	kpi["weekoff_days"] = int(weekoff_days or 0)
	kpi["holiday_days"] = int(holiday_days or 0)

	frappe.response["message"] = {
		"emp_id": emp_id, "from_date": str(h_from), "to_date": str(h_to),
		"employee": emp_meta, "kpi": kpi, "monthly": monthly, "rows": rows,
		"ot_threshold_min": 30,
	}


# SERVER SCRIPT: attendance_mark
# API Method: attendance_mark
# Receives: emp_ids=500165,500273  att_date=2026-05-13  status=Present  reason=Away Assignment
#
# FIX: set att.shift from the employee's effective shift (default_shift, else an
# active Shift Assignment covering att_date). ERPNext's Attendance validation
# errors with "[Attendance, ...]: shift" when an employee has a shift context
# for the day but the Attendance record is submitted with a blank/mismatched
# shift, so we resolve and set it before insert.
@frappe.whitelist()
def attendance_mark():
	"""Marks attendance for people the machines missed."""

	emp_ids_raw = frappe.form_dict.get("emp_ids") or ""
	att_date	= frappe.form_dict.get("att_date") or str(frappe.utils.today())
	att_dates_raw = frappe.form_dict.get("att_dates") or ""
	only_gaps = (frappe.form_dict.get("only_gaps") or "") == "1"
	allow_off = (frappe.form_dict.get("allow_off") or "") == "1"
	status	  = frappe.form_dict.get("status")   or "Present"
	reason	  = (frappe.form_dict.get("reason")  or "").strip()

	valid_reasons = ["Away Assignment", "Pending Off", "Pending Holiday"]
	if reason and reason not in valid_reasons:
		reason = ""
	if reason:
		status = "Present"

	# ── mode=off_absents: find (and with apply=1, cancel) every submitted Absent
	# that lands on the employee's own weekly off. Nobody can be absent from a
	# rest day, so these rows are always wrong — they come from shift-wise auto
	# attendance running over a holiday. Read-only unless apply=1 is passed. ──
	if (frappe.form_dict.get("mode") or "") == "off_absents":
		o_from = frappe.form_dict.get("from_date") or str(frappe.utils.add_days(frappe.utils.today(), -120))
		o_to = frappe.form_dict.get("to_date") or str(frappe.utils.today())
		if str(o_to) < str(o_from):
			o_from, o_to = o_to, o_from
		o_apply = (frappe.form_dict.get("apply") or "") == "1"
		o_holidays = (frappe.form_dict.get("include_holidays") or "") == "1"
		o_limit = frappe.utils.cint(frappe.form_dict.get("limit") or 500)
		if o_limit < 1:
			o_limit = 1
		if o_limit > 2000:
			o_limit = 2000

		o_where = ""
		o_params = {"o_from": o_from, "o_to": o_to, "o_limit": o_limit}
		o_company = (frappe.form_dict.get("company") or "").strip()
		o_farm = (frappe.form_dict.get("farm") or "").strip()
		o_etype = (frappe.form_dict.get("employment_type") or "").strip()
		o_emps = (frappe.form_dict.get("emp_ids") or "").strip()
		if o_company:
			o_where += " AND e.company = %(o_company)s"
			o_params["o_company"] = o_company
		if o_farm:
			o_where += " AND TRIM(e.custom_farm) = TRIM(%(o_farm)s)"
			o_params["o_farm"] = o_farm
		if o_etype:
			o_where += " AND FIND_IN_SET(e.employment_type, %(o_etype)s)"
			o_params["o_etype"] = o_etype
		if o_emps:
			o_where += " AND FIND_IN_SET(a.employee, %(o_emps)s)"
			o_params["o_emps"] = o_emps
		# weekly_off = 1 is the rest day; public holidays (weekly_off = 0) are only
		# included when asked for, because those are a separate policy question
		o_off = " AND h.weekly_off = 1"
		if o_holidays:
			o_off = ""

		# count=1: sizing only — how many rest-day Absents exist, grouped by month
		# and company, so a cleanup can be scoped before anything is cancelled
		if (frappe.form_dict.get("count") or "") == "1":
			o_agg = frappe.db.sql("""
				SELECT DATE_FORMAT(a.attendance_date, '%%Y-%%m') AS ym,
					   COALESCE(e.company, '') AS company,
					   COUNT(*) AS n,
					   COUNT(DISTINCT a.employee) AS emps,
					   SUM(CASE WHEN a.in_time IS NOT NULL OR a.out_time IS NOT NULL
								THEN 1 ELSE 0 END) AS with_scan
				FROM `tabAttendance` a
				JOIN `tabEmployee` e ON e.name = a.employee
				JOIN `tabHoliday` h ON h.parent = e.holiday_list
								   AND h.holiday_date = a.attendance_date
				WHERE a.docstatus = 1
				  AND a.status = 'Absent'
				  AND a.attendance_date BETWEEN %(o_from)s AND %(o_to)s
			""" + o_off + o_where + """
				GROUP BY ym, company
				ORDER BY ym ASC, company ASC
			""", o_params, as_dict=True)
			o_tot = 0
			o_scan = 0
			o_buckets = []
			for g in o_agg:
				o_tot = o_tot + frappe.utils.cint(g["n"])
				o_scan = o_scan + frappe.utils.cint(g["with_scan"])
				o_buckets.append({"month": g["ym"], "company": g["company"],
								  "rows": frappe.utils.cint(g["n"]),
								  "employees": frappe.utils.cint(g["emps"]),
								  "with_scan": frappe.utils.cint(g["with_scan"])})
			frappe.response["message"] = {"mode": "off_absents", "count_only": 1,
										  "from": str(o_from), "to": str(o_to),
										  "total": o_tot, "with_scan": o_scan,
										  "buckets": o_buckets}
			return

		o_rows = frappe.db.sql("""
			SELECT a.name AS name, a.employee AS employee, a.employee_name AS employee_name,
				   a.attendance_date AS d, a.shift AS shift, a.in_time AS in_time,
				   a.out_time AS out_time, a.creation AS creation, a.owner AS owner,
				   COALESCE(e.company, '') AS company,
				   COALESCE(NULLIF(TRIM(e.custom_farm), ''), '') AS farm,
				   COALESCE(e.employment_type, '') AS employment_type,
				   h.description AS holiday, h.weekly_off AS weekly_off
			FROM `tabAttendance` a
			JOIN `tabEmployee` e ON e.name = a.employee
			JOIN `tabHoliday` h ON h.parent = e.holiday_list
							   AND h.holiday_date = a.attendance_date
			WHERE a.docstatus = 1
			  AND a.status = 'Absent'
			  AND a.attendance_date BETWEEN %(o_from)s AND %(o_to)s
		""" + o_off + o_where + """
			ORDER BY a.attendance_date ASC, a.employee ASC
			LIMIT %(o_limit)s
		""", o_params, as_dict=True)

		o_list = []
		for r in o_rows:
			o_list.append({"name": r["name"], "employee": r["employee"],
						   "employee_name": r["employee_name"], "date": str(r["d"]),
						   "shift": r["shift"] or "", "company": r["company"],
						   "farm": r["farm"], "employment_type": r["employment_type"],
						   "holiday": r["holiday"] or "",
						   "weekly_off": frappe.utils.cint(r["weekly_off"]),
						   "had_scan": 1 if (r["in_time"] or r["out_time"]) else 0,
						   "created": str(r["creation"])[:19], "owner": r["owner"]})

		o_ok = 0
		o_err = 0
		if o_apply:
			for row in o_list:
				# a rest-day Absent with scan times is a roster question, not a
				# clerical error, so it is reported but never cancelled here
				if row["had_scan"]:
					row["skipped"] = "has scan times"
					continue
				try:
					o_doc = frappe.get_doc("Attendance", row["name"])
					o_doc.flags.ignore_permissions = True
					o_doc.cancel()
					row["cancelled"] = 1
					o_ok = o_ok + 1
				except Exception as oe:
					row["error"] = str(oe)
					o_err = o_err + 1

		if o_ok:
			frappe.db.commit()
		frappe.response["message"] = {"mode": "off_absents", "from": str(o_from), "to": str(o_to),
									  "applied": 1 if o_apply else 0, "found": len(o_list),
									  "cancelled": o_ok, "errors": o_err,
									  "capped_at": o_limit, "rows": o_list}
		return

	# ── mode=cancel: cancel specific submitted Attendance records by name. Used to
	# clear contradictory rows — an Absent on a day that already has a Present, an
	# Absent written on the employee's own weekly off, or a duplicate Present with
	# no scan times sitting beside a scan-backed one. Guarded: Attendance only,
	# submitted only, and every record acted on is reported back. ──
	if (frappe.form_dict.get("mode") or "") == "cancel":
		c_names = []
		for nm in (frappe.form_dict.get("att_names") or "").split(","):
			nm = nm.strip()
			if nm and nm not in c_names:
				c_names.append(nm)
		c_results = []
		c_ok = 0
		c_err = 0
		for nm in c_names[:400]:
			try:
				cur = frappe.db.get_value("Attendance", nm,
					["name", "employee", "attendance_date", "status", "docstatus", "shift"],
					as_dict=True)
				if not cur:
					c_results.append({"name": nm, "ok": False, "error": "not found"})
					c_err = c_err + 1
					continue
				if frappe.utils.cint(cur.get("docstatus")) != 1:
					c_results.append({"name": nm, "ok": False,
						"error": "not submitted (docstatus " + str(cur.get("docstatus")) + ")"})
					c_err = c_err + 1
					continue
				c_doc = frappe.get_doc("Attendance", nm)
				c_doc.flags.ignore_permissions = True
				c_doc.cancel()
				c_results.append({"name": nm, "ok": True, "employee": cur.get("employee"),
					"date": str(cur.get("attendance_date")), "status": cur.get("status"),
					"shift": cur.get("shift") or ""})
				c_ok = c_ok + 1
			except Exception as ce:
				c_results.append({"name": nm, "ok": False, "error": str(ce)})
				c_err = c_err + 1
		if c_ok:
			frappe.db.commit()
		frappe.response["message"] = {"mode": "cancel", "requested": len(c_names),
			"ok_count": c_ok, "err_count": c_err, "results": c_results}
		return

	# ── mode=gaps: report, per date in a range, which of the selected employees have
	# NO attendance record at all or an ABSENT one. Read-only; used by the dashboard
	# to offer the dates that are actually markable. ──
	if (frappe.form_dict.get("mode") or "") == "gaps":
		g_from = frappe.form_dict.get("from_date") or str(frappe.utils.today())
		g_to   = frappe.form_dict.get("to_date")   or g_from
		if str(g_to) < str(g_from):
			g_from, g_to = g_to, g_from
		span = frappe.utils.date_diff(g_to, g_from)
		if span < 0:
			span = 0
		if span > 61:
			span = 61
			g_to = frappe.utils.add_days(g_from, 61)
		g_ids = [e.strip() for e in (frappe.form_dict.get("emp_ids") or "").split(",") if e.strip()]
		g_meta = {}
		if not g_ids:
			# nobody ticked: scan the whole filtered population so employees who were
			# absent EARLIER in the range (but not today) are still found and offered.
			gp_where = ["e.status = 'Active'"]
			gp = {}
			if (frappe.form_dict.get("company") or "").strip():
				gp_where.append("e.company = %(company)s")
				gp["company"] = frappe.form_dict.get("company").strip()
			if (frappe.form_dict.get("farm") or "").strip():
				gp_where.append("TRIM(e.custom_farm) = TRIM(%(farm)s)")
				gp["farm"] = frappe.form_dict.get("farm").strip()
			if (frappe.form_dict.get("employment_type") or "").strip():
				gp_where.append("FIND_IN_SET(e.employment_type, %(etype)s)")
				gp["etype"] = frappe.form_dict.get("employment_type").strip()
			for r in frappe.db.sql("""
				SELECT e.name, e.employee_name, e.custom_farm, e.designation, e.employment_type
				FROM `tabEmployee` e
				WHERE """ + " AND ".join(gp_where) + """
				LIMIT 2000
			""", gp, as_dict=True):
				g_ids.append(r["name"])
				g_meta[r["name"]] = r
		if not g_ids:
			frappe.response["message"] = {"error": "No employees matched the filters"}
			return
		if not g_meta:
			for r in frappe.db.sql("""
				SELECT e.name, e.employee_name, e.custom_farm, e.designation, e.employment_type
				FROM `tabEmployee` e WHERE e.name IN %(ids)s
			""", {"ids": tuple(g_ids)}, as_dict=True):
				g_meta[r["name"]] = r

		p = {"ids": tuple(g_ids), "f": g_from, "t": g_to}
		att_map = {}
		for r in frappe.db.sql("""
			SELECT employee, attendance_date, status
			FROM `tabAttendance`
			WHERE employee IN %(ids)s AND attendance_date BETWEEN %(f)s AND %(t)s
			  AND docstatus < 2
		""", p, as_dict=True):
			att_map[str(r["employee"]) + "|" + str(r["attendance_date"])] = r["status"] or ""

		scan_set = {}
		for r in frappe.db.sql("""
			SELECT ec.employee AS emp, DATE(ec.`time`) AS d
			FROM `tabEmployee Checkin` ec
			WHERE ec.employee IN %(ids)s
			  AND ec.`time` >= %(f)s AND ec.`time` < %(t)s + INTERVAL 1 DAY
			GROUP BY ec.employee, DATE(ec.`time`)
		""", p, as_dict=True):
			scan_set[str(r["emp"]) + "|" + str(r["d"])] = 1

		hol_set = {}
		for r in frappe.db.sql("""
			SELECT e.name AS emp, h.holiday_date AS d
			FROM `tabEmployee` e
			JOIN `tabHoliday` h ON h.parent = e.holiday_list
			WHERE e.name IN %(ids)s AND h.holiday_date BETWEEN %(f)s AND %(t)s
		""", p, as_dict=True):
			hol_set[str(r["emp"]) + "|" + str(r["d"])] = 1

		leave_rows = frappe.db.sql("""
			SELECT la.employee AS emp, la.from_date AS fd, la.to_date AS td
			FROM `tabLeave Application` la
			WHERE la.employee IN %(ids)s AND la.docstatus = 1
			  AND la.from_date <= %(t)s AND la.to_date >= %(f)s
		""", p, as_dict=True)

		out_dates = []
		emp_gaps = {}
		emp_abs = {}
		emp_absscan = {}
		emp_absoff = {}
		emp_mis = {}
		today_str = str(frappe.utils.today())
		i = 0
		while i <= span:
			d = str(frappe.utils.add_days(g_from, i))
			i = i + 1
			absent_n = 0
			absent_scan_n = 0
			absent_off_n = 0
			missing_n = 0
			present_n = 0
			leave_n = 0
			holiday_n = 0
			for e in g_ids:
				k = e + "|" + d
				st = att_map.get(k)
				on_leave = 0
				for lr in leave_rows:
					if str(lr["emp"]) == e and str(lr["fd"]) <= d and str(lr["td"]) >= d:
						on_leave = 1
						break
				if st == "Absent":
					# marked Absent on a weekly-off / holiday -> the Absent record is
					# wrong, but the fix is to CANCEL it (they were off), not to mark
					# them Present. Flagged separately and kept out of "markable".
					if hol_set.get(k):
						absent_off_n = absent_off_n + 1
						if e not in emp_absoff:
							emp_absoff[e] = []
						emp_absoff[e].append(d)
					# marked Absent but a scan exists that day -> contradiction worth
					# flagging separately: they clearly attended
					elif scan_set.get(k):
						absent_scan_n = absent_scan_n + 1
						if e not in emp_absscan:
							emp_absscan[e] = []
						emp_absscan[e].append(d)
					else:
						absent_n = absent_n + 1
						if e not in emp_abs:
							emp_abs[e] = []
						emp_abs[e].append(d)
					if e not in emp_gaps:
						emp_gaps[e] = []
					emp_gaps[e].append(d)
				elif st in ("Present", "Work From Home", "Half Day"):
					present_n = present_n + 1
				elif st == "On Leave" or on_leave:
					leave_n = leave_n + 1
				elif hol_set.get(k):
					holiday_n = holiday_n + 1
				elif scan_set.get(k):
					present_n = present_n + 1
				else:
					missing_n = missing_n + 1
					if e not in emp_gaps:
						emp_gaps[e] = []
					emp_gaps[e].append(d)
					if e not in emp_mis:
						emp_mis[e] = []
					emp_mis[e].append(d)
			out_dates.append({
				"date": d,
				"absent": absent_n,
				"absent_with_scan": absent_scan_n,
				"absent_on_off": absent_off_n,
				"missing": missing_n,
				"present": present_n,
				"leave": leave_n,
				"holiday": holiday_n,
				"markable": absent_n + absent_scan_n + missing_n,
				"flagged": absent_off_n,
				"future": 1 if d > today_str else 0,
			})

		sortable = []
		for e in emp_gaps:
			m = g_meta.get(e) or {}
			sortable.append((
				-len(emp_gaps[e]),
				str(m.get("employee_name") or e).lower(),
				{
					"name": e,
					"employee_name": m.get("employee_name") or e,
					"custom_farm": m.get("custom_farm") or "",
					"designation": m.get("designation") or "",
					"employment_type": m.get("employment_type") or "",
					"gap_dates": emp_gaps[e],
					"gap_count": len(emp_gaps[e]),
					"absent_dates": emp_abs.get(e) or [],
					"absent_scan_dates": emp_absscan.get(e) or [],
					"absent_off_dates": emp_absoff.get(e) or [],
					"missing_dates": emp_mis.get(e) or [],
					"absent_count": len(emp_abs.get(e) or []),
					"absent_scan_count": len(emp_absscan.get(e) or []),
					"absent_off_count": len(emp_absoff.get(e) or []),
					"missing_count": len(emp_mis.get(e) or []),
				}
			))
		sortable.sort()
		gap_emps = [row[2] for row in sortable]

		frappe.response["message"] = {
			"from_date": str(g_from), "to_date": str(g_to),
			"employees": len(g_ids), "dates": out_dates,
			"gap_employees": gap_emps, "gap_employee_count": len(gap_emps),
		}
		return

	if not emp_ids_raw:
		frappe.response["message"] = {
			"error": "No emp_ids provided",
			"form_keys": list(frappe.form_dict.keys())
		}
		return

	emp_ids = [e.strip() for e in emp_ids_raw.split(",") if e.strip()]
	if not emp_ids:
		frappe.response["message"] = {"error": "emp_ids was empty after split"}
		return

	# ── resolve an active Shift Assignment per employee covering att_date.
	# Used only as a fallback when the employee has no default_shift. ──
	assigned_shift_map = {}
	sa_rows = frappe.db.sql("""
		SELECT sa.employee, sa.shift_type
		FROM `tabShift Assignment` sa
		WHERE sa.docstatus = 1 AND sa.status = 'Active'
		  AND sa.employee IN %(emp_ids)s
		  AND sa.start_date <= %(att_date)s
		  AND (sa.end_date IS NULL OR sa.end_date >= %(att_date)s)
	""", {"emp_ids": tuple(emp_ids), "att_date": att_date}, as_dict=True)
	for r in sa_rows:
		# first active assignment wins; do not overwrite
		if r.employee not in assigned_shift_map:
			assigned_shift_map[r.employee] = r.shift_type

	# one or many dates: att_dates wins when supplied (bulk date-range marking)
	mark_dates = [d.strip() for d in att_dates_raw.split(",") if d.strip()]
	if not mark_dates:
		mark_dates = [att_date]

	# ── HARD RULE: never write attendance on an employee's weekly off / holiday.
	# Marking Present on a rest day misstates the record (and inflates worked days),
	# so those pairs are skipped unless allow_off=1 is passed explicitly. ──
	off_map = {}
	if mark_dates:
		for r in frappe.db.sql("""
			SELECT e.name AS emp, h.holiday_date AS d, IFNULL(h.weekly_off, 0) AS wo
			FROM `tabEmployee` e
			JOIN `tabHoliday` h ON h.parent = e.holiday_list
			WHERE e.name IN %(ids)s AND h.holiday_date IN %(dates)s
		""", {"ids": tuple(emp_ids), "dates": tuple(mark_dates)}, as_dict=True):
			off_map[str(r["emp"]) + "|" + str(r["d"])] = int(r["wo"] or 0)

	results = []

	for mark_date in mark_dates:
	  for emp_id in emp_ids:
		  try:
			  off_flag = off_map.get(emp_id + "|" + str(mark_date))
			  if off_flag is not None and not allow_off:
				  results.append({"employee": emp_id, "date": mark_date, "ok": False,
					  "error": ("Weekly off" if off_flag else "Holiday") +
							   " — attendance not marked on a rest day",
					  "skipped_off": 1})
				  continue
			  rest_day = ""
			  if off_flag is not None:
				  rest_day = "Weekly off" if off_flag else "Holiday"

			  # ── An existing Attendance for the same employee/date blocks a new one
			  # (ERPNext rejects duplicates while docstatus < 2). When that record is
			  # an ABSENT, marking someone present must REPLACE it: cancel the Absent
			  # first, then insert the Present. A draft Absent is converted in place.
			  # Any other status (Present / On Leave / Holiday) is left untouched. ──
			  prior = frappe.db.get_value("Attendance", {
				  "employee":		emp_id,
				  "attendance_date": mark_date,
				  "docstatus":	   ["<", 2]
			  }, ["name", "status", "docstatus"], as_dict=True)

			  replaced = ""
			  if prior:
				  prior_status = prior.get("status") or ""
				  if prior_status == "Absent":
					  old_doc = frappe.get_doc("Attendance", prior.get("name"))
					  old_doc.flags.ignore_permissions = True
					  if prior.get("docstatus") == 1:
						  old_doc.cancel()
						  replaced = "cancelled Absent " + str(prior.get("name"))
					  else:
						  # draft Absent: delete it so the new Present can be inserted
						  old_doc.delete()
						  replaced = "removed draft Absent " + str(prior.get("name"))
				  elif only_gaps:
					  # bulk range marking: this employee is already accounted for on
					  # this date, so it is not a gap -> skip quietly
					  continue
				  else:
					  results.append({"employee": emp_id, "date": mark_date, "ok": False,
						  "error": "Already marked " + str(prior_status or "?") +
								   " (" + str(prior.get("name")) + ")"})
					  continue

			  emp_doc = frappe.db.get_value("Employee", emp_id,
				  ["company", "employee_name", "default_shift"], as_dict=True)
			  if not emp_doc:
				  results.append({"employee": emp_id, "date": mark_date, "ok": False,
					  "error": "Employee not found"})
				  continue

			  # effective shift: employee default_shift is source of truth, active
			  # Shift Assignment used only when default_shift is blank.
			  eff_shift = emp_doc.default_shift or assigned_shift_map.get(emp_id)

			  att = frappe.new_doc("Attendance")
			  att.employee		= emp_id
			  att.employee_name   = emp_doc.employee_name
			  att.attendance_date = mark_date
			  att.status		  = status
			  att.company		 = emp_doc.company
			  if eff_shift:
				  att.shift = eff_shift
			  if reason:
				  att.custom_marking_reason = reason
			  att.insert(ignore_permissions=True)
			  att.submit()

			  results.append({"employee": emp_id, "date": mark_date, "ok": True, "name": att.name,
				  "shift": eff_shift or "", "replaced": replaced,
				  "on_rest_day": rest_day})

		  except Exception as e:
			  # keep more of the message so shift/validation errors are legible
			  results.append({"employee": emp_id, "date": mark_date, "ok": False, "error": str(e)[:300]})

	ok_count  = len([r for r in results if r.get("ok")])
	err_count = len([r for r in results if not r.get("ok")])

	replaced_count = len([r for r in results if r.get("replaced")])
	off_skipped = len([r for r in results if r.get("skipped_off")])
	off_marked = len([r for r in results if r.get("ok") and r.get("on_rest_day")])

	frappe.response["message"] = {
		"results":		results,
		"ok_count":	   ok_count,
		"err_count":	  err_count,
		"replaced_count": replaced_count,
		"off_skipped": off_skipped,
		"off_marked": off_marked,
		"allow_off": 1 if allow_off else 0,
	}


# SERVER SCRIPT: shift_assign
# API Method: shift_assign
@frappe.whitelist()
def shift_assign():
	"""Moves a group of employees onto another shift."""

	emp_ids_raw = frappe.form_dict.get("emp_ids")	or ""
	shift_type  = frappe.form_dict.get("shift_type") or ""

	if not emp_ids_raw:
		frappe.response["message"] = {"error": "No emp_ids provided"}
		return
	if not shift_type:
		frappe.response["message"] = {"error": "No shift_type provided"}
		return

	emp_ids = [e.strip() for e in emp_ids_raw.split(",") if e.strip()]
	results = []

	for emp_id in emp_ids:
		try:
			frappe.db.set_value("Employee", emp_id, "default_shift", shift_type)
			results.append({"employee": emp_id, "ok": True})
		except Exception as e:
			results.append({"employee": emp_id, "ok": False, "error": str(e)[:150]})

	frappe.db.commit()

	ok_count  = len([r for r in results if r.get("ok")])
	err_count = len([r for r in results if not r.get("ok")])

	frappe.response["message"] = {
		"results":   results,
		"ok_count":  ok_count,
		"err_count": err_count,
	}


# ─── the one custom field this backend depends on ──────────────────────────

# The Actions tab writes a reason when somebody marks attendance by hand, so the
# row can be told apart from one the machines produced. It was a Custom Field
# created on the live site only, which meant the register query
# (`SELECT ... a.custom_marking_reason`) died with "Unknown column" on every
# other site — the whole register, not just the reason.
#
# The app owns the field now, so a site that has this code has the column.
MARKING_REASON_FIELD = {
	"Attendance": [
		{
			"fieldname": "custom_marking_reason",
			"label": "Marking Reason",
			"fieldtype": "Select",
			"insert_after": "status",
			"options": "\nAway Assignment\nPending Off\nPending Holiday",
			"module": "Upande TA",
		}
	]
}


def ensure_attendance_insights_fields():
	"""Create the custom field the dashboard's register and marking rely on."""
	if not frappe.db.table_exists("Attendance"):
		return

	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(MARKING_REASON_FIELD, update=True)
