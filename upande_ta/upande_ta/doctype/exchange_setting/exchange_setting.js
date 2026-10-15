// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

const EXS = "upande_ta.upande_ta.exchange.";

$(`<style>
	.exs-instances [data-fieldname="test_connection"] .btn,
	.exs-instances [data-fieldname="push_now"] .btn { white-space: nowrap; }
</style>`).appendTo("head");

frappe.ui.form.on("Exchange Setting", {
	setup(frm) {
		frm.set_query("department", "company_map", (doc, cdt, cdn) => ({
			filters: { company: locals[cdt][cdn].company },
		}));
	},

	refresh(frm) {
		frm.add_custom_button(__("Sync Log"), () => frappe.set_route("List", "Exchange Sync Log"));
		frm.add_custom_button(__("Exchange Employees"), () =>
			frappe.set_route("List", "Exchange Employee"),
		);
		frm.fields_dict.instances.$wrapper.addClass("exs-instances");
		exs_instance_options(frm);
		exs_render_status(frm);
		(frm.doc.instances || []).forEach((row) =>
			exs_remote_companies(frm, row.doctype, row.name, true),
		);
	},

	generate_api_key(frm) {
		const run = () =>
			frappe.xcall(EXS + "generate_api_key").then((r) => {
				frm.reload_doc();
				exs_show_token(r.token);
			});
		if (frm.is_dirty()) {
			frm.save().then(run);
		} else if (frm.doc.api_key) {
			frappe.confirm(
				__(
					"The old token stops working on every instance that uses it. Generate a new one?",
				),
				run,
			);
		} else {
			run();
		}
	},
});

frappe.ui.form.on("Exchange Instance", {
	instance_code: exs_instance_options,
	instances_remove: exs_instance_options,

	form_render(frm, cdt, cdn) {
		exs_remote_companies(frm, cdt, cdn, true);
	},
	base_url: exs_remote_companies,
	api_token: exs_remote_companies,

	test_connection(frm, cdt, cdn) {
		exs_row_action(frm, cdt, cdn, "test_connection", (m, row) =>
			__("Connected to {0}", [m.instance_code || row.instance_code]),
		);
	},

	push_now(frm, cdt, cdn) {
		exs_row_action(frm, cdt, cdn, "push_now", (m) =>
			__("{0} sent, {1} failed", [m.sent || 0, m.failed || 0]),
		);
	},
});

frappe.ui.form.on("Exchange Company Map", {
	/** Their company starts as the company chosen on that instance's row. */
	instance(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const inst = (frm.doc.instances || []).find((r) => r.instance_code === row.instance);
		if (inst && inst.company && !row.home_company) {
			frappe.model.set_value(cdt, cdn, "home_company", inst.company);
		}
	},
});

/** The Company field of an Instances row lists the companies on that
 * instance, read with the URL and token typed into the row. */
const exs_company_cache = {};
/** ``quiet`` (the form or a row opening) fetches without error messages;
 * typing a URL, key or secret shows what went wrong. */
function exs_remote_companies(frm, cdt, cdn, quiet) {
	const row = locals[cdt][cdn];
	if (!row.base_url || !row.api_token) return;
	const key = [row.base_url, row.api_token].join("|");
	const apply = (companies) => {
		// the Company cell in the grid and, when the row is open, its form
		const grid_row = frm.fields_dict.instances.grid.grid_rows_by_docname[cdn];
		const fields = [
			grid_row && grid_row.on_grid_fields_dict && grid_row.on_grid_fields_dict.company,
			grid_row && grid_row.grid_form && grid_row.grid_form.fields_dict.company,
		].filter(Boolean);
		fields.forEach((field) => {
			field.df.options = companies;
			if (field.set_data) field.set_data(companies);
		});
	};
	if (exs_company_cache[key]) {
		apply(exs_company_cache[key]);
		return;
	}
	frappe
		.xcall(EXS + "remote_companies", {
			base_url: row.base_url,
			api_token: row.api_token,
			row_name: row.__islocal ? null : row.name,
			instance_code: row.instance_code,
			quiet: quiet === true ? 1 : 0,
		})
		.then((companies) => {
			companies = companies || [];
			// an empty answer is not kept, so the next try asks again
			if (companies.length) exs_company_cache[key] = companies;
			apply(companies);
			if (!(companies || []).length && quiet !== true) {
				frappe.show_alert({
					message: __("No companies came back from {0}", [row.base_url]),
					indicator: "orange",
				});
			} else if (companies.length === 1 && !row.company) {
				frappe.model.set_value(cdt, cdn, "company", companies[0]);
			}
		});
}

/** The row buttons call the server about a saved row, so unsaved edits are
 * saved first. */
function exs_row_action(frm, cdt, cdn, method, describe) {
	const row = locals[cdt][cdn];
	if (!row.instance_code) {
		frappe.msgprint(__("Set the Instance Code first."));
		return;
	}
	const run = () =>
		frappe
			.xcall(EXS + method, { instance_code: row.instance_code }, null, { freeze: true })
			.then((m) => {
				frappe.show_alert({ message: describe(m || {}, row), indicator: "green" });
				frm.reload_doc();
			});
	if (frm.is_dirty()) frm.save().then(run);
	else run();
}

function exs_instance_options(frm) {
	const codes = (frm.doc.instances || []).map((r) => r.instance_code).filter(Boolean);
	frm.fields_dict.company_map.grid.update_docfield_property("instance", "options", codes);
}

function exs_render_status(frm) {
	const status = (frm.doc.__onload || {}).sync_status || {};
	const rows = (frm.doc.instances || []).map((r) => {
		const s = status[r.instance_code] || {};
		const pill = (n, label, color) =>
			n
				? `<span class="indicator-pill ${color}" style="margin-right:6px">${n} ${__(label)}</span>`
				: "";
		return `<tr>
			<td><b>${frappe.utils.escape_html(r.instance_code || "")}</b></td>
			<td>${r.connection_status ? frappe.utils.escape_html(__(r.connection_status)) : ""}</td>
			<td>${r.last_success ? frappe.datetime.str_to_user(r.last_success) : ""}</td>
			<td>${pill(s.Sent, "Sent", "green")}${pill(s.Queued, "Queued", "orange")}${pill(s.Sending, "Sending", "blue")}${pill(
				s.Failed,
				"Failed",
				"red",
			)}</td>
			<td class="text-muted">${frappe.utils.escape_html(r.last_error || "")}</td>
		</tr>`;
	});
	frm.get_field("sync_status").$wrapper.html(
		rows.length
			? `<table class="table table-bordered" style="margin-bottom:0">
				<thead><tr><th>${__("Instance")}</th><th>${__("Status")}</th><th>${__("Last Success")}</th>
				<th>${__("Sync Log")}</th><th>${__("Last Error")}</th></tr></thead>
				<tbody>${rows.join("")}</tbody></table>`
			: `<p class="text-muted">${__("Add the other instances in the Instances tab.")}</p>`,
	);
}

function exs_show_token(token) {
	const d = new frappe.ui.Dialog({
		title: __("Token for the Other Instances"),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "note",
				options: `<p class="text-muted">${__(
					"Copy it now: the secret is shown only this once. On each other instance, paste it into API Key:Secret on this instance's row in Exchange Setting → Instances.",
				)}</p>`,
			},
			{
				fieldtype: "Data",
				fieldname: "token",
				label: __("API Key:Secret"),
				read_only: 1,
				default: token,
			},
		],
		primary_action_label: __("Copy"),
		primary_action() {
			frappe.utils.copy_to_clipboard(token);
		},
	});
	d.show();
}
