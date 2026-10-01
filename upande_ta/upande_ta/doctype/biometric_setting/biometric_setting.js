// Copyright (c) 2026, Upande LTD and contributors

(function inject_sticky_head_styles() {
	if (document.getElementById("upande-ta-sticky-head-styles")) return;
	const style = document.createElement("style");
	style.id = "upande-ta-sticky-head-styles";
	style.textContent = `
		.sticky-head-table thead th {
			position: sticky;
			top: 0;
			z-index: 2;
			background: var(--bg-color, #f3f3f3);
			background-clip: padding-box;
			border-bottom: 1px solid var(--border-color, #d1d8dd);
			box-shadow: inset 0 -1px 0 var(--border-color, #d1d8dd);
		}
		[data-theme="dark"] .sticky-head-table thead th {
			background: var(--gray-800, #333);
		}

		/* grid.scss paints .grid-heading-row with a raw --gray-600, and that
		   token gets DARKER on the dark theme (#7c7c7c light -> #383838 dark),
		   so the header text ends up near-invisible on the dark panel. Point
		   this form's grids at the semantic --text-color instead, which flips
		   properly (--ink-gray-8: #171717 light -> #d9d9d9 dark). Light mode is
		   untouched — every rule is gated on the theme attribute — and the
		   .upande-bio-grid scope keeps the rest of the desk alone. */
		[data-theme="dark"] .upande-bio-grid .grid-heading-row,
		[data-theme="dark"] .upande-bio-grid .grid-heading-row .col,
		[data-theme="dark"] .upande-bio-grid .grid-heading-row .static-area,
		[data-theme="dark"] .upande-bio-grid .grid-heading-row .grid-static-col {
			color: var(--text-color);
		}
		[data-theme="dark"] .upande-bio-grid .grid-body .grid-static-col,
		[data-theme="dark"] .upande-bio-grid .grid-body .grid-static-col .static-area,
		[data-theme="dark"] .upande-bio-grid .grid-body .grid-row .col {
			color: var(--text-color);
		}
		/* Placeholders stay muted — "Click to set farms" must not read as data.
		   The status pill keeps its green/red: those rules use !important. */
		[data-theme="dark"] .upande-bio-grid .grid-body .missing-value,
		[data-theme="dark"] .upande-bio-grid .grid-body .text-muted,
		[data-theme="dark"] .upande-bio-grid .grid-body .grid-empty {
			color: var(--text-muted);
		}

		.grid-row [data-fieldname="status"] .field-area { display: none !important; }
		.grid-row [data-fieldname="status"] .static-area { display: block !important; }

		.grid-static-col[data-fieldname="status"] .bio-status-pill {
			display: inline-flex;
			align-items: center;
			gap: 6px;
			font-weight: 600;
		}
		.grid-static-col[data-bio-status="online"] .bio-status-pill {
			color: var(--green-600, #198754) !important;
		}
		.grid-static-col[data-bio-status="offline"] .bio-status-pill {
			color: var(--red-600, #dc3545) !important;
		}

		.bulk-user-table-fixed {
			border-collapse: separate;
			border-spacing: 0;
			width: auto !important;
			min-width: 100%;
			table-layout: auto;
		}
		.bulk-user-table-fixed td.bulk-col-pin,
		.bulk-user-table-fixed th.bulk-col-pin {
			position: sticky;
			left: 0;
			width: 90px;
			min-width: 90px;
			max-width: 90px;
			background: var(--bg-color, #fff);
			z-index: 5;
		}
		.bulk-user-table-fixed td.bulk-col-name,
		.bulk-user-table-fixed th.bulk-col-name {
			position: sticky;
			left: 90px;
			width: 220px;
			min-width: 220px;
			max-width: 220px;
			background: var(--bg-color, #fff);
			z-index: 5;
			box-shadow: 2px 0 0 var(--border-color, #d1d8dd);
		}
		.bulk-user-table-fixed:has(.bulk-col-skip) td.bulk-col-name,
		.bulk-user-table-fixed:has(.bulk-col-skip) th.bulk-col-name,
		.bulk-user-table-fixed:has(.bulk-col-priv) td.bulk-col-name,
		.bulk-user-table-fixed:has(.bulk-col-priv) th.bulk-col-name {
			box-shadow: none;
		}
		.bulk-user-table-fixed:has(.bulk-col-priv) td.bulk-col-skip,
		.bulk-user-table-fixed:has(.bulk-col-priv) th.bulk-col-skip {
			box-shadow: none;
		}
		.bulk-user-table-fixed thead th.bulk-col-pin,
		.bulk-user-table-fixed thead th.bulk-col-name {
			z-index: 6;
		}
		[data-theme="dark"] .bulk-user-table-fixed td.bulk-col-pin,
		[data-theme="dark"] .bulk-user-table-fixed th.bulk-col-pin,
		[data-theme="dark"] .bulk-user-table-fixed td.bulk-col-name,
		[data-theme="dark"] .bulk-user-table-fixed th.bulk-col-name {
			background: var(--gray-900, #1f1f1f);
		}
		.bulk-user-table-fixed td.bulk-col-skip,
		.bulk-user-table-fixed th.bulk-col-skip {
			position: sticky;
			left: 310px;
			width: 90px;
			min-width: 90px;
			max-width: 90px;
			background: var(--bg-color, #fff);
			z-index: 5;
			box-shadow: 2px 0 0 var(--border-color, #d1d8dd);
		}
		.bulk-user-table-fixed thead th.bulk-col-skip {
			z-index: 6;
		}
		[data-theme="dark"] .bulk-user-table-fixed td.bulk-col-skip,
		[data-theme="dark"] .bulk-user-table-fixed th.bulk-col-skip {
			background: var(--gray-900, #1f1f1f);
		}
		.bulk-user-table-fixed td.bulk-col-priv,
		.bulk-user-table-fixed th.bulk-col-priv {
			position: sticky;
			left: 400px;
			width: 120px;
			min-width: 120px;
			max-width: 120px;
			background: var(--bg-color, #fff);
			z-index: 5;
			box-shadow: 2px 0 0 var(--border-color, #d1d8dd);
		}
		.bulk-user-table-fixed thead th.bulk-col-priv {
			z-index: 6;
		}
		[data-theme="dark"] .bulk-user-table-fixed td.bulk-col-priv,
		[data-theme="dark"] .bulk-user-table-fixed th.bulk-col-priv {
			background: var(--gray-900, #1f1f1f);
		}
	`;
	document.head.appendChild(style);
})();

frappe.ui.form.on("Biometric Setting", {
	refresh: function(frm) {
		make_primary(frm, "get_checkin");
		make_primary(frm, "absent_run_now");
		make_primary(frm, "get_bio");
		make_primary(frm, "update");
		if (!frm.doc.date) {
			frm.doc.date = frappe.datetime.get_today();
			frm.refresh_field("date");
		}
		if (!frm.doc.absent_preview_date) {
			frm.doc.absent_preview_date = frappe.datetime.get_today();
			frm.refresh_field("absent_preview_date");
		}
		refresh_device_options(frm);
		backfill_poll_device_sns(frm);
		render_users_tab(frm);
		render_biodata_tab(frm);
		render_scheduled_job_links(frm);
		guard_devices_delete(frm);
		paint_device_status(frm);
		subscribe_device_status(frm);
		add_device_refresh_button(frm);
		wire_device_farms(frm);
		mark_bio_grids(frm);
		frm.set_query("employee", "attendance_employee_filters", function(doc, cdt, cdn) {
			// Once an employee is picked in one row, drop them from every other
			// row's suggestion list -- there is no reason to exclude the same
			// employee twice, and it only clutters the picker.
			const selected = (doc.attendance_employee_filters || [])
				.filter(r => r.name !== cdn)
				.map(r => r.employee)
				.filter(Boolean);
			return { filters: { name: ["not in", selected] } };
		});
		const emp_filter_grid = frm.fields_dict.attendance_employee_filters &&
			frm.fields_dict.attendance_employee_filters.grid;
		if (emp_filter_grid) {
			emp_filter_grid.add_custom_button(__("Find & Add by Payroll Number"),
				() => open_bulk_add_by_payroll(frm), "top");
		}
	},

	devices_on_form_rendered: function(frm) {
		paint_device_status(frm);
		add_device_refresh_button(frm);
		wire_device_farms(frm);
		mark_bio_grids(frm);
	},

	devices_add: function(frm, cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		if (row && !row.status) row.status = "Offline";
		add_device_refresh_button(frm);
		wire_device_farms(frm);
		mark_bio_grids(frm);
		setTimeout(() => paint_device_status(frm), 0);
		setTimeout(() => paint_device_status(frm), 150);
		setTimeout(() => paint_device_farms(frm), 0);
		setTimeout(() => paint_device_farms(frm), 150);
	},

	users_device_picker: function(frm) {
		const match = _find_device_by_location(frm, frm.doc.users_device_picker);
		frm.set_value("users_device_sn", match ? (match.device_sn || "") : "");
		render_users_tab(frm);
	},

	biodata_device_picker: function(frm) {
		const match = _find_device_by_location(frm, frm.doc.biodata_device_picker);
		frm.set_value("biodata_device_sn", match ? (match.device_sn || "") : "");
		render_biodata_tab(frm);
	},

	enable_checkin:           autosave_on_change,
	enable_users:             autosave_on_change,
	enable_bio_templates:     autosave_on_change,
	enable_flip:              autosave_on_change,
	enable_absent:            autosave_on_change,
	checkin_event_frequency:  autosave_on_change,
	biodata_event_frequency:  autosave_on_change,
	flip_event_frequency:     autosave_on_change,
	absent_event_frequency:   autosave_on_change,
	checkin_cron_format:      autosave_on_change,
	biodata_cron_format:      autosave_on_change,
	flip_cron_format:         autosave_on_change,
	absent_cron_format:       autosave_on_change,

	detect_capabilities: function(frm) {
		// Dry run first, always: the report shows what would be cleared
		// before any stored credential is touched.
		run_with_progress(
			__("Detecting device capabilities"),
			__("Reading the templates each device has delivered..."),
			{
				method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.detect_device_capabilities",
				args: { apply: 0 },
				callback(r) {
					if (r.exc || !r.message) return;
					show_capability_report(frm, r.message);
				}
			}
		);
	},

	absent_preview: function(frm) {
		run_absent_marking(frm, 1);
	},

	absent_run_now: function(frm) {
		frappe.confirm(
			__("Mark Absent for every closed shift window with no check-in logs? Preview first if you have just changed the grace period."),
			() => run_absent_marking(frm, 0)
		);
	},

	update: function(frm) {
		const day = frm.doc.date || frappe.datetime.get_today();
		const run_flip = () => {
			run_with_progress(
				__("Updating check-ins"),
				__("Normalizing check-in directions for {0} by assigned shift...", [day]),
				{
					method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.flip_checkins_for_date",
					args: { date: day },
					callback: function(r) {
						if (!r.exc && r.message) {
							const m = r.message;
							frappe.show_alert({
								message: __("Updated {0}: {1} IN→OUT, {2} OUT→IN across {3} shift window(s).",
									[day, m.flipped_to_out || 0, m.flipped_to_in || 0, m.candidates || 0]),
								indicator: (m.flipped ? "green" : "blue")
							}, 10);
						}
					}
				}
			);
		};

		if (frm.is_dirty()) {
			frm.save().then(run_flip);
		} else {
			run_flip();
		}
	},

	get_checkin: function(frm) {
		if (!frm.doc.enable_checkin) {
			frappe.msgprint(__("Enable Checkin"));
			return;
		}
		if (!frm.doc.start_date || !frm.doc.end_date) {
			frappe.msgprint("Set both Start Date and End Date.");
			return;
		}
		if (frm.doc.start_date > frm.doc.end_date) {
			frappe.msgprint("Start Date cannot be after End Date.");
			return;
		}
		if (!frm.doc.poll_devices || !frm.doc.poll_devices.length) {
			frappe.msgprint("Add at least one device to poll.");
			return;
		}

		const run_poll = () => {
			const total = (frm.doc.poll_devices || []).length;
			run_with_progress(
				__("Polling devices"),
				__("Sending poll commands to {0} device(s)...", [total]),
				{
					method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.poll_devices",
					callback: function(r) {
						if (!r.exc && r.message) {
							const m = r.message;
							frappe.show_alert({
								message: __("Poll queued for {0} device(s){1} ({2} → {3}).",
									[m.queued, m.failed ? `, ${m.failed} failed` : "",
									 frm.doc.start_date, frm.doc.end_date]),
								indicator: m.failed ? "orange" : "blue"
							}, 10);
							frm.reload_doc();
						}
					}
				}
			);
		};

		if (frm.is_dirty()) {
			frm.save().then(run_poll);
		} else {
			run_poll();
		}
	},

	get_bio: function(frm) {
		if (!frm.doc.enable_bio_templates) {
			frappe.msgprint(__("Enable Bio Templates"));
			return;
		}
		const match = _find_device_by_location(frm, frm.doc.biodata_device_picker);
		if (!match) {
			frappe.msgprint("Pick a device above first.");
			return;
		}
		const sn = match.device_sn;

		const open_dialog = () => {
			open_bulk_user_dialog(
				"Poll BioData",
				sn,
				match.device_location || sn,
				() => render_biodata_tab(frm),
				get_enabled_filters(frm)
			);
		};

		if (frm.is_dirty()) {
			frm.save().then(open_dialog);
		} else {
			open_dialog();
		}
	}
});

