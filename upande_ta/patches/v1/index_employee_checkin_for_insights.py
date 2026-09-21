# Copyright (c) 2026, Upande LTD and contributors
"""Index `tabEmployee Checkin` for Attendance Insights.

The dashboard reads the raw check-ins for a period — a dozen times over, for
the present/absent split, the daily series, average hours, the late and
early-out counts and the hourly pattern. HRMS ships the table with an index on
`employee` alone and none on `time`, so every one of those queries was a full
scan of the whole table: on a site with ~370,000 check-ins the strip took five
to seven seconds to draw, whatever period was asked for.

Two indexes, each earning its place on a measured query:

``(time, employee)``
    The range filter every query starts with, `time >= a AND time < b`. Both
    columns together make it a covering index for the common shape — group
    the period's check-ins by day and employee — so the rows themselves are
    never touched. A one-day view went from 5.7s to 0.18s on that alone.

``(employee, log_type, time)``
    Average hours pairs each first IN with the earliest OUT after it, joining
    the table to itself on employee and a time window. That lookup leads with
    `employee`, which the range index above cannot serve, and `log_type`
    halves what is left to scan. Measured on a month of Kaitet data: 4.5s
    before, 0.37s after, to the same numbers.

A plain `(employee, time)` was measured too and dropped: it took the same
query only to 3.7s, and it is redundant once the three-column one exists.
Check-ins are written constantly by the biometric sync, so an index that does
not earn its keep is a cost on every write.
"""

import frappe

#: (index name, columns). `time` is backticked because it reads as a type name.
INDEXES = (
	("checkin_time_employee", ["`time`", "employee"]),
	("checkin_employee_logtype_time", ["employee", "log_type", "`time`"]),
)


def execute():
	if not frappe.db.exists("DocType", "Employee Checkin"):
		return

	for index_name, columns in INDEXES:
		# add_index is a no-op when the index is already there, and leaves a
		# property setter behind so a later migrate does not drop it
		frappe.db.add_index("Employee Checkin", columns, index_name=index_name)
