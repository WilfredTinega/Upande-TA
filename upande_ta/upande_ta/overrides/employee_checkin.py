# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, getdate, today


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


def auto_close_open_ins(target_date=None, days=7):
	end_date   = getdate(target_date) if target_date else getdate(today())
	start_date = add_days(end_date, -(days - 1))

	range_start = get_datetime(f"{start_date} 00:00:00")
	range_end   = get_datetime(f"{end_date} 23:59:59")

	rows = frappe.db.sql(
		"""
		SELECT employee, DATE(time) AS log_date,
		       SUM(CASE WHEN log_type = 'IN' THEN 1 ELSE 0 END) AS in_count
		FROM `tabEmployee Checkin`
		WHERE time BETWEEN %(start)s AND %(end)s
		  AND employee IS NOT NULL
		GROUP BY employee, DATE(time)
		HAVING in_count > 1
		""",
		{"start": range_start, "end": range_end},
		as_dict=True
	)

	flipped = 0
	deleted = 0
	for r in rows:
		day_start = get_datetime(f"{r.log_date} 00:00:00")
		day_end   = get_datetime(f"{r.log_date} 23:59:59")

		in_logs = frappe.db.get_all(
			"Employee Checkin",
			filters={
				"employee":   r.employee,
				"log_type":   "IN",
				"time":       ["between", [day_start, day_end]],
				"attendance": ["in", ["", None]],
			},
			fields=["name", "time"],
			order_by="time asc",
		)

		if len(in_logs) < 2:
			continue

		first_in = in_logs[0]
		last_in  = in_logs[-1]

		middle_logs = frappe.db.get_all(
			"Employee Checkin",
			filters={
				"employee":   r.employee,
				"time":       ["between", [first_in.time, last_in.time]],
				"name":       ["not in", [first_in.name, last_in.name]],
				"attendance": ["in", ["", None]],
			},
			fields=["name"],
		)

		try:
			doc = frappe.get_doc("Employee Checkin", last_in.name)
			doc.flags.ignore_validate = True
			doc.log_type = "OUT"
			doc.save(ignore_permissions=True)
			flipped += 1
		except Exception as e:
			frappe.log_error(
				f"auto_close_open_ins flip failed for {last_in.name}: {e}",
				"Employee Checkin Auto-Close"
			)
			continue

		for mid in middle_logs:
			try:
				frappe.delete_doc(
					"Employee Checkin",
					mid.name,
					ignore_permissions=True,
					force=True,
					delete_permanently=True,
				)
				deleted += 1
			except Exception as e:
				frappe.log_error(
					f"auto_close_open_ins delete failed for {mid.name}: {e}",
					"Employee Checkin Auto-Close"
				)

	frappe.db.commit()
	frappe.logger().info(
		f"auto_close_open_ins: scanned {start_date}..{end_date}, "
		f"flipped {flipped} IN→OUT, deleted {deleted} middle logs across {len(rows)} (employee, day) groups"
	)
	return {
		"start_date": str(start_date),
		"end_date":   str(end_date),
		"candidates": len(rows),
		"flipped":    flipped,
		"deleted":    deleted,
	}
