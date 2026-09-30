import csv
import datetime
import html
import io
from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cstr, formatdate, get_datetime, getdate, today
from frappe.utils.pdf import get_pdf

try:
	from hrms.hr.utils import get_holiday_list_for_employee
except ImportError:
	get_holiday_list_for_employee = None

MAX_DAYS = 93
PDF_DETAIL_LIMIT = 4000

CATEGORIES = [
	"Present",
	"Present (Not Marked)",
	"Half Day",
	"Work From Home",
	"Clocked In Only",
	"Clocked Out Only",
	"On Leave",
	"Absent",
	"Holiday / Week Off",
]

DETAIL_HEADERS = [
	"Employee",
	"Employee Name",
	"Department",
	"Designation",
	"Date",
	"Shift",
	"Status",
	"Clock In",
	"Clock Out",
	"Hours Worked",
	"Marked Attendance",
	"Leave Type",
]
STATUS_IDX = 6
DATE_IDX = 4

PDF_CSS = """
<style>
	body { font-family: Arial, sans-serif; font-size: 8px; }
	h2 { margin: 0 0 6px; font-size: 13px; }
	h3 { margin: 14px 0 4px; font-size: 10px; }
	table { border-collapse: collapse; width: 100%; }
	thead { display: table-header-group; }
	tr { page-break-inside: avoid; }
	th, td { border: 1px solid #999; padding: 2px 3px; text-align: left; }
	th { background: #1F4E78; color: #fff; }
</style>
"""


@frappe.whitelist()
def export_ta_data(start_date, end_date, company, file_format="Excel"):
	for dt in ("Attendance", "Employee Checkin", "Employee"):
		if not frappe.has_permission(dt, "read"):
			frappe.throw(
				_("You do not have permission to read {0}").format(dt),
				frappe.PermissionError,
			)

	start = getdate(start_date)
	end = min(getdate(end_date), getdate(today()))
	if start > end:
		frappe.throw(_("From date cannot be after To date"))
	if (end - start).days + 1 > MAX_DAYS:
		frappe.throw(_("Please select a range of {0} days or fewer").format(MAX_DAYS))

	details, summary = build_data(start, end, company)
	base = "Attendance_{0}_{1}_to_{2}".format(company, start, end).replace(" ", "_")
	title = "Attendance: {0} ({1} to {2})".format(company, formatdate(start), formatdate(end))

	if file_format == "CSV":
		content = to_csv(details)
		ext = "csv"
	elif file_format == "PDF":
		content = to_pdf(title, details, summary)
		ext = "pdf"
	else:
		content = to_xlsx(details, summary)
		ext = "xlsx"

	frappe.response["filename"] = "{0}.{1}".format(base, ext)
	frappe.response["filecontent"] = content
	frappe.response["type"] = "download"


def get_employees(company, start):
	rows = frappe.get_all(
		"Employee",
		filters={"company": company},
		fields=[
			"name",
			"employee_name",
			"department",
			"designation",
			"date_of_joining",
			"relieving_date",
			"holiday_list",
			"default_shift",
			"status",
		],
		order_by="name asc",
	)
	result = []
	for e in rows:
		if e.status == "Active":
			result.append(e)
		elif e.relieving_date and getdate(e.relieving_date) >= start:
			result.append(e)
	return result


def get_attendance(company, start, end):
	attendance = {}
	rows = frappe.get_all(
		"Attendance",
		filters={
			"company": company,
			"docstatus": 1,
			"attendance_date": ["between", [start, end]],
		},
		fields=[
			"employee",
			"attendance_date",
			"status",
			"shift",
			"in_time",
			"out_time",
			"working_hours",
			"leave_type",
		],
	)
	for a in rows:
		attendance[(a.employee, getdate(a.attendance_date))] = a
	return attendance


def get_checkin_logs(emp_ids, start, end):
	logs = defaultdict(list)
	time_from = "{0} 00:00:00".format(start)
	time_to = "{0} 23:59:59".format(add_days(end, 1))
	rows = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": ["in", emp_ids],
			"time": ["between", [time_from, time_to]],
		},
		fields=["employee", "time", "log_type", "shift", "shift_start"],
		order_by="time asc",
	)
	for c in rows:
		if c.shift_start:
			day = getdate(c.shift_start)
		else:
			day = getdate(c.time)
		if start <= day <= end:
			logs[(c.employee, day)].append(c)
	return logs