// Grid wrappers the dark-theme text rules apply to. Re-applied whenever Frappe
// rebuilds the grid DOM.
function mark_bio_grids(frm) {
	["devices", "poll_devices"].forEach(fieldname => {
		const field = frm.fields_dict && frm.fields_dict[fieldname];
		if (field && field.$wrapper) field.$wrapper.addClass("upande-bio-grid");
	});
}

const CAPABILITY_LABELS = {
	supports_fingerprint: __("Fingerprint"),
	supports_face:        __("Face"),
	supports_palm:        __("Palm"),
	supports_card:        __("Card"),
	supports_password:    __("Password"),
};

function show_capability_report(frm, res) {
	const esc = frappe.utils.escape_html;
	const flags = Object.keys(CAPABILITY_LABELS);
	const report = res.report || [];
	const to_clear = res.clear || 0;

	// Ticked: rows carrying it. Not ticked: nothing, or what Apply clears.
	const rows = report.map(d => {
		const ev = d.evidence || {};
		const cells = flags.map(flag => {
			const n = ev[flag] || 0;
			if ((d.flags || {})[flag]) return `<td class="cap-num${n ? "" : " zero"}">${n}</td>`;
			const clear = (d.clear || {})[flag] || 0;
			return clear
				? `<td class="cap-num cap-off">${clear}<span class="cap-pill off">${__("clear")}</span></td>`
				: `<td class="cap-num cap-off" title="${__("Not supported")}">—</td>`;
		}).join("");
		return `<tr>
			<td class="cap-dev" title="${esc(d.device_sn)}">${esc(d.device_location || d.device_sn)}</td>
			<td class="cap-num">${d.template_rows || 0}</td>
			${cells}
		</tr>`;
	}).join("");

	const head = Object.values(CAPABILITY_LABELS)
		.map(l => `<th class="cap-num">${l}</th>`).join("");

	const summary = __("{0} unsupported credential(s) to clear", [to_clear]);

	const d = new frappe.ui.Dialog({
		title: __("Device Capabilities"),
		size: "extra-large",
		fields: [{
			fieldtype: "HTML",
			options: `
				<style>
					.cap-report .cap-summary { color:var(--text-muted); font-size:var(--text-sm,13px); margin-bottom:12px; }
					.cap-report .cap-wrap { max-height:60vh; overflow:auto; border:1px solid var(--border-color); border-radius:8px; }
					.cap-report table { width:100%; margin:0; border-collapse:separate; border-spacing:0; font-size:var(--text-sm,13px); }
					.cap-report th, .cap-report td { padding:10px 16px; white-space:nowrap; vertical-align:middle;
						border-bottom:1px solid var(--border-color); }
					.cap-report thead th { background:var(--subtle-fg, var(--bg-light-gray)); color:var(--text-muted);
						font-weight:500; position:sticky; top:0; z-index:1; }
					.cap-report tbody tr:last-child td { border-bottom:0; }
					.cap-report .cap-num { text-align:right; font-variant-numeric:tabular-nums; width:1%; }
					.cap-report .cap-num.zero { color:var(--text-light, var(--text-muted)); }
					.cap-report td.cap-dev { color:var(--text-color); font-weight:500; }
					.cap-report td.cap-off { background:var(--subtle-fg, var(--bg-light-gray)); color:var(--text-light, var(--text-muted)); }
					.cap-report .cap-pill { display:inline-block; margin-left:8px; padding:0 8px; line-height:18px;
						border-radius:9px; font-size:var(--text-xs,11px); font-weight:500; vertical-align:middle; }
					.cap-report .cap-pill.off { background:var(--bg-red, #fff0f0); color:var(--red-600, #e03636); }
				</style>
				<div class="cap-report">
					<div class="cap-summary">${summary}</div>
					<div class="cap-wrap">
						<table>
							<thead><tr>
								<th>${__("Device")}</th>
								<th class="cap-num">${__("Rows")}</th>
								${head}
							</tr></thead>
							<tbody>${rows}</tbody>
						</table>
					</div>
				</div>`
		}],
		primary_action_label: to_clear ? __("Clear {0}", [to_clear]) : __("Close"),
		primary_action() {
			if (!to_clear) { d.hide(); return; }
			frappe.confirm(__("Clear {0} unsupported credential(s) from the stored templates?", [to_clear]), () => {
				d.hide();
				run_with_progress(
					__("Clearing unsupported credentials"),
					__("Clearing templates..."),
					{
						method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.detect_device_capabilities",
						args: { apply: 1 },
						callback(r2) {
							if (r2.exc) return;
							frm.reload_doc();
							frappe.show_alert({
								message: __("{0} credential(s) cleared.", [(r2.message || {}).clear || 0]),
								indicator: "green"
							}, 7);
						}
					}
				);
			});
		}
	});
	d.show();
}

function run_with_progress(title, message, call_args) {
	let pct = 5;
	frappe.show_progress(title, pct, 100, message, true);
	const tick = setInterval(() => {
		if (pct < 90) {
			pct += 5;
			frappe.show_progress(title, pct, 100, message, true);
		}
	}, 400);

	const stop = () => {
		clearInterval(tick);
		frappe.show_progress(title, 100, 100, __("Done"), true);
	};
	const fail = () => {
		clearInterval(tick);
		frappe.hide_progress();
	};

	const user_callback = call_args.callback;
	const user_error    = call_args.error;

	return frappe.call(Object.assign({}, call_args, {
		callback: (r) => {
			stop();
			if (user_callback) user_callback(r);
		},
		error: (r) => {
			fail();
			if (user_error) user_error(r);
		}
	}));
}
window.upande_ta_run_with_progress = run_with_progress;

function make_primary(frm, fieldname) {
	const $btn = frm.fields_dict[fieldname] && frm.fields_dict[fieldname].$wrapper.find("button");
	if (!$btn || !$btn.length) return;
	$btn.removeClass("btn-default btn-secondary btn-success btn-danger").addClass("btn-primary");
}

function guard_devices_delete(frm) {
	const try_install = () => {
		const grid = frm.fields_dict.devices && frm.fields_dict.devices.grid;
		if (!grid) return false;
		if (grid._delete_guard_installed) return true;

		const original_delete_rows = grid.delete_rows.bind(grid);
		const original_delete_all_rows = grid.delete_all_rows.bind(grid);

		const collect_sns = (docs) => (docs || [])
			.map(d => d && d.device_sn)
			.filter(Boolean);

		const check_then_run = (sns, on_ok) => {
			if (!sns.length) {
				on_ok();
				return;
			}
			frappe.call({
				method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.devices_with_templates",
				args: { device_sns: JSON.stringify(sns) },
				callback: (r) => {
					const blocked = (r && r.message) || {};
					const blocked_sns = Object.keys(blocked);
					if (blocked_sns.length) {
						const link_list = (names, base) => names.map(name => {
							const safe = frappe.utils.escape_html(name);
							const href = `${base}/${encodeURIComponent(name)}`;
							return `<a href="${href}" target="_blank">${safe}</a>`;
						}).join(", ");
						const lines = blocked_sns.map(sn => {
							const info = blocked[sn] || {};
							const templates = info.templates || [];
							const users = info.users || [];
							const parts = [];
							if (templates.length) {
								parts.push(`${templates.length} template(s): ${link_list(templates, "/app/biometric-template")}`);
							}
							if (users.length) {
								parts.push(`${users.length} user record(s): ${link_list(users, "/app/biometric-user")}`);
							}
							return `<li><b>${frappe.utils.escape_html(sn)}</b> → ${parts.join("; ")}</li>`;
						}).join("");
						frappe.msgprint({
							title: __("Cannot delete device(s)"),
							indicator: "red",
							message: __("The following device(s) have Biometric User or Biometric Template records. Delete the linked rows first:") +
								`<ul>${lines}</ul>`
						});
						return;
					}
					on_ok();
				}
			});
		};

		grid.delete_rows = function() {
			const selected = grid.get_selected_children() || [];
			check_then_run(collect_sns(selected), () => original_delete_rows());
		};

		grid.delete_all_rows = function() {
			const all_docs = (frm.doc.devices || []);
			check_then_run(collect_sns(all_docs), () => original_delete_all_rows());
		};

		grid._delete_guard_installed = true;
		console.log("[upande_ta] devices delete guard installed");
		return true;
	};

	if (try_install()) return;
	setTimeout(try_install, 50);
	setTimeout(try_install, 250);
	setTimeout(try_install, 1000);
}

function subscribe_device_status(frm) {
	if (frm._device_status_subscribed) return;
	frm._device_status_subscribed = true;

	frappe.realtime.on("biometric_device_status", (payload) => {
		const changes = []
			.concat(payload && payload.updated ? payload.updated : [])
			.concat(payload && payload.offline ? payload.offline : []);
		if (!changes.length) return;

		const grid_rows_by_sn = {};
		(frm.doc.devices || []).forEach(d => {
			if (d.device_sn) grid_rows_by_sn[d.device_sn] = d;
		});

		let touched = false;
		changes.forEach(c => {
			const row = grid_rows_by_sn[c.device_sn];
			if (!row) return;
			if (c.status)    row.status    = c.status;
			if (c.last_seen) row.last_seen = c.last_seen;
			touched = true;
		});

		if (!touched) return;
		const grid = frm.fields_dict.devices && frm.fields_dict.devices.grid;
		if (grid) grid.refresh();
		paint_device_status(frm);
	});
}

function paint_device_status(frm) {
	const grid = frm.fields_dict.devices && frm.fields_dict.devices.grid;
	if (!grid || !grid.wrapper) return;

	if (!grid._status_focus_handler) {
		$(grid.wrapper).on(
			"focusin click",
			'[data-fieldname="status"]',
			() => setTimeout(() => paint_device_status(frm), 0)
		);
		grid._status_focus_handler = true;
	}

	if (!grid._status_refresh_hook) {
		const original_refresh = grid.refresh.bind(grid);
		grid.refresh = function() {
			const result = original_refresh.apply(this, arguments);
			setTimeout(() => paint_device_status(frm), 0);
			return result;
		};
		grid._status_refresh_hook = true;
	}

	const tag = (el, child) => {
		const status = (child && child.status) || "Offline";
		const flag = status === "Online" ? "online" : "offline";
		el.setAttribute("data-bio-status", flag);
	};

	const paint_row = (row_name, child) => {
		if (!child) return;
		if (!child.status) child.status = "Offline";
		const status = child.status || "Offline";
		const flag = status === "Online" ? "online" : "offline";
		const color = status === "Online" ? "#198754" : "#dc3545";
		const label = frappe.utils.escape_html(status);
		const pill_html = `<span class="bio-status-pill" style="color:${color};font-weight:600;display:inline-flex;align-items:center;gap:6px"><span>●</span>${label}</span>`;

		const $row = $(grid.wrapper).find(`.grid-row[data-name="${row_name}"]`);
		$row.find('[data-fieldname="status"]').attr("data-bio-status", flag);
		$row.find('[data-fieldname="status"] select.form-control').attr("data-bio-status", flag);

		const $static = $row.find('[data-fieldname="status"] .static-area');
		if ($static.length) {
			$static.html(pill_html);
			$static.css("display", "block");
		}
		const $field = $row.find('[data-fieldname="status"] .field-area');
		if ($field.length) $field.css("display", "none");
	};

	const by_name = {};
	(frm.doc.devices || []).forEach(d => {
		if (d && d.name) by_name[d.name] = d;
	});

	$(grid.wrapper).find(".grid-row").each(function () {
		const $r = $(this);
		const row_name = $r.attr("data-name");
		if (!row_name) return;
		const child = by_name[row_name]
			|| (locals["Biometric Device"] && locals["Biometric Device"][row_name]);
		paint_row(row_name, child || { status: "Offline" });
	});
}

