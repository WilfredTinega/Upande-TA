// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.pages["exchange-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Exchange Dashboard"),
		single_column: true,
	});
	wrapper.exchange_dashboard = new ExchangeDashboard(page);
};

frappe.pages["exchange-dashboard"].on_page_show = function (wrapper) {
	wrapper.exchange_dashboard && wrapper.exchange_dashboard.refresh();
};

const EXD_FLAG_COLOR = {
	"On Program": "green",
	"Ending Soon": "orange",
	"Past End Date": "red",
	"Not Started": "blue",
	Ended: "gray",
};

class ExchangeDashboard {
	constructor(page) {
		this.page = page;
		this.make_filters();
		this.$body = $(`<div class="exd"></div>`).appendTo(page.main);
		this.page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.page.add_inner_button(__("Exchange Employees"), () =>
			frappe.set_route("List", "Exchange Employee"),
		);
		this.page.add_inner_button(__("HQ Employees"), () =>
			frappe.set_route("List", "HQ Employee"),
		);
		this.page.add_inner_button(__("Exchange Checkins"), () =>
			frappe.set_route("List", "Exchange Checkin"),
		);
		this.style();
	}

	make_filters() {
		const change = () => this.refresh();
		this.filters = {
			from_date: this.page.add_field({
				fieldname: "from_date",
				label: __("From"),
				fieldtype: "Date",
				default: frappe.datetime.add_days(frappe.datetime.get_today(), -29),
				change,
			}),
			to_date: this.page.add_field({
				fieldname: "to_date",
				label: __("To"),
				fieldtype: "Date",
				default: frappe.datetime.get_today(),
				change,
			}),
			company: this.page.add_field({
				fieldname: "company",
				label: __("Host Company"),
				fieldtype: "Link",
				options: "Company",
				change,
			}),
			home_instance: this.page.add_field({
				fieldname: "home_instance",
				label: __("Home Instance"),
				fieldtype: "Select",
				options: [""],
				change,
			}),
			status: this.page.add_field({
				fieldname: "status",
				label: __("Status"),
				fieldtype: "Select",
				options: ["", "Active", "Ended"],
				change,
			}),
		};
		frappe.xcall("upande_ta.upande_ta.exchange.instance_codes").then((codes) => {
			const f = this.filters.home_instance;
			f.df.options = [""].concat(codes || []);
			f.refresh();
		});
	}

	values() {
		const v = {};
		Object.entries(this.filters).forEach(([k, f]) => (v[k] = f.get_value() || null));
		return v;
	}

	refresh() {
		if (this.loading) return;
		this.loading = true;
		frappe
			.xcall(
				"upande_ta.upande_ta.page.exchange_dashboard.exchange_dashboard.get_data",
				this.values(),
			)
			.then((data) => this.render(data))
			.finally(() => (this.loading = false));
	}

	render(d) {
		const s = d.summary;
		const range = `${frappe.datetime.str_to_user(d.from_date)} – ${frappe.datetime.str_to_user(d.to_date)}`;
		this.$body.html(`
			<div class="exd-cards">
				${this.card(__("Exchange Employees"), s.total)}
				${this.card(__("Active"), s.active, "green")}
				${this.card(__("Ended"), s.ended, "gray")}
				${this.card(__("Added"), s.added, "blue", range)}
				${this.card(__("Present Today"), s.present_today, "green")}
				${this.card(__("Hours Worked"), format_number(s.hours, null, 1), "blue", range)}
				${this.card(__("Ending in 14 Days"), s.ending_soon, "orange")}
				${this.card(__("Past End Date"), s.overdue, "red")}
			</div>
			<div class="exd-grid">
				<div class="exd-panel"><div class="exd-title">${__("Employees per Company")}</div><div class="exd-chart" data-chart="company"></div></div>
				<div class="exd-panel"><div class="exd-title">${__("Hours Worked per Company")}</div><div class="exd-chart" data-chart="hours"></div></div>
				<div class="exd-panel"><div class="exd-title">${__("Program End Dates")}</div><div class="exd-chart" data-chart="ending"></div></div>
				<div class="exd-panel"><div class="exd-title">${__("Home Instance")}</div><div class="exd-chart" data-chart="instance"></div></div>
			</div>
			<div class="exd-panel">
				<div class="exd-title">${__("Per Company")}</div>
				${this.company_table(d.companies)}
			</div>
			<div class="exd-panel">
				<div class="exd-title">${__("Program Duration and End Date")}</div>
				${this.employee_table(d.employees)}
			</div>
			<div class="exd-panel">
				<div class="exd-title">${__("Check-in and Check-out")}</div>
				${this.daily_table(d.daily)}
			</div>
			${this.hq_section(d.hq || {}, range)}
		`);
		this.charts(d);
		this.$body.find("[data-company]").on("click", (e) => {
			const c = $(e.currentTarget).attr("data-company");
			this.filters.company.set_value(c === __("Not Set") ? "" : c);
		});
		this.$body.find("[data-employee]").on("click", (e) => {
			const $row = $(e.currentTarget);
			frappe.set_route(
				"Form",
				$row.attr("data-doctype") || "Exchange Employee",
				$row.attr("data-employee"),
			);
		});
	}