class HolidayLookup:
	def __init__(self, company, start, end):
		self.start = start
		self.end = end
		self.company_list = frappe.get_cached_value("Company", company, "default_holiday_list")
		self.cache = {}

	def for_employee(self, emp):
		hl = None
		if get_holiday_list_for_employee:
			try:
				hl = get_holiday_list_for_employee(emp.name, raise_exception=False)
			except Exception:
				hl = None
		hl = hl or emp.holiday_list or self.company_list
		if not hl:
			return set()
		if hl not in self.cache:
			dates = frappe.get_all(
				"Holiday",
				filters={
					"parent": hl,
					"holiday_date": ["between", [self.start, self.end]],
				},
				pluck="holiday_date",
			)
			self.cache[hl] = {getdate(d) for d in dates}
		return self.cache[hl]


def build_data(start, end, company):
	employees = get_employees(company, start)
	if not employees:
		frappe.throw(_("No employees found for {0}").format(company))

	emp_ids = [e.name for e in employees]
	attendance = get_attendance(company, start, end)
	logs = get_checkin_logs(emp_ids, start, end)
	holidays = HolidayLookup(company, start, end)

	dates = []
	d = start
	while d <= end:
		dates.append(getdate(d))
		d = add_days(d, 1)

	details = []
	summary = {}
	for d in dates:
		summary[d] = Counter()

	for emp in employees:
		hol = holidays.for_employee(emp)
		doj = getdate(emp.date_of_joining) if emp.date_of_joining else None
		rel = getdate(emp.relieving_date) if emp.relieving_date else None

		for d in dates:
			if doj and d < doj:
				continue
			if rel and d > rel:
				continue

			a = attendance.get((emp.name, d))
			lg = logs.get((emp.name, d), [])
			row = build_row(emp, d, a, lg, d in hol)
			details.append(row)
			summary[d][row[STATUS_IDX]] += 1

	details.sort(key=lambda r: (r[DATE_IDX], r[0]))
	return details, summary


def build_row(emp, d, a, lg, is_holiday):
	ins = [l.time for l in lg if l.log_type == "IN"]
	outs = [l.time for l in lg if l.log_type == "OUT"]
	untyped = [l.time for l in lg if not l.log_type]

	clock_in = None
	if ins:
		clock_in = ins[0]
	elif untyped:
		clock_in = untyped[0]

	clock_out = None
	if outs:
		clock_out = outs[-1]
	elif len(untyped) > 1:
		clock_out = untyped[-1]

	if a:
		clock_in = a.in_time or clock_in
		clock_out = a.out_time or clock_out

	status = resolve_status(a, clock_in, clock_out, is_holiday)

	hours = ""
	if clock_in and clock_out:
		cin = get_datetime(clock_in)
		cout = get_datetime(clock_out)
		if cout > cin:
			hours = round((cout - cin).total_seconds() / 3600, 2)
	if hours == "" and a and a.working_hours:
		hours = round(a.working_hours, 2)

	shift = ""
	if a and a.shift:
		shift = a.shift
	else:
		for l in lg:
			if l.shift:
				shift = l.shift
				break
	if not shift:
		shift = emp.default_shift or ""

	marked = a.status if a else "Not Marked"
	leave_type = (a.leave_type or "") if a else ""

	return [
		emp.name,
		emp.employee_name,
		emp.department or "",
		emp.designation or "",
		d,
		shift,
		status,
		fmt_time(clock_in, d),
		fmt_time(clock_out, d),
		hours,
		marked,
		leave_type,
	]


def resolve_status(a, clock_in, clock_out, is_holiday):
	if a:
		working = ("Present", "Half Day", "Work From Home")
		if a.status in working and clock_in and not clock_out:
			return "Clocked In Only"
		return a.status
	if clock_in and clock_out:
		return "Present (Not Marked)"
	if clock_in:
		return "Clocked In Only"
	if clock_out:
		return "Clocked Out Only"
	if is_holiday:
		return "Holiday / Week Off"
	return "Absent"


def fmt_time(value, day):
	if not value:
		return ""
	dt = get_datetime(value)
	if dt.date() == day:
		return dt.strftime("%H:%M")
	return dt.strftime("%d-%m-%Y %H:%M")