function add_device_refresh_button(frm) {
	const grid = frm.fields_dict.devices && frm.fields_dict.devices.grid;
	if (!grid || !grid.wrapper) return;
	const $wrapper = $(grid.wrapper);
	if ($wrapper.find(".bio-device-refresh-btn").length) return;

	const $anchor = $wrapper.find(".grid-custom-buttons").first().length
		? $wrapper.find(".grid-custom-buttons").first().parent()
		: $wrapper;
	if (getComputedStyle($anchor[0]).position === "static") {
		$anchor.css("position", "relative");
	}

	const $btn = $(`
		<button type="button"
			class="btn btn-xs btn-primary bio-device-refresh-btn"
			title="${frappe.utils.escape_html(__("Refresh device status and last seen"))}"
			style="position:absolute;top:6px;right:8px;z-index:5;display:inline-flex;align-items:center;gap:4px">
			<span>↻</span>${frappe.utils.escape_html(__("Refresh"))}
		</button>
	`);
	$anchor.append($btn);

	$btn.on("click", () => refresh_device_statuses(frm, $btn));
}

function refresh_device_statuses(frm, $btn) {
	if ($btn) $btn.prop("disabled", true);
	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.get_device_statuses",
		callback: (r) => {
			const rows = (r && r.message) || [];
			const by_sn = {};
			rows.forEach(d => { if (d.device_sn) by_sn[d.device_sn] = d; });

			let touched = false;
			(frm.doc.devices || []).forEach(child => {
				if (!child.device_sn) return;
				const fresh = by_sn[child.device_sn];
				if (!fresh) return;
				if (child.status !== fresh.status) {
					child.status = fresh.status;
					touched = true;
				}
				if (fresh.last_seen && fresh.last_seen !== child.last_seen) {
					child.last_seen = fresh.last_seen;
					touched = true;
				}
			});

			const grid = frm.fields_dict.devices && frm.fields_dict.devices.grid;
			if (touched && grid) {
				grid.grid_rows && grid.grid_rows.forEach(gr => gr && gr.refresh && gr.refresh());
				grid.refresh();
			}
			paint_device_status(frm);

			frappe.show_alert({
				message: __("Device status refreshed"),
				indicator: "blue"
			}, 3);
		},
		always: () => {
			if ($btn) $btn.prop("disabled", false);
		}
	});
}

// Wildcard search over Employee (name and Employee Number both carry the
// payroll number on this fleet, e.g. "H 007") + a checklist of the matches,
// so excluding a whole batch (e.g. every "H0*" payroll number) does not mean
// adding them to the Employee Filters grid one row at a time.
function _payroll_like_pattern(pattern) {
	let txt = (pattern || "").trim();
	if (!txt) return "%";
	let sql = txt.replace(/\*/g, "%");
	if (!sql.includes("%")) sql = "%" + sql + "%";
	return sql;
}

function open_bulk_add_by_payroll(frm) {
	const already = new Set(
		(frm.doc.attendance_employee_filters || []).map(r => r.employee).filter(Boolean)
	);

	function fetch_matches(pattern) {
		const like = _payroll_like_pattern(pattern);
		return frappe.db.get_list("Employee", {
			filters: { status: "Active" },
			or_filters: [["name", "like", like], ["employee_number", "like", like]],
			fields: ["name", "employee_name"],
			order_by: "name asc",
			limit: 500
		}).then(rows => rows
			.filter(r => !already.has(r.name))
			.map(r => ({ label: `${r.name} — ${r.employee_name || ""}`, value: r.name, checked: false }))
		);
	}

	// `dlg` is declared (not const-assigned) before the Dialog is built because
	// MultiCheck's get_data runs synchronously while the fields are being set
	// up -- i.e. before `new frappe.ui.Dialog(...)` has finished and returned,
	// so a `const dlg = new frappe.ui.Dialog(...)` closed over by get_data
	// would hit the temporal-dead-zone and throw, killing the dialog silently
	// (see open_device_farms_dialog above for the same pattern).
	let dlg;
	dlg = new frappe.ui.Dialog({
		title: __("Find & Add Employees by Payroll Number"),
		size: "large",
		fields: [
			{
				fieldname: "pattern",
				fieldtype: "Data",
				label: __("Payroll Number"),
				description: __("Use * as a wildcard, e.g. H0* or *035. Leave blank to list everyone."),
				change: () => { if (dlg) dlg.fields_dict.matches.refresh(); }
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "matches",
				fieldtype: "MultiCheck",
				label: __("Matching Employees (already-added ones are left out)"),
				columns: 2,
				select_all: true,
				get_data: () => fetch_matches(dlg ? dlg.get_value("pattern") : "")
			}
		],
		primary_action_label: __("Add Selected"),
		primary_action: () => {
			const picked = (dlg.get_value("matches") || []).filter(emp => !already.has(emp));
			dlg.hide();
			if (!picked.length) return;
			picked.forEach(emp => {
				const row = frm.add_child("attendance_employee_filters");
				row.employee = emp;
				row.excluded = 1;
			});
			frm.refresh_field("attendance_employee_filters");
			frappe.show_alert({ message: __("Added {0} employee(s)", [picked.length]), indicator: "green" });
		}
	});

	dlg.show();
}

function _split_farms(value) {
	if (!value) return [];
	if (Array.isArray(value)) return value.filter(Boolean);
	return String(value).split(",").map(s => s.trim()).filter(Boolean);
}

// The Devices grid `farms` cell is read-only; clicking it opens a multi-select
// pills dialog. We store the picked Farm docnames as a comma-separated string.
function open_device_farms_dialog(frm, row, on_close) {
	let dlg;
	dlg = new frappe.ui.Dialog({
		title: __("Farms for {0}", [row.device_location || row.device_sn || __("device")]),
		fields: [
			{
				fieldtype: "MultiSelectPills",
				fieldname: "farms",
				label: __("Farms"),
				get_data(txt) {
					// Drop already-selected farms from the suggestion list.
					// `dlg` is undefined while the dialog is still constructing
					// (get_data runs during field render) — guard against it.
					const selected = dlg ? _split_farms(dlg.get_value("farms")) : [];
					return frappe.db.get_link_options("Farm", txt).then(opts =>
						(opts || []).filter(o => {
							const v = (o && o.value !== undefined) ? o.value : o;
							return !selected.includes(v);
						})
					// The Farm doctype may not be installed on this site (it ships
					// with upande_kaitet). Swallow the rejection so the dialog opens
					// cleanly instead of throwing "DocType Farm does not exist" and
					// bouncing back to the workspace.
					).catch(() => []);
				}
			}
		],
		primary_action_label: __("Save"),
		primary_action(values) {
			// MultiSelectPills returns an array; store as comma-separated string.
			const val = _split_farms(values.farms).join(",");
			frappe.model.set_value(row.doctype, row.name, "farms", val);
			paint_device_farms(frm);
			dlg.hide();
		}
	});
	if (on_close) dlg.$wrapper.one("hidden.bs.modal", () => on_close());
	dlg.show();
	// Prefill AFTER show and with an ARRAY — the pills control does value.map(),
	// so a string here throws and the dialog would never open.
	dlg.set_value("farms", _split_farms(row.farms));
}

// Render the read-only `farms` cell as ", "-joined farm names (or a hint when
// empty) and mark it clickable.
function paint_device_farms(frm) {
	const grid = frm.fields_dict.devices && frm.fields_dict.devices.grid;
	if (!grid || !grid.wrapper) return;

	const by_name = {};
	(frm.doc.devices || []).forEach(d => { if (d && d.name) by_name[d.name] = d; });

	$(grid.wrapper).find(".grid-row").each(function () {
		const row_name = $(this).attr("data-name");
		if (!row_name) return;
		const child = by_name[row_name]
			|| (locals["Biometric Device"] && locals["Biometric Device"][row_name]);
		const farms = _split_farms(child && child.farms);
		const $cell = $(this).find('[data-fieldname="farms"]');
		$cell.css("cursor", "pointer");
		const $static = $cell.find(".static-area");
		if ($static.length) {
			if (farms.length) {
				$static.text(farms.join(", "));
			} else {
				$static.html(
					`<span class="text-muted">${frappe.utils.escape_html(__("Click to set farms"))}</span>`
				);
			}
			$static.css("display", "block");
		}
	});
}

function wire_device_farms(frm) {
	const grid = frm.fields_dict.devices && frm.fields_dict.devices.grid;
	if (!grid || !grid.wrapper) return;

	// `farms` is a read-only field, so when a device row is in edit mode Frappe
	// renders it as a DISABLED <input>. Disabled inputs swallow mouse events —
	// they don't fire and don't bubble — so the delegated mousedown handler below
	// would never see clicks on it and the picker would never open. Make the
	// cell's inner input/areas transparent to pointer events so clicks fall
	// through to the `.grid-static-col` wrapper (which is NOT disabled and DOES
	// receive the event). Scoped to this grid so the dialog's own `farms` picker
	// field is unaffected.
	$(grid.wrapper).addClass("bio-farms-grid");
	if (!document.getElementById("bio-farms-style")) {
		const style = document.createElement("style");
		style.id = "bio-farms-style";
		style.textContent =
			'.bio-farms-grid [data-fieldname="farms"] .field-area,' +
			'.bio-farms-grid [data-fieldname="farms"] .static-area{pointer-events:none;}' +
			'.bio-farms-grid [data-fieldname="farms"]{cursor:pointer;}';
		document.head.appendChild(style);
	}

	if (!grid._farms_click_handler) {
		// Open the picker from the Farms cell on mousedown. preventDefault stops
		// the cell entering the grid's inline-edit/focus state (which on a
		// not-saved doc can trigger revalidation/route side-effects); the modal
		// is the only way to edit farms. Clicks land on the cell wrapper for both
		// saved rows (static span) and rows in edit mode (disabled input made
		// click-through above).
		$(grid.wrapper).on("mousedown", '[data-fieldname="farms"]', function (e) {
			const $cell = $(e.target).closest('[data-fieldname="farms"]');
			if (!$cell.length) return;
			e.preventDefault();
			e.stopPropagation();
			if (grid._farms_dialog_open) return;
			const row_name = $cell.closest(".grid-row").attr("data-name");
			if (!row_name) return;
			const row = (locals["Biometric Device"] && locals["Biometric Device"][row_name])
				|| (frm.doc.devices || []).find(d => d.name === row_name);
			if (!row) return;
			grid._farms_dialog_open = true;
			open_device_farms_dialog(frm, row, () => {
				setTimeout(() => { grid._farms_dialog_open = false; }, 200);
			});
		});
		// Swallow the trailing click so it can't re-enter the cell edit state.
		$(grid.wrapper).on("click", '[data-fieldname="farms"]', function (e) {
			e.preventDefault();
			e.stopPropagation();
		});
		grid._farms_click_handler = true;
	}

	if (!grid._farms_refresh_hook) {
		const original_refresh = grid.refresh.bind(grid);
		grid.refresh = function () {
			const result = original_refresh.apply(this, arguments);
			setTimeout(() => paint_device_farms(frm), 0);
			return result;
		};
		grid._farms_refresh_hook = true;
	}

	paint_device_farms(frm);
}

function refresh_device_options(frm) {
	const locations = (frm.doc.devices || [])
		.map(d => d.device_location || d.device_sn)
		.filter(loc => loc);
	const locations_opts = "\n" + locations.join("\n");

	frm.set_df_property("users_device_picker", "options", locations_opts);
	frm.set_df_property("biodata_device_picker", "options", locations_opts);

	const grid = frm.fields_dict.poll_devices && frm.fields_dict.poll_devices.grid;
	if (grid) {
		grid.update_docfield_property("device", "options", locations_opts);

		const set_df_options = (df) => {
			if (!df || df.fieldname !== "device") return;
			df.options = locations_opts;
		};
		(grid.docfields || []).forEach(set_df_options);
		(grid.meta && grid.meta.fields || []).forEach(set_df_options);
		if (grid.grid_rows) {
			grid.grid_rows.forEach(gr => {
				if (gr && gr.docfields) gr.docfields.forEach(set_df_options);
				const col = gr && gr.columns && gr.columns.device;
				if (col && col.df) col.df.options = locations_opts;
			});
		}

		grid.refresh();
	}
}