	hq_section(hq, range) {
		const s = hq.summary || {};
		if (!s.total) return "";
		const e = frappe.utils.escape_html;
		const people = this.table(
			[
				[__("Employee")],
				[__("Payroll No.")],
				[__("Home")],
				[__("Host Company")],
				[__("Status")],
				[__("Days Worked"), "text-right"],
				[__("Hours Worked"), "text-right"],
				[__("Avg Hours / Day"), "text-right"],
				[__("Last Punch")],
			],
			(hq.employees || []).map(
				(
					r,
				) => `<tr class="exd-click" data-doctype="HQ Employee" data-employee="${e(r.name)}">
				<td><b>${e(r.full_name || r.name)}</b></td>
				<td>${e(r.payroll_number || "")}</td>
				<td>${e(r.home_instance || "")}</td>
				<td>${e(r.host_company || "")}</td>
				<td><span class="indicator-pill ${r.status === "Active" ? "green" : "gray"}">${__(r.status)}</span></td>
				<td class="text-right">${r.days_worked}</td>
				<td class="text-right">${format_number(r.hours, null, 2)}</td>
				<td class="text-right">${format_number(r.avg_hours, null, 2)}</td>
				<td>${r.last_punch ? frappe.datetime.str_to_user(r.last_punch) : ""}</td>
			</tr>`,
			),
		);
		const companies = this.table(
			[
				[__("Host Company")],
				[__("Employees"), "text-right"],
				[__("Active"), "text-right"],
				[__("Days Worked"), "text-right"],
				[__("Hours Worked"), "text-right"],
				[__("Avg Hours / Day"), "text-right"],
			],
			(hq.companies || []).map(
				(c) => `<tr><td><b>${e(c.company)}</b></td>
				<td class="text-right">${c.employees}</td>
				<td class="text-right">${c.active}</td>
				<td class="text-right">${c.days}</td>
				<td class="text-right">${format_number(c.hours, null, 2)}</td>
				<td class="text-right">${format_number(c.avg_hours, null, 2)}</td></tr>`,
			),
		);
		const daily = this.daily_table(hq.daily || [], "HQ Employee");
		return `
			<div class="exd-section-title">${__("HQ Employees")}</div>
			<div class="exd-cards">
				${this.card(__("HQ Employees"), s.total)}
				${this.card(__("Active"), s.active, "green")}
				${this.card(__("Present Today"), s.present_today, "green")}
				${this.card(__("Hours Worked"), format_number(s.hours, null, 1), "blue", range)}
				${this.card(__("Days Worked"), s.days_worked, "blue", range)}
			</div>
			<div class="exd-panel"><div class="exd-title">${__("Per Company")}</div>${companies}</div>
			<div class="exd-panel"><div class="exd-title">${__("HQ Employees")}</div>${people}</div>
			<div class="exd-panel"><div class="exd-title">${__("Check-in and Check-out")}</div>${daily}</div>`;
	}

	card(label, value, color, sub) {
		return `<div class="exd-card ${color ? "exd-" + color : ""}">
			<div class="exd-card-label">${label}</div>
			<div class="exd-card-value">${value}</div>
			${sub ? `<div class="exd-card-sub">${sub}</div>` : ""}
		</div>`;
	}

