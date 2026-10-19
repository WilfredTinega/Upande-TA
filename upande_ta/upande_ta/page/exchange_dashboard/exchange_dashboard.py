# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Numbers for the Exchange Dashboard page: who is on the program, where
they are hosted, the hours their punches add up to, and how far each one is
through their program."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, flt, get_datetime, getdate, today

ENDING_SOON_DAYS = 14


@frappe.whitelist()
def get_data(
	from_date: str | None = None,
	to_date: str | None = None,
	company: str | None = None,
	home_instance: str | None = None,
	status: str | None = None,
) -> dict:
	if not (
		frappe.has_permission("Exchange Employee", "read") or frappe.has_permission("HQ Employee", "read")
	):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	to_date = getdate(to_date or today())
	from_date = getdate(from_date or add_days(to_date, -29))
	if from_date > to_date:
		from_date, to_date = to_date, from_date
	now = getdate(today())

	filters = {}
	if company:
		filters["host_company"] = company
	if home_instance:
		filters["home_instance"] = home_instance
	hq = _hq_section(dict(filters), status, from_date, to_date, now)
	if status:
		filters["status"] = status
	employees = frappe.get_all(
		"Exchange Employee",
		filters=filters,
		fields=[
			"name",
			"full_name",
			"payroll_number",
			"home_instance",
			"home_company",
			"host_company",
			"status",
			"program_start",
			"program_end",
			"creation",
		],
		order_by="full_name asc",
	)

	days = _worked_days([e.name for e in employees], from_date, to_date)
	per_employee = defaultdict(lambda: {"days": 0, "hours": 0.0, "last": None})
	for d in days:
		agg = per_employee[d["employee"]]
		agg["days"] += 1
		agg["hours"] += d["hours"]
		last = get_datetime(d["last_out"] or d["first_in"])
		agg["last"] = max(agg["last"], last) if agg["last"] else last

	rows = []
	for e in employees:
		agg = per_employee[e.name]
		start, end = e.program_start and getdate(e.program_start), e.program_end and getdate(e.program_end)
		duration = date_diff(end, start) + 1 if start and end else None
		elapsed = remaining = progress = None
		if start:
			elapsed = max(min(date_diff(now, start) + 1, duration or 10**6), 0)
		if end:
			remaining = date_diff(end, now)
		if duration and elapsed is not None:
			progress = round(min(elapsed / duration, 1) * 100)
		rows.append(
			{
				**e,
				"duration": duration,
				"elapsed": elapsed,
				"remaining": remaining,
				"progress": progress,
				"flag": _flag(e.status, start, end, now),
				"days_worked": agg["days"],
				"hours": round(agg["hours"], 2),
				"avg_hours": round(agg["hours"] / agg["days"], 2) if agg["days"] else 0,
				"last_punch": agg["last"],
			}
		)

	companies = defaultdict(lambda: {"employees": 0, "active": 0, "ended": 0, "hours": 0.0, "days": 0})
	instances = defaultdict(int)
	added = defaultdict(int)
	for r in rows:
		c = companies[r["host_company"] or _("Not Set")]
		c["employees"] += 1
		c["active" if r["status"] == "Active" else "ended"] += 1
		c["hours"] += r["hours"]
		c["days"] += r["days_worked"]
		instances[r["home_instance"] or _("Not Set")] += 1
		if from_date <= getdate(r["creation"]) <= to_date:
			added[r["host_company"] or _("Not Set")] += 1

	active = [r for r in rows if r["status"] == "Active"]
	today_ids = {d["employee"] for d in days if d["date"] == now}
	return {
		"from_date": from_date,
		"to_date": to_date,
		"summary": {
			"total": len(rows),
			"active": len(active),
			"ended": len(rows) - len(active),
			"added": sum(added.values()),
			"present_today": len(today_ids),
			"hours": round(sum(r["hours"] for r in rows), 2),
			"days_worked": len(days),
			"ending_soon": sum(1 for r in rows if r["flag"] == "Ending Soon"),
			"overdue": sum(1 for r in rows if r["flag"] == "Past End Date"),
		},
		"companies": [
			{
				"company": k,
				**v,
				"added": added.get(k, 0),
				"hours": round(v["hours"], 2),
				"avg_hours": round(v["hours"] / v["days"], 2) if v["days"] else 0,
			}
			for k, v in sorted(companies.items(), key=lambda kv: -kv[1]["employees"])
		],
		"instances": [{"instance": k, "employees": v} for k, v in sorted(instances.items())],
		"employees": rows,
		"daily": sorted(days, key=lambda d: (d["date"], d["full_name"] or ""), reverse=True)[:1000],
		"ending": _ending_by_month(active),
		"hq": hq,
	}