function _find_device_by_location(frm, value) {
	if (!value) return null;
	const devices = frm.doc.devices || [];
	return devices.find(d => (d.device_location || d.device_sn) === value)
		|| devices.find(d => d.device_sn === value)
		|| null;
}

function render_users_tab(frm) {
	const wrapper = frm.fields_dict.users_html && frm.fields_dict.users_html.$wrapper;
	const actions_wrapper = frm.fields_dict.users_actions_html && frm.fields_dict.users_actions_html.$wrapper;
	if (!wrapper) return;

	const device_match = _find_device_by_location(frm, frm.doc.users_device_picker);

	const buttons_html = `
		<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
			<button class="btn btn-sm btn-primary" id="btn-bulk-add">Add</button>
			<button class="btn btn-sm btn-primary" id="btn-bulk-update">Update</button>
			<button class="btn btn-sm btn-primary" id="btn-bulk-delete">Delete</button>
			<button class="btn btn-sm btn-primary" id="btn-hydrate-templates">Sync</button>
		</div>
	`;

	const has_actions_slot = actions_wrapper && actions_wrapper.length;
	if (has_actions_slot) {
		actions_wrapper.html(buttons_html);
	}

	if (!device_match) {
		wrapper.html(
			(has_actions_slot ? "" : buttons_html) +
			`<div style="padding:20px;color:var(--text-muted)">
				Pick a device above to view and manage its users.
			</div>`
		);
		const btn_scope_nm = has_actions_slot ? actions_wrapper : wrapper;
		const remind = () => frappe.msgprint("Pick a device above first.");
		btn_scope_nm.find("#btn-bulk-add").off("click").on("click", remind);
		btn_scope_nm.find("#btn-bulk-update").off("click").on("click", remind);
		btn_scope_nm.find("#btn-bulk-delete").off("click").on("click", remind);
		btn_scope_nm.find("#btn-hydrate-templates").off("click").on("click", remind);
		return;
	}
	const sn = device_match.device_sn;
	const loc = device_match.device_location || sn;

	wrapper.html(
		(has_actions_slot ? "" : buttons_html) +
		`<div id="users-table-container" style="margin-top:${has_actions_slot ? 0 : 12}px">
			<p style="color:var(--text-muted)">Loading users on ${frappe.utils.escape_html(loc)}...</p>
		</div>`
	);

	const open_bulk = (cmd) => {
		if (!frm.doc.enable_users) {
			frappe.msgprint(__("Enable Users"));
			return;
		}
		open_bulk_user_dialog(cmd, sn, loc, () => render_users_tab(frm), get_enabled_filters(frm));
	};

	const btn_scope = has_actions_slot ? actions_wrapper : wrapper;
	btn_scope.find("#btn-bulk-add").off("click").on("click", () => open_bulk("Add User"));
	btn_scope.find("#btn-bulk-update").off("click").on("click", () => open_bulk("Update User"));
	btn_scope.find("#btn-bulk-delete").off("click").on("click", () => open_bulk("Delete User"));
	btn_scope.find("#btn-hydrate-templates").off("click").on("click", () => {
		open_template_sync_dialog(frm, sn, loc);
	});

	const call = (method) => new Promise(resolve => frappe.call({
		method: `upande_ta.upande_ta.doctype.biometric_user.biometric_user.${method}`,
		args: { device_sn: sn },
		callback: (r) => resolve(r.message),
		error: () => resolve(null),
	}));
	Promise.all([call("get_device_users"), call("get_user_sync_status")]).then(([users, sync]) => {
		render_user_list(wrapper, sn, users || [], frm, sync);
	});
}

// Sync B from A: pick the device whose templates B should receive. Picking B
// itself keeps the original roster sync from B's own templates.
function open_template_sync_dialog(frm, sn, loc) {
	const esc = frappe.utils.escape_html;
	const devices = (frm.doc.devices || []).filter(dev => dev.device_sn);
	const options = [{ device_sn: sn, device_location: loc }]
		.concat(devices.filter(dev => dev.device_sn !== sn))
		.map(dev => `<option value="${esc(dev.device_sn)}" data-sub="${esc(dev.device_sn)}">${esc(dev.device_location || dev.device_sn)}</option>`)
		.join("");

	const d = new frappe.ui.Dialog({
		title: __("Sync {0}", [loc]),
		fields: [{
			fieldname: "source_html",
			fieldtype: "HTML",
			options: `<label class="uds-field" style="margin:0">
				<span>${__("Templates from")}</span>
				<select id="sync-source-sel">${options}</select>
			</label>`,
		}],
		primary_action_label: __("Sync"),
		primary_action() {
			const source_sn = d.$wrapper.find("#sync-source-sel").val() || sn;
			d.hide();
			run_template_sync(frm, sn, loc, source_sn);
		},
	});
	d.show();
	const sel = d.$wrapper.find("#sync-source-sel")[0];
	if (sel && upande_ta.device_select && upande_ta.device_select.enhance) upande_ta.device_select.enhance(sel);
}

function run_template_sync(frm, sn, loc, source_sn) {
	const done = (m) => {
		let msg = __("{0}: {1} added, {2} updated, {3} command(s) queued", [loc, m.added || 0, m.updated || 0, m.queued || 0]);
		if (m.failed) msg += __(", {0} failed", [m.failed]);
		frappe.show_alert({ message: msg, indicator: m.failed ? "orange" : "green" }, 8);
		render_users_tab(frm);
	};

	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.sync_templates_from_device",
		args: { device_sn: sn, source_device_sn: source_sn },
		freeze: true,
		freeze_message: __("Syncing {0}...", [loc]),
		callback: (r) => {
			if (r.exc || !r.message) return;
			const m = r.message;
			if (source_sn === sn) {
				if (m.reason) {
					frappe.show_alert({ message: __("No templates on {0}", [loc]), indicator: "orange" }, 5);
					return;
				}
				frappe.show_alert({
					message: __("Synced {0} user(s); skipped {1}.", [m.created, m.skipped]),
					indicator: m.created ? "green" : "blue"
				}, 5);
				render_users_tab(frm);
				return;
			}
			if (m.queued_job) {
				frappe.show_alert({
					message: __("Syncing {0} user(s) to {1} in the background", [(m.add || 0) + (m.update || 0), loc]),
					indicator: "blue"
				}, 6);
				frappe.realtime.off("upande_ta_template_sync_done");
				frappe.realtime.on("upande_ta_template_sync_done", (res) => {
					frappe.realtime.off("upande_ta_template_sync_done");
					done(res || {});
				});
				return;
			}
			done(m);
		}
	});
}

