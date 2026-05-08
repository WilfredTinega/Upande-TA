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


def auto_close_open_ins(target_date=None):
	target_date = getdate(target_date) if target_date else getdate(add_days(today(), -1))

	day_start = get_datetime(f"{target_date} 00:00:00")
	day_end   = get_datetime(f"{target_date} 23:59:59")

	rows = frappe.db.sql(
		"""
		SELECT employee,
		       SUM(CASE WHEN log_type = 'IN'  THEN 1 ELSE 0 END) AS in_count,
		       SUM(CASE WHEN log_type = 'OUT' THEN 1 ELSE 0 END) AS out_count
		FROM `tabEmployee Checkin`
		WHERE time BETWEEN %(start)s AND %(end)s
		  AND employee IS NOT NULL
		GROUP BY employee
		HAVING (in_count >= 2 AND out_count = 0)
		    OR (in_count > 2)
		""",
		{"start": day_start, "end": day_end},
		as_dict=True
	)

	flipped = 0
	for r in rows:
		last_log = frappe.db.get_value(
			"Employee Checkin",
			{
				"employee":   r.employee,
				"time":       ["between", [day_start, day_end]],
				"attendance": ["in", ["", None]],
			},
			["name", "log_type", "time"],
			order_by="time desc"
		)
		if not last_log:
			continue

		name, last_log_type, _last_time = last_log
		if last_log_type != "IN":
			continue

		try:
			doc = frappe.get_doc("Employee Checkin", name)
			doc.flags.ignore_validate = True
			doc.log_type = "OUT"
			if r.out_count == 0:
				reason = _("last log of {0} with no OUT recorded").format(frappe.utils.format_date(target_date))
			else:
				reason = _("last log of {0} is IN with {1} INs and {2} OUTs recorded").format(
					frappe.utils.format_date(target_date), int(r.in_count), int(r.out_count)
				)
			doc.add_comment(
				"Comment",
				_("Auto-corrected by scheduler: flipped IN → OUT ({0}).").format(reason)
			)
			doc.save(ignore_permissions=True)
			flipped += 1
		except Exception as e:
			frappe.log_error(
				f"auto_close_open_ins failed for {name}: {e}",
				"Employee Checkin Auto-Close"
			)

	frappe.db.commit()
	frappe.logger().info(
		f"auto_close_open_ins: scanned {target_date}, flipped {flipped} IN→OUT across {len(rows)} employees"
	)
	return {"date": str(target_date), "candidates": len(rows), "flipped": flipped}