def _hq_section(filters, status, from_date, to_date, now) -> dict:
	"""HQ Employees: people from another instance working from here, with no
	program dates. Counts, per company and per person, from their punches."""
	if not frappe.has_permission("HQ Employee", "read"):
		return {"summary": {}, "companies": [], "employees": []}
	if status:
		# the dashboard filter says Ended; an HQ Employee is Inactive
		filters["status"] = "Active" if status == "Active" else "Inactive"
	people = frappe.get_all(
		"HQ Employee",
		filters=filters,
		fields=["name", "full_name", "payroll_number", "home_instance", "host_company", "status"],
		order_by="full_name asc",
	)
	days = _worked_days([p.name for p in people], from_date, to_date)
	agg = defaultdict(lambda: {"days": 0, "hours": 0.0, "last": None})
	for d in days:
		a = agg[d["employee"]]
		a["days"] += 1
		a["hours"] += d["hours"]
		last = get_datetime(d["last_out"] or d["first_in"])
		a["last"] = max(a["last"], last) if a["last"] else last
	rows = [
		{
			**p,
			"days_worked": agg[p.name]["days"],
			"hours": round(agg[p.name]["hours"], 2),
			"avg_hours": round(agg[p.name]["hours"] / agg[p.name]["days"], 2) if agg[p.name]["days"] else 0,
			"last_punch": agg[p.name]["last"],
		}
		for p in people
	]
	companies = defaultdict(lambda: {"employees": 0, "active": 0, "hours": 0.0, "days": 0})
	for r in rows:
		c = companies[r["host_company"] or _("Not Set")]
		c["employees"] += 1
		c["active"] += 1 if r["status"] == "Active" else 0
		c["hours"] += r["hours"]
		c["days"] += r["days_worked"]
	return {
		"summary": {
			"total": len(rows),
			"active": sum(1 for r in rows if r["status"] == "Active"),
			"present_today": len({d["employee"] for d in days if d["date"] == now}),
			"hours": round(sum(r["hours"] for r in rows), 2),
			"days_worked": len(days),
		},
		"companies": [
			{
				"company": k,
				**v,
				"hours": round(v["hours"], 2),
				"avg_hours": round(v["hours"] / v["days"], 2) if v["days"] else 0,
			}
			for k, v in sorted(companies.items(), key=lambda kv: -kv[1]["employees"])
		],
		"employees": rows,
		"daily": sorted(days, key=lambda d: (d["date"], d["full_name"] or ""), reverse=True)[:1000],
	}


def _flag(status, start, end, now):
	if status != "Active":
		return "Ended"
	if start and start > now:
		return "Not Started"
	if end and end < now:
		return "Past End Date"
	if end and date_diff(end, now) <= ENDING_SOON_DAYS:
		return "Ending Soon"
	return "On Program"


def _worked_days(names, from_date, to_date) -> list:
	"""One row per employee per day with a punch: the first check-in, the
	last check-out and the hours between them. Without IN/OUT on the
	punches, the first and last punch of the day are used."""
	if not names:
		return []
	punches = frappe.get_all(
		"Exchange Checkin",
		filters={
			"exchange_employee": ["in", names],
			"time": ["between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59.999999"]],
		},
		fields=["exchange_employee", "employee_name", "company", "time", "log_type"],
		order_by="time asc",
	)
	by_day = defaultdict(list)
	for p in punches:
		by_day[(p.exchange_employee, getdate(p.time))].append(p)

	out = []
	for (employee, day), items in by_day.items():
		ins = [p.time for p in items if p.log_type == "IN"]
		outs = [p.time for p in items if p.log_type == "OUT"]
		first_in = ins[0] if ins else items[0].time
		last_out = outs[-1] if outs else items[-1].time
		if get_datetime(last_out) <= get_datetime(first_in):
			last_out = items[-1].time
		hours = (
			(get_datetime(last_out) - get_datetime(first_in)).total_seconds() / 3600 if len(items) > 1 else 0
		)
		out.append(
			{
				"employee": employee,
				"full_name": items[0].employee_name,
				"company": items[-1].company,
				"date": day,
				"first_in": first_in,
				"last_out": last_out if len(items) > 1 else None,
				"punches": len(items),
				"hours": round(flt(hours), 2),
			}
		)
	return out


def _ending_by_month(active) -> list:
	months = defaultdict(int)
	for r in active:
		if r["program_end"]:
			months[getdate(r["program_end"]).strftime("%Y-%m")] += 1
	return [{"month": k, "employees": v} for k, v in sorted(months.items())]
