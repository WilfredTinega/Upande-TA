# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt
"""Candidate employee fetch for the Holiday Assignment Tool tool.

The tool hands a group of employees a holiday list for a window (a public
holiday, a farm shutdown, a rest day the unit takes together). Picking that
group is the whole job of this module: the desk form calls
:func:`get_holiday_assignment_employees` with the filters the user set, shows what
comes back in a checkbox table, and writes the ticked rows into the parent's
``employees`` child table. Nothing here writes anything.

Three things distinguish it from HRMS's own Shift Assignment Tool fetch, which
is otherwise the model it follows:

  * **``custom_farm`` is a first-class filter.** Unit/Division is how Kaitet
    supervisors actually think about a group of employees, and it is the reason
    this tool exists rather than the Bulk Week Off form. It is a custom field,
    so it is applied only where the column exists (see
    :func:`_employee_has_custom_farm`).

  * **The prior holiday list is resolved, not read off the Employee.** HRMS
    ignores ``Employee.holiday_list``: the list in force on a date comes from
    the submitted Holiday List Assignment records. See
    :func:`_resolve_prior_holiday_lists`.

  * **There is a cap.** The shift tool has no LIMIT at all; on Kaitet that is
    ~4100 active employees rendered into a client-side DataTable. This one takes
    a ``limit`` (default 500), reports the true match count alongside the page
    it returns, and lets the UI tell the user to narrow the filters.
"""

import frappe
from erpnext.accounts.utils import build_qb_match_conditions
from frappe import _
from frappe.query_builder import Criterion
from frappe.query_builder.functions import Count
from frappe.utils import add_days, cint, getdate

#: Rows returned in one fetch unless the caller asks for fewer/more.
DEFAULT_LIMIT = 500

#: Hard ceiling, so a hand-crafted call cannot ask for the whole company. A
#: DataTable of this size is already past the point of being usable; the answer
#: to "I need more" is a narrower filter, not a bigger page.
MAX_LIMIT = 2000


def _employee_has_custom_farm() -> bool:
	"""``Employee.custom_farm`` (label "Unit/Division") is a custom field owned by
	upande_kaitet, so it is absent on sites that do not install it. Same guard as
	``upande_ta.upande_ta.api.dashboard``: filter on it only where it exists,
	rather than letting the query blow up with an unknown column."""
	return "custom_farm" in frappe.db.get_table_columns("Employee")


def _build_employee_query(
	*,
	company: str,
	from_date,
	to_date,
	custom_farm: str | None,
	department: str | None,
	designation: str | None,
	has_farm: bool,
	fields: list,
	order_by: str | None = None,
):
	"""The one query both the page fetch and the count run through.

	Permission handling is deliberately split in two, exactly as the Shift
	Assignment Tool does it:

	  * ``frappe.qb.get_query`` applies the role-level read permission and the
	    permitted-field check for the Employee doctype;
	  * ``Criterion.all(build_qb_match_conditions("Employee"))`` applies the
	    *record*-level restrictions — User Permissions, so a farm supervisor
	    restricted to one company or one Unit/Division cannot fetch anyone
	    outside it. That line is the security boundary of this endpoint. Do not
	    drop it; with an empty restriction set it renders to nothing
	    (``Criterion.all([])`` is an ``EmptyCriterion``, which ``.where()``
	    ignores), so it costs nothing on an unrestricted user.
	"""
	Employee = frappe.qb.DocType("Employee")

	filters = [["company", "=", company]]
	if department:
		filters.append(["department", "=", department])
	if designation:
		filters.append(["designation", "=", designation])
	if custom_farm and has_farm:
		filters.append(["custom_farm", "=", custom_farm])

	query = frappe.qb.get_query(
		Employee,
		fields=fields,
		filters=filters,
		order_by=order_by,
	).where(
		(Employee.status == "Active")
		# date_of_joining is mandatory on Employee, so a strict comparison does
		# not silently drop rows the way a NULL-intolerant filter would elsewhere.
		& (Employee.date_of_joining <= from_date)
		& ((Employee.relieving_date >= from_date) | (Employee.relieving_date.isnull()))
	)

	if to_date:
		query = query.where((Employee.relieving_date >= to_date) | (Employee.relieving_date.isnull()))

	# Employees who already hold an assignment on from_date are *not* excluded:
	# the tool replaces whatever starts inside its window, so re-running it is
	# how a run is changed.
	return query.where(Criterion.all(build_qb_match_conditions("Employee")))