	charts(d) {
		const empty = (el) => $(el).html(`<div class="exd-empty">${__("Nothing yet")}</div>`);
		const at = (name) => this.$body.find(`[data-chart="${name}"]`)[0];
		const companies = d.companies;
		if (companies.length) {
			new frappe.Chart(at("company"), {
				type: "bar",
				height: 240,
				barOptions: { stacked: 1 },
				colors: ["#29CD42", "#A6B1B9"],
				data: {
					labels: companies.map((c) => c.company),
					datasets: [
						{ name: __("Active"), values: companies.map((c) => c.active) },
						{ name: __("Ended"), values: companies.map((c) => c.ended) },
					],
				},
			});
			new frappe.Chart(at("hours"), {
				type: "bar",
				height: 240,
				colors: ["#5E64FF"],
				data: {
					labels: companies.map((c) => c.company),
					datasets: [{ name: __("Hours"), values: companies.map((c) => c.hours) }],
				},
			});
		} else {
			empty(at("company"));
			empty(at("hours"));
		}
		if (d.ending.length) {
			new frappe.Chart(at("ending"), {
				type: "bar",
				height: 240,
				colors: ["#FF9F43"],
				data: {
					labels: d.ending.map((m) => moment(m.month, "YYYY-MM").format("MMM YYYY")),
					datasets: [
						{
							name: __("Active Employees Ending"),
							values: d.ending.map((m) => m.employees),
						},
					],
				},
			});
		} else {
			empty(at("ending"));
		}
		if (d.instances.length) {
			new frappe.Chart(at("instance"), {
				type: "donut",
				height: 240,
				data: {
					labels: d.instances.map((i) => i.instance),
					datasets: [{ values: d.instances.map((i) => i.employees) }],
				},
			});
		} else {
			empty(at("instance"));
		}
	}

	table(head, rows) {
		if (!rows.length) return `<div class="exd-empty">${__("Nothing yet")}</div>`;
		return `<div class="exd-table-wrap"><table class="table table-bordered exd-table">
			<thead><tr>${head.map((h) => `<th class="${h[1] || ""}">${h[0]}</th>`).join("")}</tr></thead>
			<tbody>${rows.join("")}</tbody></table></div>`;
	}

	company_table(companies) {
		const e = frappe.utils.escape_html;
		return this.table(
			[
				[__("Host Company")],
				[__("Employees"), "text-right"],
				[__("Active"), "text-right"],
				[__("Ended"), "text-right"],
				[__("Added"), "text-right"],
				[__("Days Worked"), "text-right"],
				[__("Hours Worked"), "text-right"],
				[__("Avg Hours / Day"), "text-right"],
			],
			companies.map(
				(c) => `<tr class="exd-click" data-company="${e(c.company)}">
				<td><b>${e(c.company)}</b></td>
				<td class="text-right">${c.employees}</td>
				<td class="text-right">${c.active}</td>
				<td class="text-right">${c.ended}</td>
				<td class="text-right">${c.added}</td>
				<td class="text-right">${c.days}</td>
				<td class="text-right">${format_number(c.hours, null, 2)}</td>
				<td class="text-right">${format_number(c.avg_hours, null, 2)}</td>
			</tr>`,
			),
		);
	}

	employee_table(employees) {
		const e = frappe.utils.escape_html;
		const date = (v) => (v ? frappe.datetime.str_to_user(v) : "");
		return this.table(
			[
				[__("Employee")],
				[__("Payroll No.")],
				[__("Home")],
				[__("Host Company")],
				[__("Start")],
				[__("End")],
				[__("Duration (Days)"), "text-right"],
				[__("Days Left"), "text-right"],
				[__("Progress")],
				[__("Status")],
				[__("Days Worked"), "text-right"],
				[__("Hours Worked"), "text-right"],
				[__("Avg Hours / Day"), "text-right"],
			],
			employees.map((r) => {
				const color = EXD_FLAG_COLOR[r.flag] || "gray";
				const left =
					r.remaining === null || r.remaining === undefined
						? ""
						: r.remaining < 0
							? `<span class="text-danger">${__("{0} over", [-r.remaining])}</span>`
							: r.remaining;
				const bar =
					r.progress === null || r.progress === undefined
						? ""
						: `<div class="exd-bar"><div class="exd-bar-fill exd-bg-${color}" style="width:${r.progress}%"></div></div>
						   <span class="exd-bar-text">${r.progress}%</span>`;
				return `<tr class="exd-click" data-employee="${e(r.name)}">
					<td><b>${e(r.full_name || r.name)}</b></td>
					<td>${e(r.payroll_number || "")}</td>
					<td>${e(r.home_instance || "")}</td>
					<td>${e(r.host_company || "")}</td>
					<td>${date(r.program_start)}</td>
					<td>${date(r.program_end)}</td>
					<td class="text-right">${r.duration ?? ""}</td>
					<td class="text-right">${left}</td>
					<td class="exd-progress">${bar}</td>
					<td><span class="indicator-pill ${color}">${__(r.flag)}</span></td>
					<td class="text-right">${r.days_worked}</td>
					<td class="text-right">${format_number(r.hours, null, 2)}</td>
					<td class="text-right">${format_number(r.avg_hours, null, 2)}</td>
				</tr>`;
			}),
		);
	}