function render_user_list(wrapper, device_sn, users, frm, sync) {
	const container = wrapper.find("#users-table-container");

	if (!users.length) {
		container.html(`<p style="color:var(--text-muted);padding:8px 0">
			No users enrolled on this device yet. Use Bulk Add to enroll employees.
		</p>`);
		return;
	}

	const esc = frappe.utils.escape_html;
	const SYNC_ON = 1;
	const CREDS = [[2, "FP"], [4, "Face"], [8, "Palm"], [16, "Card"], [32, "Password"]];
	sync = sync || { devices: [], status: {}, eligible: {}, farm: {} };
	sync.farm = sync.farm || {};
	const devs = sync.devices || [];
	const here = devs.findIndex(dev => dev.device_sn === device_sn);
	// Enrolled here although the employee's farm is not one of this device's.
	const is_anomaly = (pin) => here >= 0 && !(sync.eligible[pin] || []).includes(here);

	// A device counts as holding the user when its roster lists the PIN or it
	// has delivered any credential for them.
	const held = (bits) => !!bits;
	// Every credential, same order on every device: held / not enrolled / not supported.
	const chips = (bits, caps) => CREDS
		.map(([bit, label]) => {
			const state = bits & bit ? "on" : (caps & bit ? "off" : "na");
			const title = { on: __("Active"), off: __("Not enrolled"), na: __("Not supported on this device") }[state];
			return `<span class="us-chip ${state}" title="${title}">${label}</span>`;
		})
		.join("");
	const coverage = (pin) => {
		const st = sync.status[pin] || [];
		const eligible = sync.eligible[pin] || [];
		const on = eligible.filter(i => held(st[i])).length;
		return { on, total: eligible.length, st, eligible };
	};

	function detail_html(pin) {
		const { st, eligible } = coverage(pin);
		const shown = devs.map((dev, i) => i).filter(i => eligible.includes(i));
		return `<div class="us-detail">${shown.map(i => {
			const dev = devs[i];
			const on = held(st[i]);
			return `<div class="us-dev${on ? "" : " missing"}${i === here ? " here" : ""}">
				<div class="us-dev-head">
					<div class="us-dev-name" title="${esc(dev.device_sn)}">${esc(dev.device_location)}</div>
					${on ? `<button type="button" class="us-dev-del" data-pin="${esc(pin)}" data-i="${i}" title="${__("Delete from {0}", [esc(dev.device_location)])}">${trash}</button>` : ""}
				</div>
				<div class="us-dev-chips">${on ? chips(st[i], dev.caps) : `<span class="us-none">${__("Not synced")}</span>`}</div>
			</div>`;
		}).join("")}</div>`;
	}

	const trash = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg>`;
	const regular = users.map((u, n) => n).filter(n => !is_anomaly(users[n].user_id || ""));
	const anomalies = users.map((u, n) => n).filter(n => is_anomaly(users[n].user_id || ""));

	const anomaly_rows = anomalies.map(n => {
		const u = users[n];
		const pin = u.user_id || "";
		const st = sync.status[pin] || [];
		return `
		<tr class="us-anom-row" data-n="${n}">
			<td style="font-family:var(--font-mono);font-size:13px">${esc(pin)}</td>
			<td>${esc(u.employee_name || "")}</td>
			<td>${sync.farm[pin] ? esc(sync.farm[pin]) : `<span class="us-none">${__("No farm")}</span>`}</td>
			<td><div class="us-dev-chips">${chips(st[here] || 0, devs[here].caps)}</div></td>
			<td style="text-align:right">
				<button type="button" class="us-dev-del" data-pin="${esc(pin)}" data-i="${here}" title="${__("Delete from {0}", [esc(devs[here].device_location)])}">${trash}</button>
			</td>
		</tr>`;
	}).join("");

	const rows = regular.map(n => {
		const u = users[n];
		const pin = u.user_id || "";
		const cov = coverage(pin);
		const full = cov.total && cov.on === cov.total;
		const this_dev = here >= 0 ? chips(cov.st[here] || 0, devs[here].caps) : "";
		return `
		<tr class="us-row" data-n="${n}" data-full="${full ? 1 : 0}">
			<td style="font-family:var(--font-mono);font-size:13px">${esc(pin)}</td>
			<td>${esc(u.employee_name || "")}</td>
			<td>${u.privilege === "14" ? "Admin" : "User"}</td>
			<td>
				<span style="font-size:11px;padding:2px 8px;border-radius:4px;
					background:var(--bg-light-gray);color:var(--text-color)">
					${esc(u.status || "")}
				</span>
			</td>
			<td><div class="us-dev-chips">${this_dev}</div></td>
			<td>
				<button type="button" class="us-cov${full ? " full" : ""}" data-n="${n}">
					${cov.on} / ${cov.total}
				</button>
			</td>
		</tr>`;
	}).join("");

	const missing_count = regular.map(n => users[n]).filter(u => {
		const c = coverage(u.user_id || "");
		return !(c.total && c.on === c.total);
	}).length;

	container.html(`
		<style>
			#users-table-container .us-bar { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
			#users-table-container .us-seg { display:inline-flex; background:var(--control-bg); border-radius:8px; padding:2px; }
			#users-table-container .us-seg button { border:0; background:transparent; height:24px; padding:0 10px;
				border-radius:6px; font-size:var(--text-sm,13px); color:var(--text-muted); cursor:pointer; }
			#users-table-container .us-seg button.active { background:var(--fg-color); color:var(--text-color);
				box-shadow:var(--shadow-sm, 0 1px 2px rgba(0,0,0,.08)); }
			#users-table-container .us-chip { display:inline-block; font-size:var(--text-xs,12px); line-height:18px;
				padding:0 7px; border-radius:9px; color:var(--text-muted);
				border:1px solid var(--border-color); white-space:nowrap; cursor:default; }
			#users-table-container .us-chip.na { opacity:.4; border-style:dashed; text-decoration:line-through; }
			#users-table-container .us-chip.on { border:1px solid transparent;
				background:var(--green-highlight-color, var(--bg-green, #e4f5e9)); color:var(--green-700, #16794c); }
			#users-table-container .us-dev-chips { display:flex; flex-wrap:wrap; gap:4px; }
			#users-table-container .us-cov { border:0; height:22px; padding:0 10px; border-radius:11px; cursor:pointer;
				font-size:var(--text-sm,13px); font-variant-numeric:tabular-nums; white-space:nowrap;
				background:var(--bg-orange, #fff1e7); color:var(--orange-700, #b4520e); }
			#users-table-container .us-cov.full { background:var(--green-highlight-color, var(--bg-green, #e4f5e9)); color:var(--green-700, #16794c); }
			#users-table-container .us-cov.open { box-shadow:0 0 0 2px var(--border-color); }
			#users-table-container tr.us-detail-row > td { background:var(--subtle-fg, var(--bg-light-gray)); padding:10px 12px; }
			#users-table-container .us-detail { display:grid; grid-template-columns:repeat(auto-fill, minmax(210px, 1fr)); gap:8px; }
			#users-table-container .us-dev { background:var(--fg-color); border:1px solid var(--border-color);
				border-radius:8px; padding:8px 10px; display:flex; flex-direction:column; gap:6px; }
			#users-table-container .us-dev.here { border-color:var(--text-muted); }
			#users-table-container .us-dev.missing .us-dev-name { color:var(--text-muted); }
			#users-table-container .us-dev-head { display:flex; align-items:center; justify-content:space-between; gap:8px; min-width:0; }
			#users-table-container .us-dev-del { flex:none; display:inline-flex; align-items:center; justify-content:center;
				width:24px; height:24px; border:0; border-radius:6px; background:transparent; color:var(--text-muted); cursor:pointer; }
			#users-table-container .us-dev-del:hover { background:var(--bg-red, #fff0f0); color:var(--red-600, #e03636); }
			#users-table-container .us-dev-del:disabled { opacity:.4; cursor:wait; }
			#users-table-container .us-dev-name { font-size:var(--text-sm,13px); font-weight:500; color:var(--text-color);
				white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
			#users-table-container .us-none { font-size:var(--text-xs,12px); color:var(--orange-700, #b4520e); }
			#users-table-container .us-seg button.us-anom-tab { color:var(--red-600, #e03636); }
		</style>
		<div class="us-bar">
			<div class="us-seg">
				<button type="button" data-f="all" class="active">${__("All")} ${regular.length}</button>
				<button type="button" data-f="missing">${__("Not on all devices")} ${missing_count}</button>
				<button type="button" data-f="full">${__("On all devices")} ${regular.length - missing_count}</button>
				${anomalies.length ? `<button type="button" data-f="anomalies" class="us-anom-tab">${__("Anomalies")} ${anomalies.length}</button>` : ""}
			</div>
		</div>
		<div class="us-anom" style="display:none;border:1px solid var(--border-color);border-radius:8px;overflow:hidden">
			<table class="table table-sm" style="margin:0">
				<thead style="background:var(--bg-light-gray)">
					<tr>
						<th style="width:110px">PIN</th>
						<th>Name</th>
						<th style="width:200px">${__("Employee farm")}</th>
						<th style="width:260px">${__("This device")}</th>
						<th style="width:60px"></th>
					</tr>
				</thead>
				<tbody>${anomaly_rows}</tbody>
			</table>
		</div>
		<div class="us-main" style="border:1px solid var(--border-color);border-radius:8px;overflow:hidden">
			<table class="table table-sm" style="margin:0">
				<thead style="background:var(--bg-light-gray)">
					<tr>
						<th style="width:110px">PIN</th>
						<th>Name</th>
						<th style="width:90px">Privilege</th>
						<th style="width:100px">Status</th>
						<th style="width:260px">${__("This device")}</th>
						<th style="width:100px">${__("Devices")}</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
	`);

	container.find(".us-seg").on("click", "button", function() {
		const f = $(this).data("f");
		frm._users_tab = f;
		$(this).addClass("active").siblings().removeClass("active");
		container.find("tr.us-detail-row").remove();
		container.find(".us-cov.open").removeClass("open");
		container.find(".us-anom").toggle(f === "anomalies");
		container.find(".us-main").toggle(f !== "anomalies");
		container.find("tr.us-row").each(function() {
			const full = $(this).attr("data-full") === "1";
			$(this).toggle(f === "all" || (f === "full" ? full : !full));
		});
	});

	const start_tab = !regular.length && anomalies.length ? "anomalies" : frm._users_tab;
	const $start = container.find(`.us-seg button[data-f="${start_tab}"]`);
	if ($start.length) $start.trigger("click");

	container.on("click", ".us-cov", function() {
		const $tr = $(this).closest("tr");
		const $next = $tr.next("tr.us-detail-row");
		if ($next.length) {
			$next.remove();
			$(this).removeClass("open");
			return;
		}
		container.find("tr.us-detail-row").remove();
		container.find(".us-cov.open").removeClass("open");
		const u = users[+$(this).data("n")];
		$(this).addClass("open");
		$tr.after(`<tr class="us-detail-row"><td colspan="6">${detail_html(u.user_id || "")}</td></tr>`);
	});

	container.on("click", ".us-dev-del", function(e) {
		e.stopPropagation();
		if (!frm.doc.enable_users) {
			frappe.msgprint(__("Enable Users"));
			return;
		}
		const $btn = $(this);
		const pin = String($btn.data("pin"));
		const dev = devs[+$btn.data("i")];
		const u = users.find(x => (x.user_id || "") === pin) || {};
		frappe.confirm(
			__("Delete {0} (PIN {1}) from {2}?", [esc(u.employee_name || pin), esc(pin), esc(dev.device_location)]),
			() => {
				$btn.prop("disabled", true);
				frappe.call({
					method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.bulk_command",
					args: {
						device_sn: dev.device_sn,
						users: [{ user_id: pin, employee_name: u.employee_name || "" }],
						command_type: "Delete User",
					},
					callback: (r) => {
						const m = r.message || {};
						if (r.exc || m.failed) {
							$btn.prop("disabled", false);
							const reason = ((m.errors || [])[0] || {}).reason;
							frappe.show_alert({ message: reason || __("Delete failed"), indicator: "red" }, 6);
							return;
						}
						frappe.show_alert({
							message: __("{0} deleted from {1}", [esc(u.employee_name || pin), esc(dev.device_location)]),
							indicator: "red",
						}, 5);
						if (dev.device_sn === device_sn) {
							render_users_tab(frm);
							return;
						}
						// another device: update this row in place
						(sync.status[pin] || [])[+$btn.data("i")] = 0;
						const cov = coverage(pin);
						const full = cov.total && cov.on === cov.total;
						const $row = container.find("tr.us-row").filter(function() {
							return users[+$(this).data("n")].user_id === pin;
						});
						$row.attr("data-full", full ? 1 : 0);
						$row.find(".us-cov").toggleClass("full", !!full).text(`${cov.on} / ${cov.total}`);
						$row.next("tr.us-detail-row").find("td").html(detail_html(pin));
					},
					error: () => $btn.prop("disabled", false),
				});
			}
		);
	});
}

function render_biodata_tab(frm) {
	const wrapper = frm.fields_dict.biometric_templates && frm.fields_dict.biometric_templates.$wrapper;
	if (!wrapper) return;

	const device_match = _find_device_by_location(frm, frm.doc.biodata_device_picker);
	if (!device_match) {
		wrapper.html(`<div style="padding:20px;color:var(--text-muted)">
			Pick a device above to view its biometric templates.
		</div>`);
		return;
	}
	const sn = device_match.device_sn;
	const loc = device_match.device_location || sn;

	wrapper.html(`
		<div id="templates-table-container">
			<p style="color:var(--text-muted)">Loading templates on ${frappe.utils.escape_html(loc)}...</p>
		</div>
	`);

	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.get_device_templates",
		args: { device_sn: sn },
		callback: (r) => render_template_list(wrapper, sn, r.message || [], frm)
	});
}

function render_template_list(wrapper, device_sn, templates, frm) {
	const container = wrapper.find("#templates-table-container");

	if (!templates.length) {
		container.html(`<p style="color:var(--text-muted);padding:8px 0">
			No biometric templates enrolled on this device yet. Use the Get BioData button to fetch templates.
		</p>`);
		return;
	}

	const tick = `<span style="color:var(--green-500)">✓</span>`;
	const dash = `<span style="color:var(--text-muted)">—</span>`;

	const rows = templates.map(t => {
		const parent_link = `/app/biometric-template/${encodeURIComponent(t.parent_name)}`;
		return `
			<tr>
				<td style="font-family:var(--font-mono);font-size:13px">${frappe.utils.escape_html(t.user_id || "")}</td>
				<td>
					<a href="${parent_link}" target="_blank">${frappe.utils.escape_html(t.employee_name || "")}</a>
				</td>
				<td style="text-align:center">${t.has_fp ? tick : dash}</td>
				<td style="text-align:center">${t.has_face ? tick : dash}</td>
				<td style="text-align:center">${t.has_palm ? tick : dash}</td>
				<td style="text-align:center">${t.has_password ? tick : dash}</td>
				<td style="text-align:center">${t.has_card ? tick : dash}</td>
			</tr>
		`;
	}).join("");

	container.html(`
		<div style="border:1px solid var(--border-color);border-radius:8px;overflow:hidden">
			<table class="table table-sm" style="margin:0">
				<thead style="background:var(--bg-light-gray)">
					<tr>
						<th style="width:130px">PIN</th>
						<th>Employee</th>
						<th style="width:60px;text-align:center">FP</th>
						<th style="width:60px;text-align:center">Face</th>
						<th style="width:60px;text-align:center">Palm</th>
						<th style="width:80px;text-align:center">Password</th>
						<th style="width:60px;text-align:center">Card</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
	`);

}

const SCHEDULED_JOB_PREFIX_TO_FREQUENCY = {
	checkin: "checkin_event_frequency",
	biodata: "biodata_event_frequency",
	flip:    "flip_event_frequency",
	absent:  "absent_event_frequency"
};

function autosave_on_change(frm) {
	if (frm._autosaving) return;
	if (frm.is_new()) return;
	if (!frm.is_dirty()) return;
	frm._autosaving = true;
	frm.save()
		.then(() => {
			frappe.show_alert({ message: __("Schedule updated"), indicator: "green" }, 3);
			render_scheduled_job_links(frm);
		})
		.finally(() => {
			frm._autosaving = false;
		});
}

// The absent pass reports every shift window it looked at, and why it passed
// over the ones it skipped — a run that marks nobody has to be distinguishable
// from a run that never happened (device outage, rest day, nothing due yet).
function run_absent_marking(frm, dry_run) {
	const day = frm.doc.absent_preview_date || null;
	const title = dry_run ? __("Previewing absentees") : __("Marking absentees");

	const call = () => run_with_progress(
		title,
		dry_run
			? __("Checking closed shift windows for missing check-ins...")
			: __("Marking Absent where no check-in logs exist..."),
		{
			method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.mark_absentees_now",
			args: { from_date: day, to_date: day, dry_run: dry_run ? 1 : 0 },
			callback: function(r) {
				if (r.exc || !r.message) return;
				show_absent_result(r.message, dry_run);
			}
		}
	);

	if (frm.is_dirty()) {
		frm.save().then(call);
	} else {
		call();
	}
}

function show_absent_result(result, dry_run) {
	const windows = result.windows || [];
	const total = dry_run
		? windows.reduce((n, w) => n + ((w.would_mark || []).length), 0)
		: (result.marked || 0);

	const rows = windows.map((w) => {
		const marked = dry_run ? (w.would_mark || []).length : (w.marked_employees || []).length;
		const note = w.skipped
			? `<span style="color:var(--text-muted)">${frappe.utils.escape_html(w.skipped)}</span>`
			: [
				`${w.scanned || 0} scanned`,
				`${w.already_marked || 0} already marked`,
				`${w.on_leave || 0} on leave`,
				`${w.rest_day || 0} week off / holiday`,
				(w.other_shift ? `${w.other_shift} on another shift` : ""),
				// Skipped rather than marked: without a week-off list assigned to
				// the employee there is no way to tell a working day from a rest
				// day, and marking would risk an Absent on someone's day off.
				(w.no_week_off_list
					? `<span style="color:var(--orange-500)">${w.no_week_off_list} no week-off list — skipped</span>`
					: "")
			].filter(Boolean).join(", ");

		return `<tr>
			<td>${frappe.utils.escape_html(w.date || "")}</td>
			<td>${frappe.utils.escape_html(w.shift || "")}</td>
			<td>${frappe.utils.escape_html(String(w.window_end || ""))}</td>
			<td style="text-align:right">${w.assigned || 0}</td>
			<td style="text-align:right;font-weight:600">${marked}</td>
			<td>${note}</td>
		</tr>`;
	}).join("");

	const body = windows.length
		? `<table class="table table-bordered" style="font-size:12px">
				<thead><tr>
					<th>Date</th><th>Shift</th><th>Window closed</th>
					<th style="text-align:right">Assigned</th>
					<th style="text-align:right">${dry_run ? "Would mark" : "Marked"}</th>
					<th>Notes</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>`
		: `<p>${__("No shift window is due yet for {0} - {1}. A window becomes due {2} minutes after the shift closes.",
				[result.from_date, result.to_date, result.grace_minutes])}</p>`;

	new frappe.ui.Dialog({
		title: dry_run ? __("Would mark {0} Absent", [total]) : __("Marked {0} Absent", [total]),
		size: "large",
		fields: [{ fieldtype: "HTML", options: body }]
	}).show();
}

function render_scheduled_job_links(frm) {
	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.get_scheduled_job_links",
		callback: (r) => {
			const data = (r && r.message) || {};
			for (const [prefix, fieldname] of Object.entries(SCHEDULED_JOB_PREFIX_TO_FREQUENCY)) {
				inject_job_link(frm, fieldname, data[prefix]);
			}
		}
	});
}

function inject_job_link(frm, fieldname, info) {
	const field = frm.fields_dict[fieldname];
	if (!field || !field.$wrapper) return;
	const $w = field.$wrapper;
	$w.find(".biometric-job-link").remove();

	if (!info) {
		$w.append(`<div class="biometric-job-link" style="margin-top:6px;font-size:12px;color:var(--text-muted)">
			Save the form to create the scheduled job.
		</div>`);
		return;
	}

	const status_color = info.stopped ? "var(--red-500)" : "var(--green-500)";
	const status_text  = info.stopped ? "Stopped" : "Active";
	const href = `/app/scheduled-job-type/${encodeURIComponent(info.name)}`;
	$w.append(`<div class="biometric-job-link" style="margin-top:6px;font-size:12px">
		<a href="${href}" target="_blank">View Scheduled Job</a>
		<span style="color:${status_color};margin-left:8px">● ${status_text}</span>
	</div>`);
}

frappe.ui.form.on("Biometric Device", {
	device_sn: function(frm, cdt, cdn) {
		refresh_device_options(frm);
		apply_serial_capability_profile(frm, cdt, cdn);
	},
	device_location: function(frm) { refresh_device_options(frm); },
	devices_remove: function(frm) { refresh_device_options(frm); }
});

// Capabilities are set once, when the device is added: the serial's prefix says
// which family of terminal it is, and therefore which credentials it carries.
// Only ever applied to a row that has not been saved yet, so it can never
// overwrite a flag an operator (or Detect Capabilities) has already set.
function apply_serial_capability_profile(frm, cdt, cdn) {
	const row = locals[cdt] && locals[cdt][cdn];
	if (!row || !row.__islocal || !row.device_sn) return;
	if (row.__capability_profile_applied === row.device_sn) return;

	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.capability_profile_for_serial",
		args: { device_sn: row.device_sn },
		callback(r) {
			const profile = r.message;
			if (!profile || !profile.capabilities) return;
			row.__capability_profile_applied = row.device_sn;
			Object.keys(profile.capabilities).forEach(flag => {
				frappe.model.set_value(cdt, cdn, flag, profile.capabilities[flag]);
			});
			frm.refresh_field("devices");
			const on = Object.keys(profile.capabilities)
				.filter(f => profile.capabilities[f])
				.map(f => CAPABILITY_LABELS[f])
				.join(", ");
			frappe.show_alert({
				message: __("{0} ({1}) — set to {2}", [profile.model, profile.prefix, on]),
				indicator: "blue"
			}, 7);
		}
	});
}

frappe.ui.form.on("Biometric Checkin", {
	poll_devices_add: function(frm) { refresh_device_options(frm); },
	device: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const match = _find_device_by_location(frm, row.device);
		const sn = match ? (match.device_sn || "") : "";
		frappe.model.set_value(cdt, cdn, "device_sn", sn);
		if (match) {
			const loc = match.device_location || match.device_sn || "";
			if (loc && row.device !== loc) {
				frappe.model.set_value(cdt, cdn, "device", loc);
			}
		}
	}
});

function backfill_poll_device_sns(frm) {
	(frm.doc.poll_devices || []).forEach(row => {
		if (!row.device) return;
		const match = _find_device_by_location(frm, row.device);
		if (!match) return;
		const loc = match.device_location || match.device_sn || "";
		const sn = match.device_sn || "";
		if (loc && row.device !== loc) {
			frappe.model.set_value(row.doctype, row.name, "device", loc);
		}
		if (sn && row.device_sn !== sn) {
			frappe.model.set_value(row.doctype, row.name, "device_sn", sn);
		}
	});
}

function get_enabled_filters(frm) {
	return {
		company:     !!frm.doc.scope_company,
		farm:        !!frm.doc.farm,
		department:  !!frm.doc.department,
		designation: !!frm.doc.designation,
		employee:    !!frm.doc.employee
	};
}

function _dialog_selected_sns(d) {
	return (d._selected_sns || []).slice();
}

function _dialog_selected_locations(d) {
	const by_sn = d._device_by_sn || {};
	return _dialog_selected_sns(d).map(sn => {
		const dev = by_sn[sn];
		return (dev && (dev.device_location || dev.device_sn)) || sn;
	});
}

// Union of the selected device(s)' farms, used to scope the employee candidate
// list to what the server will accept. Returns [] (no client scoping) when any
// selected device is unrestricted (no farms), since the server enforces per
// device anyway and we must not hide employees valid for that device.
function _dialog_device_farms(d) {
	const by_sn = d._device_by_sn || {};
	let sns = _dialog_selected_sns(d);
	if (!sns.length) sns = Object.keys(by_sn);
	if (!sns.length) return [];
	const set = new Set();
	for (const sn of sns) {
		const dev = by_sn[sn];
		const farms = (dev && dev.farms) || [];
		if (!farms.length) return [];
		farms.forEach(f => { if (f) set.add(f); });
	}
	return Array.from(set);
}

function open_bulk_user_dialog(command_type, default_sn, default_location, on_success, enabled_filters) {
	enabled_filters = enabled_filters || {
		company: false, farm: false, department: false, designation: false, employee: false
	};
	const any_filter_enabled = enabled_filters.company || enabled_filters.farm
		|| enabled_filters.department || enabled_filters.designation || enabled_filters.employee;
	let dialog_title = {
		"Add User":    "Bulk Add Users to Device",
		"Update User": "Bulk Update Users on Device",
		"Delete User": "Bulk Delete Users from Device",
		"Poll BioData": "Poll BioData from Device"
	}[command_type];

	let indicator = {
		"Add User":    "green",
		"Update User": "blue",
		"Delete User": "red",
		"Poll BioData": "blue"
	}[command_type];

	const is_poll = command_type === "Poll BioData";

	let d = new frappe.ui.Dialog({
		title: dialog_title,
		size: "extra-large",
		fields: [
			{
				fieldname: "filter_company",
				fieldtype: "Autocomplete",
				label: "Company",
				options: [],
				columns: 2,
				hidden: !enabled_filters.company,
				change() {
					close_autocomplete("filter_company");
					reload_with_filters();
				}
			},
			{ fieldname: "col_break_filter_farm", fieldtype: "Column Break" },
			{
				fieldname: "filter_farm",
				fieldtype: "Autocomplete",
				label: "Farm",
				options: [],
				columns: 2,
				hidden: !enabled_filters.farm,
				change() {
					close_autocomplete("filter_farm");
					reload_with_filters();
				}
			},
			{ fieldname: "col_break_filter_0", fieldtype: "Column Break" },
			{
				fieldname: "filter_department",
				fieldtype: "Autocomplete",
				label: "Department",
				options: [],
				columns: 2,
				hidden: !enabled_filters.department,
				change() {
					close_autocomplete("filter_department");
					reload_with_filters();
				}
			},
			{ fieldname: "col_break_filter_1", fieldtype: "Column Break" },
			{
				fieldname: "filter_designation",
				fieldtype: "Autocomplete",
				label: "Designation",
				options: [],
				columns: 2,
				hidden: !enabled_filters.designation,
				change() {
					close_autocomplete("filter_designation");
					reload_with_filters();
				}
			},
			{ fieldname: "col_break_filter_2", fieldtype: "Column Break" },
			{
				fieldname: "filter_employee",
				fieldtype: "Link",
				label: "Employee",
				options: "Employee",
				columns: 2,
				hidden: !enabled_filters.employee,
				get_query() {
					let f = { status: "Active" };
					let dept = d.get_value("filter_department");
					let desg = d.get_value("filter_designation");
					let comp = d.get_value("filter_company");
					let frm_ = d.get_value("filter_farm");
					if (dept) f.department  = dept;
					if (desg) f.designation = desg;
					if (comp) f.company     = comp;
					if (frm_) {
						f.custom_farm = frm_;
					} else {
						const device_farms = _dialog_device_farms(d);
						if (device_farms.length) f.custom_farm = ["in", device_farms];
					}
					return { filters: f };
				},
				change() { reload_with_filters(); }
			},
			{ fieldname: "col_break_filter_3", fieldtype: "Column Break" },
			{
				fieldname: "clear_filters_html",
				fieldtype: "HTML",
				hidden: !any_filter_enabled,
				options: `<div style="display:flex;align-items:flex-end;height:100%;padding-bottom:4px">
					<button type="button" id="clear-filters-btn"
						title="Clear filters"
						style="background:transparent;border:1px solid var(--color-border-tertiary);
							border-radius:6px;width:48px;height:48px;cursor:pointer;
							color:var(--color-text-secondary);font-size:24px;font-weight:600;
							line-height:1;display:none;align-items:center;justify-content:center">
						✕
					</button>
				</div>`
			},
			{ fieldname: "table_section", fieldtype: "Section Break" },
			{
				fieldname: "user_table_html",
				fieldtype: "HTML",
				options: `<div id="bulk-user-table" style="margin-top:8px">
					<p style="color:var(--color-text-secondary)">Loading...</p>
				</div>`
			}
		],
		primary_action_label: is_poll ? "Poll Selected" : `${command_type.split(" ")[0]} Selected`,
		primary_action() {
			const sns = _dialog_selected_sns(d);
			if (!sns.length) {
				frappe.msgprint("Tick at least one device column header.");
				return;
			}

			const assignments = build_per_device_assignments();
			const total_picks = assignments.reduce((n, a) => n + a.users.length, 0);
			if (!total_picks) {
				frappe.msgprint("Tick at least one device cell for the users you want to apply.");
				return;
			}

			const locs = _dialog_selected_locations(d);
			const loc_label = locs.length === 1 ? locs[0] : `${locs.length} device(s)`;

			if (is_poll) {
				const poll_assignments = assignments.map(a => ({
					device_sn: a.device_sn,
					pins: a.users.map(u => u.user_id).filter(Boolean)
				})).filter(a => a.pins.length);
				const total_pin_picks = poll_assignments.reduce((n, a) => n + a.pins.length, 0);
				if (!total_pin_picks) {
					frappe.msgprint("None of the ticked employees have a PIN.");
					return;
				}
				frappe.confirm(`Poll BioData (${total_pin_picks} pick(s) across ${poll_assignments.length} device(s))?`, () => {
					run_with_progress(
						__("Polling BioData"),
						__("Queuing biodata poll commands..."),
						{
							method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.request_biodata_per_device",
							args: { assignments: JSON.stringify(poll_assignments) },
							callback(r) {
								if (!r.exc) {
									d.hide();
									if (on_success) on_success();
									const n = (r.message && r.message.queued) || 0;
									frappe.show_alert({
										message: `${n} biodata queries queued. Templates will arrive within 30 seconds.`,
										indicator: indicator
									}, 10);
								}
							}
						}
					);
				});
				return;
			}

			let label = command_type === "Delete User"
				? `Delete ${total_picks} user-device pick(s) across ${assignments.length} device(s)?`
				: `${command_type.split(" ")[0]} ${total_picks} user-device pick(s) across ${assignments.length} device(s)?`;
			const source_dev = d._source_sn && d._device_by_sn && d._device_by_sn[d._source_sn];
			if (command_type !== "Delete User" && source_dev) {
				label += `<br>Templates from ${frappe.utils.escape_html(source_dev.device_location || source_dev.device_sn)}.`;
			}

			frappe.confirm(label, () => {
				run_with_progress(
					__("{0} ({1} picks)", [command_type, total_picks]),
					__("Queuing {0} commands...", [command_type]),
					{
						method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.bulk_command_per_device",
						args: {
							assignments: JSON.stringify(assignments),
							command_type: command_type,
							source_device_sn: d._source_sn || null
						},
						callback(r) {
							if (!r.exc) {
								d.hide();
								if (on_success) on_success();
								const m = r.message || {};
								let msg = `${m.queued || 0} command(s) queued across ${assignments.length} device(s).`;
								if (m.failed) msg += ` ${m.failed} failed.`;
								frappe.show_alert({ message: msg, indicator: indicator }, 8);
							}
						}
					}
				);
			});
		}
	});

	function build_per_device_assignments() {
		const m = d._bulk_model;
		if (!m) return [];
		// a picked source sends its face as-is; otherwise Skip Face (default on)
		const skip_face = !d._source_sn && d._skip_face !== false;
		return (d._selected_sns || [])
			.filter(sn => m.checked[sn])
			.map(sn => {
				const checked = m.checked[sn];
				const locked = m.locked[sn];
				const users = [];
				for (let i = 0; i < m.users.length; i++) {
					if (!checked[i] || locked[i]) continue;
					const user = m.users[i];
					users.push({
						user_id:       user.user_id,
						employee_name: user.employee_name,
						privilege:     m.privilege[i] || user.privilege || "0",
						row_name:      user.row_name || null,
						skip_name:     m.skip[i] ? 1 : 0,
						skip_face:     skip_face ? 1 : 0
					});
				}
				return { device_sn: sn, users };
			})
			.filter(a => a.users.length);
	}

	const apply_toolbar_layout = () => {
		const $anchor = d.$wrapper.find(`[data-fieldname="clear_filters_html"]`).first();
		if (!$anchor.length) return;
		const $col = $anchor.closest(".form-column");
		const $row = $col.parent();
		if (!$row.length) return;

		const $cols = $row.children(".form-column");
		const filter_field_names = [
			"filter_company", "filter_farm", "filter_department",
			"filter_designation", "filter_employee"
		];
		let visible_filter_count = 0;
		$cols.each(function () {
			const $c = $(this);
			if ($c.find(`[data-fieldname="clear_filters_html"]`).length) return;
			const filter_match = filter_field_names.find(fn =>
				$c.find(`[data-fieldname="${fn}"]`).length > 0
			);
			if (!filter_match) return;
			const field = d.get_field(filter_match);
			const is_hidden = !!(field && field.df && field.df.hidden);
			$c[0].style.setProperty("display", is_hidden ? "none" : "block", "important");
			if (!is_hidden) visible_filter_count++;
		});

		const has_visible_filters = visible_filter_count > 0;
		const $clearCol = $cols.filter(function () {
			return $(this).find(`[data-fieldname="clear_filters_html"]`).length > 0;
		});
		if ($clearCol.length) {
			$clearCol[0].style.setProperty(
				"display", has_visible_filters ? "block" : "none", "important"
			);
		}
		const grid_cols = has_visible_filters
			? `${"1fr ".repeat(visible_filter_count)}56px`
			: "1fr";

		$row.css({
			"display":               "grid",
			"grid-template-columns": grid_cols,
			"gap":                   "20px",
			"align-items":           "end",
			"width":                 "100%",
			"padding":               "0",
			"margin":                "0"
		});

		$cols.css({
			"padding":     "0",
			"margin":      "0",
			"min-width":   "0",
			"max-width":   "100%",
			"width":       "100%",
			"float":       "none",
			"overflow":    "hidden",
			"box-sizing":  "border-box"
		});

		$cols.find("*").css("box-sizing", "border-box");
		$cols.find(
			".frappe-control, .form-group, .control-input, .control-input-wrapper, " +
			".like-disabled-input, .awesomplete, .link-field, input, select"
		).css({
			"width":     "100%",
			"min-width": "0",
			"max-width": "100%"
		});
		$cols.find(".input-max-width").css("max-width", "100%");
		$cols.find("[style*='min-width']").each(function () {
			this.style.minWidth = "0";
		});

		$cols.css("overflow", "visible");
		$cols.find(".awesomplete").css({ "position": "relative", "z-index": "1050" });
		$cols.find(".awesomplete ul").css({ "z-index": "1051" });
	};

	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_devices",
		callback(r) {
			if (r.message) {
				d._devices = r.message;
				d._device_by_label = {};
				d._device_by_sn = {};
				r.message.forEach(dev => {
					const label = dev.device_location || dev.device_sn;
					d._device_by_label[label] = dev;
					d._device_by_sn[dev.device_sn] = dev;
				});
				d._selected_sns = default_sn ? [default_sn] : [];
				apply_toolbar_layout();
				reload_users();
			}
		}
	});

	let reload_timer = null;
	function reload_users() {
		clearTimeout(reload_timer);
		reload_timer = setTimeout(load_users, 30);
	}

	if (any_filter_enabled) refresh_filter_options();

	function refresh_filter_options() {
		let department  = d.get_value("filter_department")  || null;
		let designation = d.get_value("filter_designation") || null;
		let company     = d.get_value("filter_company")     || null;
		let farm        = d.get_value("filter_farm")        || null;
		const device_farms = _dialog_device_farms(d);
		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_active_filter_options",
			args: { department, designation, company, farm, farms: device_farms.length ? JSON.stringify(device_farms) : null },
			callback(r) {
				let opts = r.message || {};
				let valid_designations = opts.designations || [];
				let valid_departments  = opts.departments  || [];
				set_autocomplete_options("filter_designation", valid_designations);
				set_autocomplete_options("filter_department",  valid_departments);
				set_autocomplete_options("filter_company",     opts.companies     || []);
				set_autocomplete_options("filter_farm",        opts.farms         || []);
				set_filter_label("filter_company",     "Company",     opts.company_count);
				set_filter_label("filter_farm",        "Farm",        opts.farm_count);
				set_filter_label("filter_department",  "Department",  opts.department_count);
				set_filter_label("filter_designation", "Designation", opts.designation_count);
				set_filter_label("filter_employee",    "Employee",    opts.employee_count);

				if (enabled_filters.farm) {
					const farm_available = (opts.farms || []).length > 0;
					d.set_df_property("filter_farm", "hidden", !farm_available);
				}
				apply_toolbar_layout();

				if (designation && !valid_designations.includes(designation)) {
					d.set_value("filter_designation", "");
				}
				if (department && !valid_departments.includes(department)) {
					d.set_value("filter_department", "");
				}
			}
		});
	}

	function set_filter_label(fieldname, base_label, count) {
		if (count == null) return;
		d.set_df_property(fieldname, "label", `${base_label} (${count})`);
	}

	function set_autocomplete_options(fieldname, values) {
		let field = d.get_field(fieldname);
		if (!field) return;
		field.df.options = values;
		if (typeof field.set_data === "function") {
			field.set_data(values);
		}
	}

	function close_autocomplete(fieldname) {
		let field = d.get_field(fieldname);
		if (field && field.awesomplete) {
			field.awesomplete.close();
		}
	}

	d.show();
	apply_toolbar_layout();
	setTimeout(apply_toolbar_layout, 50);
	setTimeout(apply_toolbar_layout, 200);

	d.$wrapper.on("click", "#clear-filters-btn", () => {
		d.set_value("filter_employee",    "");
		d.set_value("filter_designation", "");
		d.set_value("filter_department",  "");
		d.set_value("filter_company",     "");
		d.set_value("filter_farm",        "");
		reload_with_filters();
	});

	function reload_with_filters() {
		toggle_clear_btn();
		if (any_filter_enabled) refresh_filter_options();
		validate_employee_against_cascade();
		reload_users();
	}

	function validate_employee_against_cascade() {
		let emp  = d.get_value("filter_employee");
		let dept = d.get_value("filter_department");
		let desg = d.get_value("filter_designation");
		let comp = d.get_value("filter_company");
		let frm_ = d.get_value("filter_farm");
		if (!emp || (!dept && !desg && !comp && !frm_)) return;

		if (d._employees) {
			const e = d._employees.find(x => x.employee === emp);
			if (!e || (dept && e.department !== dept) || (desg && e.designation !== desg)
				|| (comp && e.company !== comp) || (frm_ && e.farm !== frm_)) {
				d.set_value("filter_employee", "");
			}
			return;
		}

		let filters = { name: emp };
		if (dept) filters.department  = dept;
		if (desg) filters.designation = desg;
		if (comp) filters.company     = comp;
		if (frm_) filters.custom_farm = frm_;

		frappe.db.get_list("Employee", { filters, limit: 1 }).then(rows => {
			if (!rows || !rows.length) {
				d.set_value("filter_employee", "");
			}
		});
	}

	function toggle_clear_btn() {
		let any = d.get_value("filter_employee")
			   || d.get_value("filter_designation")
			   || d.get_value("filter_department")
			   || d.get_value("filter_company")
			   || d.get_value("filter_farm");
		d.$wrapper.find("#clear-filters-btn").css("display", any ? "flex" : "none");
	}

	function get_filter_args() {
		const device_farms = _dialog_device_farms(d);
		return {
			employee:    d.get_value("filter_employee")    || null,
			designation: d.get_value("filter_designation") || null,
			department:  d.get_value("filter_department")  || null,
			company:     d.get_value("filter_company")     || null,
			farm:        d.get_value("filter_farm")        || null,
			farms:       device_farms.length ? JSON.stringify(device_farms) : null
		};
	}

	// Device rosters and the active employee list are fetched once per dialog;
	// every filter change after that is answered from memory.
	function fetch_dialog_data() {
		if (d._data_promise) return d._data_promise;
		const all_sns = (d._devices || []).map(dev => dev.device_sn);
		const call = (method, args) => new Promise(resolve => frappe.call({
			method, args,
			callback: r => resolve((r && r.message) || null),
			error: () => resolve(null)
		}));
		d._data_promise = Promise.all([
			call("upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_device_users_multi",
				{ device_sns: JSON.stringify(all_sns) }),
			call("upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employees",
				{ status: "Active" })
		]).then(([payload, employees]) => {
			payload = payload || { users: [], pins_by_device: {} };
			d._device_users = payload.users || [];
			d._device_pins = {};
			for (const sn_key in (payload.pins_by_device || {})) {
				d._device_pins[sn_key] = new Set(payload.pins_by_device[sn_key] || []);
			}
			d._employees = employees || [];
			d._has_farm = d._employees.some(e => e.farm);
		});
		return d._data_promise;
	}

	// Same rules as biometric_user.get_employees, applied to the cached roster.
	function filter_employees(f) {
		const device_farms = f.farms ? JSON.parse(f.farms) : [];
		let scoped = null;
		if (d._has_farm) {
			if (device_farms.length) {
				scoped = (f.farm && device_farms.includes(f.farm)) ? [f.farm] : device_farms;
			} else if (f.farm) {
				scoped = [f.farm];
			}
		}
		return (d._employees || []).filter(e =>
			(!f.employee    || e.employee    === f.employee)
			&& (!f.designation || e.designation === f.designation)
			&& (!f.department  || e.department  === f.department)
			&& (!f.company     || e.company     === f.company)
			&& (!scoped        || scoped.includes(e.farm))
		);
	}

	function load_users() {
		let container = d.$wrapper.find("#bulk-user-table");
		if (!d._employees) {
			container.html(`<p style="color:var(--color-text-secondary)">Loading...</p>`);
		}
		const seq = (d._load_seq = (d._load_seq || 0) + 1);

		fetch_dialog_data().then(() => {
			if (seq !== d._load_seq) return;
			const filters = get_filter_args();
			const has_filters = filters.employee || filters.designation || filters.department
				|| filters.company || filters.farm;

			if (command_type === "Add User" || command_type === "Poll BioData") {
				render_table(filter_employees(filters).map(e => ({
					user_id:       e.user_id,
					employee_name: e.full_name,
					privilege:     "0"
				})), command_type);
			} else if (!has_filters) {
				render_table(d._device_users, command_type);
			} else {
				const allowed_pins = new Set(filter_employees(filters).map(e => e.user_id));
				render_table(d._device_users.filter(u => allowed_pins.has(u.user_id)), command_type);
			}
		});
	}

	// The roster can be thousands of employees × every device, so the ticks,
	// Skip and Privilege live in memory and only the rows scrolled into view
	// are in the DOM. Handlers are delegated on the container.
	const OVERSCAN = 10;

	function render_table(users, action) {
		let container = d.$wrapper.find("#bulk-user-table");
		d._bulk_users_data = users;

		if (!users.length) {
			d._bulk_model = null;
			container[0].innerHTML = `<p style="color:var(--color-text-secondary);padding:8px 0">
				No users found for this action on the selected device.
			</p>`;
			return;
		}

		const esc = frappe.utils.escape_html;
		const n = users.length;
		let show_privilege = action === "Add User" || action === "Update User";
		let show_skip_name = action === "Add User" || action === "Update User";
		let skip_name_col  = show_skip_name ? `<th class="bulk-col-skip" style="text-align:center;white-space:nowrap">Skip?</th>` : "";
		let privilege_col  = show_privilege ? `<th class="bulk-col-priv" style="white-space:nowrap">Privilege</th>` : "";
		const all_devices  = d._devices || [];
		let device_pins    = d._device_pins || {};

		let lock_title = "";
		if (action === "Add User") lock_title = "Already enrolled on this device";
		else if (action === "Update User" || action === "Delete User") lock_title = "Not enrolled on this device";
		lock_title = esc(lock_title);

		const cell_state = (has, is_target) => {
			let default_check;
			let locked = false;
			if (action === "Add User") {
				default_check = !has;
				locked = has;
			} else if (action === "Update User" || action === "Delete User") {
				default_check = has;
				locked = !has;
			} else {
				default_check = true;
			}
			return { locked, checked: locked ? (is_target && has) : (is_target && default_check) };
		};

		const active_filters = get_filter_args();
		const fill_all = !!(active_filters.employee || active_filters.designation || active_filters.department
			|| active_filters.company || active_filters.farm);

		const targets = new Set(d._selected_sns || []);
		const m = d._bulk_model = {
			users,
			has: {}, locked: {}, checked: {},
			skip: new Uint8Array(n),
			privilege: users.map(u => u.privilege === "14" ? "14" : "0")
		};
		for (const dev of all_devices) {
			const sn = dev.device_sn;
			const pins = device_pins[sn] || new Set();
			const has = m.has[sn] = new Uint8Array(n);
			const locked = m.locked[sn] = new Uint8Array(n);
			const checked = m.checked[sn] = new Uint8Array(n);
			const is_target = targets.has(sn);
			for (let i = 0; i < n; i++) {
				has[i] = pins.has(users[i].user_id) ? 1 : 0;
				const st = cell_state(!!has[i], is_target);
				locked[i] = st.locked ? 1 : 0;
				checked[i] = (st.checked || (fill_all && is_target && !st.locked)) ? 1 : 0;
			}
		}

		let device_cols = all_devices.map(dev => {
			const checked = targets.has(dev.device_sn) ? "checked" : "";
			const label = dev.device_location || dev.device_sn;
			return `<th style="min-width:120px;text-align:center;white-space:nowrap;padding:6px 10px"
				title="${esc(label)} (${esc(dev.device_sn)})">
				<label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-weight:600;margin:0;white-space:nowrap">
					<input type="checkbox" class="bulk-device-header-check"
						data-sn="${esc(dev.device_sn)}" ${checked}
						style="margin:0;flex:0 0 auto">
					<span style="white-space:nowrap">${esc(label)}</span>
				</label>
			</th>`;
		}).join("");
		const n_cols = 2 + (show_skip_name ? 1 : 0) + (show_privilege ? 1 : 0) + all_devices.length;

		function row_html(i) {
			const u = users[i];
			const live = new Set(d._selected_sns || []);
			let skip_name_cell = show_skip_name
				? `<td class="bulk-col-skip" style="text-align:center"><input type="checkbox" class="skip-name-check" data-idx="${i}"${m.skip[i] ? " checked" : ""}></td>`
				: "";
			let privilege_cell = show_privilege
				? `<td class="bulk-col-priv"><select class="form-control form-control-sm privilege-sel" data-idx="${i}" style="width:100px"><option value="0"${m.privilege[i] === "14" ? "" : " selected"}>User</option><option value="14"${m.privilege[i] === "14" ? " selected" : ""}>Admin</option></select></td>`
				: "";
			let device_cells = "";
			for (const dev of all_devices) {
				const sn = dev.device_sn;
				const has = m.has[sn][i];
				const locked = m.locked[sn][i];
				device_cells += `<td class="bdc${live.has(sn) ? " is-target" : ""}" data-sn="${esc(sn)}"><span class="bulk-device-presence${has ? " has" : ""}">${has ? "✓" : "—"}</span><input type="checkbox" class="bulk-device-cell-check"${locked ? ` title="${lock_title}" disabled` : ""}${m.checked[sn][i] ? " checked" : ""}></td>`;
			}
			let status_badge = u.status ? ` <span class="bulk-status">${esc(u.status)}</span>` : "";
			return `<tr class="vr" data-idx="${i}"><td class="bulk-col-pin">${esc(u.user_id || "")}</td><td class="bulk-col-name">${esc(u.employee_name || "")}${status_badge}</td>${skip_name_cell}${privilege_cell}${device_cells}</tr>`;
		}

		const skip_face_on = d._skip_face !== false;
		let skip_names_toggle = show_skip_name ? `
			<button class="btn btn-xs btn-default" id="skip-names-btn"
					title="Toggle Skip for all rows">Skip</button>
			<label id="skip-face-label" class="uds-check-label" style="display:${d._source_sn ? "none" : "inline-flex"}">
				<input type="checkbox" id="skip-face-check" style="margin:0" ${skip_face_on ? "checked" : ""}>${__("Skip Face")}
			</label>
			<label class="uds-field">
				<span>${__("Templates from")}</span>
				<select id="source-device-sel">
					<option value="">${__("Any device")}</option>
					${all_devices.map(dev => `<option value="${esc(dev.device_sn)}" data-sub="${esc(dev.device_sn)}"${d._source_sn === dev.device_sn ? " selected" : ""}>${esc(dev.device_location || dev.device_sn)}</option>`).join("")}
				</select>
			</label>` : "";

		container[0].innerHTML = `
			<style>
				#bulk-user-table tr.vr { height:37px; }
				#bulk-user-table tr.vr > td { vertical-align:middle; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
				#bulk-user-table tr.vspace > td { padding:0; border:0; }
				#bulk-user-table td.bulk-col-pin { font-family:var(--font-mono); font-size:13px; }
				#bulk-user-table .bulk-status { font-size:11px; padding:2px 6px; border-radius:4px;
					background:var(--color-background-success); color:var(--color-text-success); }
				#bulk-user-table td.bdc { min-width:120px; text-align:center; }
				#bulk-user-table td.bdc .bulk-device-presence { color:var(--text-muted); }
				#bulk-user-table td.bdc .bulk-device-presence.has { color:var(--green-500); }
				#bulk-user-table td.bdc .bulk-device-cell-check { display:none; margin:0; }
				#bulk-user-table td.bdc.is-target .bulk-device-presence { display:none; }
				#bulk-user-table td.bdc.is-target .bulk-device-cell-check { display:inline-block; }
				#bulk-user-table td.bdc .bulk-device-cell-check:disabled { opacity:0.6; cursor:not-allowed; }
			</style>
			<div class="uds-toolbar">
				<button class="btn btn-xs btn-default" id="select-all-btn"
					title="Tick every cell in currently-selected device columns">Select All in Targets</button>
				<button class="btn btn-xs btn-default" id="deselect-all-btn">Deselect All</button>
				${skip_names_toggle}
				<span class="uds-count" id="selected-count">0 picks</span>
			</div>
			<div class="bulk-user-scroller"
				style="max-height:400px;overflow:auto;
				border:1px solid var(--color-border-tertiary);border-radius:8px">
				<table class="table table-sm sticky-head-table bulk-user-table-fixed" style="margin:0">
					<thead>
						<tr>
							<th class="bulk-col-pin"  style="width:90px">PIN</th>
							<th class="bulk-col-name" style="width:220px">Name</th>
							${skip_name_col}
							${privilege_col}
							${device_cols}
						</tr>
					</thead>
					<tbody></tbody>
				</table>
			</div>
		`;
		const source_sel = container[0].querySelector("#source-device-sel");
		if (source_sel && upande_ta.device_select && upande_ta.device_select.enhance) upande_ta.device_select.enhance(source_sel);
		const scroller = container[0].querySelector(".bulk-user-scroller");
		const tbody = container[0].querySelector("tbody");
		let row_h = 37;
		let drawn = [-1, -1];

		function draw(force) {
			const view_h = scroller.clientHeight || 400;
			const first = Math.max(0, Math.floor(scroller.scrollTop / row_h) - OVERSCAN);
			const last = Math.min(n, Math.ceil((scroller.scrollTop + view_h) / row_h) + OVERSCAN);
			if (!force && first === drawn[0] && last === drawn[1]) return;
			drawn = [first, last];
			let html = first ? `<tr class="vspace"><td colspan="${n_cols}" style="height:${first * row_h}px"></td></tr>` : "";
			for (let i = first; i < last; i++) html += row_html(i);
			if (last < n) html += `<tr class="vspace"><td colspan="${n_cols}" style="height:${(n - last) * row_h}px"></td></tr>`;
			tbody.innerHTML = html;
		}

		function update_count() {
			let picks = 0;
			for (const sn of (d._selected_sns || [])) {
				const checked = m.checked[sn], locked = m.locked[sn];
				if (!checked) continue;
				for (let i = 0; i < n; i++) if (checked[i] && !locked[i]) picks++;
			}
			container.find("#selected-count").text(`${picks} pick(s)`);
		}

		const cell_of = el => {
			const $td = $(el).closest("td");
			return { sn: $td.attr("data-sn"), i: parseInt($td.closest("tr").attr("data-idx")) };
		};

		container.off(".bulk")
			.on("click.bulk", "#select-all-btn", () => {
				for (const sn of (d._selected_sns || [])) {
					const checked = m.checked[sn], locked = m.locked[sn];
					if (checked) for (let i = 0; i < n; i++) if (!locked[i]) checked[i] = 1;
				}
				draw(true);
				update_count();
			})
			.on("click.bulk", "#deselect-all-btn", () => {
				for (const sn in m.checked) m.checked[sn].fill(0);
				draw(true);
				update_count();
			})
			.on("change.bulk", ".bulk-device-cell-check", function() {
				const { sn, i } = cell_of(this);
				if (m.checked[sn]) m.checked[sn][i] = this.checked ? 1 : 0;
				update_count();
			})
			.on("change.bulk", ".skip-name-check", function() {
				m.skip[parseInt($(this).attr("data-idx"))] = this.checked ? 1 : 0;
			})
			.on("change.bulk", "#source-device-sel", function() {
				d._source_sn = $(this).val() || "";
				if (!d._source_sn) {
					d._skip_face = true;
					container.find("#skip-face-check").prop("checked", true);
				}
				container.find("#skip-face-label").css("display", d._source_sn ? "none" : "inline-flex");
			})
			.on("change.bulk", "#skip-face-check", function() {
				d._skip_face = this.checked;
			})
			.on("change.bulk", ".privilege-sel", function() {
				m.privilege[parseInt($(this).attr("data-idx"))] = $(this).val() || "0";
			})
			.on("change.bulk", ".bulk-device-header-check", function(e) {
				e.stopPropagation();
				const sn = String($(this).attr("data-sn"));
				const turned_on = this.checked;
				const cur = new Set(d._selected_sns || []);
				if (turned_on) cur.add(sn); else cur.delete(sn);
				d._selected_sns = Array.from(cur);

				const has = m.has[sn], locked = m.locked[sn], checked = m.checked[sn];
				if (has) {
					for (let i = 0; i < n; i++) {
						const st = cell_state(!!has[i], turned_on);
						locked[i] = st.locked ? 1 : 0;
						checked[i] = st.checked ? 1 : 0;
					}
				}
				draw(true);
				update_count();
			});

		if (show_skip_name) {
			container.on("click.bulk", "#skip-names-btn", function() {
				const turn_on = m.skip.some(v => !v);
				m.skip.fill(turn_on ? 1 : 0);
				$(this).toggleClass("btn-primary btn-default");
				draw(true);
			});
		}

		scroller.addEventListener("scroll", () => draw(false), { passive: true });
		draw(true);
		const measured = tbody.querySelector("tr.vr");
		if (measured && measured.offsetHeight && measured.offsetHeight !== row_h) {
			row_h = measured.offsetHeight;
			draw(true);
		}
		update_count();
	}

}