def summary_table(summary):
	seen = set()
	for cnt in summary.values():
		seen.update(cnt.keys())
	extra = sorted(seen - set(CATEGORIES))
	cats = CATEGORIES + extra

	headers = ["Date", "Headcount"] + cats
	rows = []
	totals = Counter()
	for d in sorted(summary):
		cnt = summary[d]
		rows.append([d, sum(cnt.values())] + [cnt.get(c, 0) for c in cats])
		totals.update(cnt)
	rows.append(["Total", sum(totals.values())] + [totals.get(c, 0) for c in cats])
	return headers, rows


def to_xlsx(details, summary):
	from openpyxl import Workbook
	from openpyxl.styles import Font, PatternFill
	from openpyxl.utils import get_column_letter

	wb = Workbook()
	wb.remove(wb.active)
	header_font = Font(bold=True, color="FFFFFF")
	header_fill = PatternFill("solid", fgColor="1F4E78")

	def add_sheet(title, headers, rows, date_col):
		ws = wb.create_sheet(title)
		ws.append(headers)
		for c in ws[1]:
			c.font = header_font
			c.fill = header_fill
		for r in rows:
			ws.append(r)
		ws.freeze_panes = "A2"
		ws.auto_filter.ref = ws.dimensions
		for col_cells in ws.iter_rows(min_row=2, min_col=date_col, max_col=date_col):
			for c in col_cells:
				if isinstance(c.value, datetime.date):
					c.number_format = "DD-MM-YYYY"
		for i, h in enumerate(headers, 1):
			lengths = [len(cstr(h))]
			for r in rows[:1000]:
				lengths.append(len(cstr(r[i - 1])))
			width = min(max(max(lengths) + 2, 10), 40)
			ws.column_dimensions[get_column_letter(i)].width = width

	s_headers, s_rows = summary_table(summary)
	add_sheet("Summary", s_headers, s_rows, 1)
	add_sheet("All Records", DETAIL_HEADERS, details, DATE_IDX + 1)

	groups = [
		("Present", ("Present", "Present (Not Marked)", "Half Day", "Work From Home")),
		("Clocked In Only", ("Clocked In Only",)),
		("Clocked Out Only", ("Clocked Out Only",)),
		("Absent", ("Absent",)),
		("On Leave", ("On Leave",)),
	]
	for sheet_name, statuses in groups:
		rows = [r for r in details if r[STATUS_IDX] in statuses]
		add_sheet(sheet_name, DETAIL_HEADERS, rows, DATE_IDX + 1)

	out = io.BytesIO()
	wb.save(out)
	return out.getvalue()


def to_csv(details):
	buf = io.StringIO()
	writer = csv.writer(buf)
	writer.writerow(DETAIL_HEADERS)
	for r in details:
		out = []
		for c in r:
			if isinstance(c, datetime.date):
				out.append(formatdate(c))
			else:
				out.append(c)
		writer.writerow(out)
	return buf.getvalue().encode("utf-8-sig")


def pdf_cell(value):
	if isinstance(value, datetime.date):
		value = formatdate(value)
	return html.escape(cstr(value))


def pdf_table(headers, rows):
	head = "".join("<th>" + pdf_cell(h) + "</th>" for h in headers)
	body_parts = []
	for r in rows:
		cells = "".join("<td>" + pdf_cell(c) + "</td>" for c in r)
		body_parts.append("<tr>" + cells + "</tr>")
	body = "".join(body_parts)
	return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>"


def to_pdf(title, details, summary):
	s_headers, s_rows = summary_table(summary)
	clocked_in = [r for r in details if r[STATUS_IDX] == "Clocked In Only"]
	absent = [r for r in details if r[STATUS_IDX] == "Absent"]

	if len(details) <= PDF_DETAIL_LIMIT:
		all_section = pdf_table(DETAIL_HEADERS, details)
	else:
		all_section = (
			"<p><i>" + str(len(details)) + " rows is too many for PDF. "
			"Download Excel for all records.</i></p>"
		)

	parts = [
		PDF_CSS,
		"<h2>" + html.escape(title) + "</h2>",
		"<h3>Daily Summary</h3>",
		pdf_table(s_headers, s_rows),
		"<h3>Clocked In Only (" + str(len(clocked_in)) + ")</h3>",
		pdf_table(DETAIL_HEADERS, clocked_in),
		"<h3>Absent (" + str(len(absent)) + ")</h3>",
		pdf_table(DETAIL_HEADERS, absent),
		"<h3>All Records (" + str(len(details)) + ")</h3>",
		all_section,
	]
	return get_pdf("".join(parts), {"orientation": "Landscape"})