	daily_table(days, doctype = "Exchange Employee") {
		const e = frappe.utils.escape_html;
		const time = (v) => (v ? moment(v).format("HH:mm") : "");
		return this.table(
			[
				[__("Date")],
				[__("Employee")],
				[__("Company")],
				[__("Check-in")],
				[__("Check-out")],
				[__("Punches"), "text-right"],
				[__("Hours"), "text-right"],
			],
			days.map(
				(
					r,
				) => `<tr class="exd-click" data-doctype="${e(doctype)}" data-employee="${e(r.employee)}">
				<td>${frappe.datetime.str_to_user(r.date)}</td>
				<td>${e(r.full_name || r.employee)}</td>
				<td>${e(r.company || "")}</td>
				<td>${time(r.first_in)}</td>
				<td>${r.last_out ? time(r.last_out) : `<span class="text-muted">${__("Missing")}</span>`}</td>
				<td class="text-right">${r.punches}</td>
				<td class="text-right">${format_number(r.hours, null, 2)}</td>
			</tr>`,
			),
		);
	}

	style() {
		if (document.getElementById("exd-style")) return;
		$(`<style id="exd-style">
			.exd { padding: 4px 0 24px; }
			.exd-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; margin-bottom: 16px; }
			.exd-card { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md); padding: 14px 16px; border-left: 3px solid var(--gray-400); }
			.exd-card.exd-green { border-left-color: var(--green-500); }
			.exd-card.exd-blue { border-left-color: var(--blue-500); }
			.exd-card.exd-orange { border-left-color: var(--orange-500); }
			.exd-card.exd-red { border-left-color: var(--red-500); }
			.exd-card.exd-gray { border-left-color: var(--gray-500); }
			.exd-card-label { color: var(--text-muted); font-size: var(--text-sm); }
			.exd-card-value { font-size: 1.6rem; font-weight: 600; color: var(--text-color); margin-top: 2px; }
			.exd-card-sub { color: var(--text-muted); font-size: var(--text-xs); }
			.exd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; }
			.exd-panel { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md); padding: 14px 16px; margin-bottom: 12px; min-width: 0; }
			.exd-section-title { font-size: 1.1rem; font-weight: 600; margin: 24px 0 12px; color: var(--text-color); }
			.exd-title { font-weight: 600; margin-bottom: 8px; color: var(--text-color); }
			.exd-empty { color: var(--text-muted); padding: 32px 0; text-align: center; }
			.exd-table-wrap { overflow-x: auto; max-height: 520px; overflow-y: auto; }
			.exd-table { margin: 0; font-size: var(--text-sm); }
			.exd-table th { white-space: nowrap; position: sticky; top: 0; background: var(--subtle-fg); z-index: 1; }
			.exd-table td { white-space: nowrap; vertical-align: middle !important; }
			.exd-click { cursor: pointer; }
			.exd-click:hover td { background: var(--highlight-color); }
			.exd-progress { min-width: 150px; }
			.exd-bar { display: inline-block; width: 100px; height: 8px; background: var(--gray-200); border-radius: 4px; overflow: hidden; vertical-align: middle; }
			.exd-bar-fill { height: 100%; }
			.exd-bar-text { margin-left: 6px; color: var(--text-muted); font-size: var(--text-xs); }
			.exd-bg-green { background: var(--green-500); } .exd-bg-orange { background: var(--orange-500); }
			.exd-bg-red { background: var(--red-500); } .exd-bg-blue { background: var(--blue-500); } .exd-bg-gray { background: var(--gray-500); }
		</style>`).appendTo("head");
	}
}