def _resolve_prior_holiday_lists(employees: list[dict], from_date) -> None:
	"""Stamp each row with the holiday list the employee was on the day before
	the override starts — what the tool must restore, and what the user needs to
	see before overwriting it.

	This deliberately does **not** read ``Employee.holiday_list``. HRMS resolves
	the holiday list for a date from submitted Holiday List Assignment records
	(``hrms.utils.holiday_list.get_assigned_holiday_list``); the Employee field
	is not consulted by the attendance or leave code at all, so on a site that
	has been using Holiday List Assignments it is routinely stale or blank.

	``from_date - 1`` is the right instant to ask about: asking as at
	``from_date`` itself would return the new assignment once one exists, which
	makes the value useless as a "restore to this" record.

	``Employee.holiday_list`` is used only as a last-resort fallback, for
	employees who have never been given a Holiday List Assignment (an untouched
	site, or someone set up before the tool was introduced). It is better than a
	blank cell, but it is a guess, not the effective list.
	"""
	from hrms.utils.holiday_list import get_assigned_holiday_list

	as_on = add_days(getdate(from_date), -1)

	for row in employees:
		fallback = row.pop("default_holiday_list", None)
		try:
			assigned = get_assigned_holiday_list(row["employee"], as_on=as_on)
		except Exception:
			assigned = None
			frappe.log_error(
				title="Holiday Assignment Tool: prior holiday list lookup failed",
				message=frappe.get_traceback(),
			)
		row["prior_holiday_list"] = assigned or fallback or None


@frappe.whitelist()
def get_holiday_assignment_employees(
	company: str,
	from_date: str,
	to_date: str | None = None,
	custom_farm: str | None = None,
	department: str | None = None,
	designation: str | None = None,
	limit: int | str | None = None,
	with_holiday_list: int | str = 1,
) -> dict:
	"""Active employees matching the Holiday Assignment Tool filters.

	Args:
	        company: required. Everything else narrows within it.
	        from_date: required. The date the holiday override starts; it defines
	                the employment window and the prior-holiday-list lookup.
	        to_date: optional end of the window. When given, employees relieved
	                before it are dropped too.
	        custom_farm: optional Unit/Division (``Employee.custom_farm``).
	                Ignored on sites without that custom field.
	        department, designation: optional.
	        with_holiday_list: 0 skips the prior-holiday-list lookup (one query
	                per employee) for callers that do not show it, such as
	                Overtime Request.
	        limit: page size, default :data:`DEFAULT_LIMIT`, capped at
	                :data:`MAX_LIMIT`.

	Returns:
	        {
	            "employees": [ {employee, employee_name, department, designation,
	                            custom_farm, prior_holiday_list}, ... ],
	            "count": <rows returned>,
	            "total": <employees matching the filters, ignoring the cap>,
	            "truncated": <bool: total > count>,
	            "limit": <cap actually applied>,
	        }

	``truncated`` is not cosmetic: the caller must show it. Silently returning
	500 of 4100 employees and letting the user submit is how a "bulk" tool
	quietly does a third of the job.
	"""
	if not company:
		frappe.throw(_("Company is required to fetch employees."), title=_("Missing Filter"))
	if not from_date:
		frappe.throw(_("From Date is required to fetch employees."), title=_("Missing Filter"))

	from_date = getdate(from_date)
	to_date = getdate(to_date) if to_date else None
	if to_date and to_date < from_date:
		frappe.throw(_("To Date cannot be before From Date."), title=_("Invalid Period"))

	limit = cint(limit) or DEFAULT_LIMIT
	limit = max(1, min(limit, MAX_LIMIT))

	has_farm = _employee_has_custom_farm()

	fields = [
		"name as employee",
		"employee_name",
		"department",
		"designation",
		# Fallback only; dropped from the row by _resolve_prior_holiday_lists.
		"holiday_list as default_holiday_list",
	]
	if has_farm:
		fields.append("custom_farm")

	query_args = dict(
		company=company,
		from_date=from_date,
		to_date=to_date,
		custom_farm=custom_farm,
		department=department,
		designation=designation,
		has_farm=has_farm,
	)

	# limit + 1: one extra row is all it takes to know the set was truncated,
	# and it saves the COUNT query entirely in the common case.
	employees = (
		_build_employee_query(**query_args, fields=fields, order_by="employee_name asc")
		.limit(limit + 1)
		.run(as_dict=True)
	)

	truncated = len(employees) > limit
	if truncated:
		employees = employees[:limit]
		base = _build_employee_query(**query_args, fields=["name"])
		total = cint(frappe.qb.from_(base).select(Count("*")).run()[0][0])
	else:
		total = len(employees)

	if not has_farm:
		for row in employees:
			row["custom_farm"] = None

	if cint(with_holiday_list):
		_resolve_prior_holiday_lists(employees, from_date)
	else:
		for row in employees:
			row.pop("default_holiday_list", None)

	return {
		"employees": employees,
		"count": len(employees),
		"total": total,
		"truncated": truncated,
		"limit": limit,
	}
