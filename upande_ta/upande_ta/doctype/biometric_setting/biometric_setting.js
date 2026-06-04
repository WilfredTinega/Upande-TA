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
		make_primary(frm, "get_bio");
		refresh_device_options(frm);
		backfill_poll_device_sns(frm);
		render_users_tab(frm);
		render_biodata_tab(frm);
		render_scheduled_job_links(frm);
		guard_devices_delete(frm);
		paint_device_status(frm);
		subscribe_device_status(frm);
		add_device_refresh_button(frm);
	},

	devices_on_form_rendered: function(frm) {
		paint_device_status(frm);
		add_device_refresh_button(frm);
	},

	devices_add: function(frm, cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		if (row && !row.status) row.status = "Offline";
		add_device_refresh_button(frm);
		setTimeout(() => paint_device_status(frm), 0);
		setTimeout(() => paint_device_status(frm), 150);
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
	checkin_event_frequency:  autosave_on_change,
	biodata_event_frequency:  autosave_on_change,
	flip_event_frequency:     autosave_on_change,
	checkin_cron_format:      autosave_on_change,
	biodata_cron_format:      autosave_on_change,
	flip_cron_format:         autosave_on_change,

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
		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.hydrate_users_from_templates",
			args: { device_sn: sn },
			callback: (r) => {
				if (r.exc || !r.message) return;
				const m = r.message;
				if (m.reason) {
					frappe.show_alert({
						message: __("No template for this device, select another to sync."),
						indicator: "orange"
					}, 5);
					return;
				}
				frappe.show_alert({
					message: __("Synced {0} user(s); skipped {1}.", [m.created, m.skipped]),
					indicator: m.created ? "green" : "blue"
				}, 5);
				render_users_tab(frm);
			}
		});
	});

	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_device_users",
		args: { device_sn: sn },
		callback: (r) => render_user_list(wrapper, sn, r.message || [], frm)
	});
}

function render_user_list(wrapper, device_sn, users, frm) {
	const container = wrapper.find("#users-table-container");

	if (!users.length) {
		container.html(`<p style="color:var(--text-muted);padding:8px 0">
			No users enrolled on this device yet. Use Bulk Add to enroll employees.
		</p>`);
		return;
	}

	const rows = users.map(u => `
		<tr>
			<td style="font-family:var(--font-mono);font-size:13px">${frappe.utils.escape_html(u.user_id || "")}</td>
			<td>${frappe.utils.escape_html(u.employee_name || "")}</td>
			<td>${u.privilege === "14" ? "Admin" : "User"}</td>
			<td>
				<span style="font-size:11px;padding:2px 8px;border-radius:4px;
					background:var(--bg-light-gray);color:var(--text-color)">
					${frappe.utils.escape_html(u.status || "")}
				</span>
			</td>
			<td style="white-space:nowrap">
				<button class="btn btn-xs btn-primary user-row-delete" data-row="${u.row_name}">Delete</button>
			</td>
		</tr>
	`).join("");

	container.html(`
		<div style="border:1px solid var(--border-color);border-radius:8px;overflow:hidden">
			<table class="table table-sm" style="margin:0">
				<thead style="background:var(--bg-light-gray)">
					<tr>
						<th style="width:130px">PIN</th>
						<th>Name</th>
						<th style="width:100px">Privilege</th>
						<th style="width:120px">Status</th>
						<th style="width:170px">Actions</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
	`);

	container.find(".user-row-delete").on("click", function() {
		if (!frm.doc.enable_users) {
			frappe.msgprint(__("Enable Users"));
			return;
		}
		const row_name = $(this).data("row");
		const u = users.find(x => x.row_name === row_name);
		frappe.confirm(`Delete ${u.employee_name} (PIN ${u.user_id}) from device?`, () => {
			send_single_user_command(device_sn, row_name, "Delete User", users, frm);
		});
	});
}

function send_single_user_command(device_sn, row_name, command_type, users, frm) {
	const u = users.find(x => x.row_name === row_name);
	if (!u) return;

	run_with_progress(
		__(command_type),
		__("Sending {0} command for {1}...", [command_type, u.employee_name]),
		{
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.send_device_command",
			args: {
				name:         row_name,
				command_type: command_type,
				override:     { user_id: u.user_id, employee_name: u.employee_name, privilege: u.privilege }
			},
			callback: (r) => {
				if (!r.exc) {
					frappe.show_alert({
						message: `${command_type} sent for ${u.employee_name}`,
						indicator: command_type === "Delete User" ? "red" : "blue"
					}, 5);
					render_users_tab(frm);
				}
			}
		}
	);
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
	flip:    "flip_event_frequency"
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
	device_sn: function(frm) { refresh_device_options(frm); },
	device_location: function(frm) { refresh_device_options(frm); },
	devices_remove: function(frm) { refresh_device_options(frm); }
});

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
					if (frm_) f.custom_farm = frm_;
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

			frappe.confirm(label, () => {
				run_with_progress(
					__("{0} ({1} picks)", [command_type, total_picks]),
					__("Queuing {0} commands...", [command_type]),
					{
						method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.bulk_command_per_device",
						args: {
							assignments: JSON.stringify(assignments),
							command_type: command_type
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
		const container = d.$wrapper.find("#bulk-user-table");
		const by_sn = {};
		(d._selected_sns || []).forEach(sn => { by_sn[sn] = []; });

		container.find(".bulk-device-cell-check:checked").each(function() {
			if (String($(this).data("locked")) === "1") return;
			const sn  = $(this).data("sn");
			const idx = parseInt($(this).data("idx"));
			if (!by_sn[sn]) return;
			const user = (d._bulk_users_data || [])[idx];
			if (!user) return;

			const priv = container.find(`.privilege-sel[data-idx="${idx}"]`).val()
				|| user.privilege || "0";
			const skip_name = container.find(`.skip-name-check[data-idx="${idx}"]`).is(":checked");
			by_sn[sn].push({
				user_id:       user.user_id,
				employee_name: user.employee_name,
				privilege:     priv,
				row_name:      user.row_name || null,
				skip_name:     skip_name ? 1 : 0
			});
		});

		return Object.keys(by_sn)
			.filter(sn => by_sn[sn].length)
			.map(sn => ({ device_sn: sn, users: by_sn[sn] }));
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

	function reload_users() {
		load_users();
	}

	if (any_filter_enabled) refresh_filter_options();

	function refresh_filter_options() {
		let department  = d.get_value("filter_department")  || null;
		let designation = d.get_value("filter_designation") || null;
		let company     = d.get_value("filter_company")     || null;
		let farm        = d.get_value("filter_farm")        || null;
		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_active_filter_options",
			args: { department, designation, company, farm },
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
		return {
			employee:    d.get_value("filter_employee")    || null,
			designation: d.get_value("filter_designation") || null,
			department:  d.get_value("filter_department")  || null,
			company:     d.get_value("filter_company")     || null,
			farm:        d.get_value("filter_farm")        || null
		};
	}

	function load_users() {
		let container = d.$wrapper.find("#bulk-user-table");
		container.html(`<p style="color:var(--color-text-secondary)">Loading...</p>`);

		let filters = get_filter_args();
		const all_sns = (d._devices || []).map(dev => dev.device_sn);

		const proceed = () => _load_users_inner(all_sns, filters, container);

		if (d._template_devices) {
			proceed();
			return;
		}
		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.get_templated_pins_per_device",
			callback(tr) {
				const data = (tr && tr.message) || { devices: [], pins_by_device: {} };
				d._template_devices = data.devices || [];
				d._template_pins_by_device = {};
				for (const sn_key in (data.pins_by_device || {})) {
					d._template_pins_by_device[sn_key] = new Set(data.pins_by_device[sn_key] || []);
				}
				proceed();
			},
			error() {
				d._template_devices = [];
				d._template_pins_by_device = {};
				proceed();
			}
		});
	}

	function _load_users_inner(sns, filters, container) {
		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_device_users_multi",
			args: { device_sns: JSON.stringify(sns) },
			callback(r) {
				let payload = (r && r.message) || { users: [], pins_by_device: {} };
				let device_users = payload.users || [];
				d._device_pins = {};
				for (const sn_key in (payload.pins_by_device || {})) {
					d._device_pins[sn_key] = new Set(payload.pins_by_device[sn_key] || []);
				}
				let has_filters = filters.employee || filters.designation || filters.department
					|| filters.company || filters.farm;

				if (command_type === "Add User" || command_type === "Poll BioData") {
					frappe.call({
						method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employees",
						args: Object.assign({ status: "Active" }, filters),
						callback(er) {
							let employees = er.message || [];
							render_table(employees.map(e => ({
								user_id:       e.user_id,
								employee_name: e.full_name,
								privilege:     "0"
							})), command_type);
						}
					});

				} else if (command_type === "Delete User" || command_type === "Update User") {
					if (!has_filters) {
						render_table(device_users, command_type);
						return;
					}
					frappe.call({
						method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employees",
						args: Object.assign({ status: "Active" }, filters),
						callback(er) {
							let allowed_pins = new Set((er.message || []).map(e => e.user_id));
							let matching     = device_users.filter(u => allowed_pins.has(u.user_id));
							render_table(matching, command_type);
						}
					});
				}
			}
		});
	}

	function render_table(users, action) {
		let container = d.$wrapper.find("#bulk-user-table");

		if (!users.length) {
			container.html(`<p style="color:var(--color-text-secondary);padding:8px 0">
				No users found for this action on the selected device.
			</p>`);
			return;
		}

		let show_privilege = action === "Add User" || action === "Update User";
		let show_skip_name = action === "Add User" || action === "Update User";
		let skip_name_col  = show_skip_name ? `<th class="bulk-col-skip" style="text-align:center;white-space:nowrap">Skip?</th>` : "";
		let privilege_col  = show_privilege ? `<th class="bulk-col-priv" style="white-space:nowrap">Privilege</th>` : "";
		const all_devices  = d._devices || [];
		const selected_sn_set = new Set(d._selected_sns || []);
		let device_pins    = d._device_pins || {};

		let device_cols = all_devices.map(dev => {
			const checked = selected_sn_set.has(dev.device_sn) ? "checked" : "";
			const label = dev.device_location || dev.device_sn;
			return `<th style="min-width:120px;text-align:center;white-space:nowrap;padding:6px 10px"
				title="${frappe.utils.escape_html(label)} (${frappe.utils.escape_html(dev.device_sn)})">
				<label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-weight:600;margin:0;white-space:nowrap">
					<input type="checkbox" class="bulk-device-header-check"
						data-sn="${frappe.utils.escape_html(dev.device_sn)}" ${checked}
						style="margin:0;flex:0 0 auto">
					<span style="white-space:nowrap">${frappe.utils.escape_html(label)}</span>
				</label>
			</th>`;
		}).join("");

		let rows = users.map((u, i) => {
			let skip_name_cell = show_skip_name ? `
				<td class="bulk-col-skip" style="text-align:center;white-space:nowrap">
					<input type="checkbox" class="skip-name-check" data-idx="${i}">
				</td>` : "";

			let privilege_cell = show_privilege ? `
				<td class="bulk-col-priv" style="white-space:nowrap">
					<select class="form-control form-control-sm privilege-sel"
							data-idx="${i}" style="width:100px">
						<option value="0"  ${u.privilege === "0"  ? "selected" : ""}>User</option>
						<option value="14" ${u.privilege === "14" ? "selected" : ""}>Admin</option>
					</select>
				</td>` : "";

			let device_cells = all_devices.map(dev => {
				const pins = device_pins[dev.device_sn] || new Set();
				const has = pins.has(u.user_id);
				const is_target = selected_sn_set.has(dev.device_sn);
				const presence = has
					? `<span style="color:var(--green-500)">✓</span>`
					: `<span style="color:var(--text-muted)">—</span>`;
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
				const visible_checked = locked
					? has
					: (is_target && default_check);
				return `<td style="min-width:120px;text-align:center"
					data-sn="${frappe.utils.escape_html(dev.device_sn)}">
					<span class="bulk-device-presence" style="display:${is_target ? "none" : "inline"}">${presence}</span>
					<input type="checkbox" class="bulk-device-cell-check"
						data-sn="${frappe.utils.escape_html(dev.device_sn)}"
						data-idx="${i}"
						data-has="${has ? 1 : 0}"
						data-locked="${locked ? 1 : 0}"
						title="${locked ? frappe.utils.escape_html(action === "Add User" ? "Already enrolled on this device" : "Not enrolled on this device") : ""}"
						style="display:${is_target ? "inline-block" : "none"};margin:0${locked ? ";opacity:0.6;cursor:not-allowed" : ""}"
						${visible_checked ? "checked" : ""}
						${locked ? "disabled" : ""}>
				</td>`;
			}).join("");

			let status_badge = u.status ? `
				<span style="font-size:11px;padding:2px 6px;border-radius:4px;
					background:var(--color-background-success);
					color:var(--color-text-success)">
					${u.status}
				</span>` : "";

			return `
				<tr data-idx="${i}">
					<td class="bulk-col-pin" style="width:90px;font-family:var(--font-mono);font-size:13px">
						${frappe.utils.escape_html(u.user_id || "")}
					</td>
					<td class="bulk-col-name" style="width:220px">
						${frappe.utils.escape_html(u.employee_name || "")}
						${status_badge}
					</td>
					${skip_name_cell}
					${privilege_cell}
					${device_cells}
				</tr>`;
		}).join("");

		let skip_names_toggle = show_skip_name ? `
			<button class="btn btn-xs btn-default" id="skip-names-btn"
					title="Toggle Skip for all rows">Skip</button>` : "";

		container.html(`
			<div style="margin-bottom:8px;display:flex;gap:8px;align-items:center">
				<button class="btn btn-xs btn-default" id="select-all-btn"
					title="Tick every cell in currently-selected device columns">Select All in Targets</button>
				<button class="btn btn-xs btn-default" id="deselect-all-btn">Deselect All</button>
				${skip_names_toggle}
				<span style="font-size:12px;color:var(--color-text-secondary)"
					  id="selected-count">0 picks</span>
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
					<tbody>${rows}</tbody>
				</table>
			</div>
		`);

		container.find("#select-all-btn").on("click", () => {
			container.find(".bulk-device-cell-check:visible").prop("checked", true);
			update_count();
		});
		container.find("#deselect-all-btn").on("click", () => {
			container.find(".bulk-device-cell-check").prop("checked", false);
			update_count();
		});
		container.find(".bulk-device-cell-check").on("change", update_count);

		container.find(".bulk-device-header-check").on("change", function(e) {
			e.stopPropagation();
			const sn = $(this).data("sn");
			const turned_on = this.checked;
			const cur = new Set(d._selected_sns || []);
			if (turned_on) cur.add(sn); else cur.delete(sn);
			d._selected_sns = Array.from(cur);

			const sn_attr = $.escapeSelector ? $.escapeSelector(sn) : sn;
			const $cells = container.find(`td[data-sn="${sn_attr}"]`);
			$cells.find(".bulk-device-presence").css("display", turned_on ? "none" : "inline");
			$cells.find(".bulk-device-cell-check").each(function() {
				const has = String($(this).data("has")) === "1";
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
				const checked_state = locked ? (turned_on && has) : (turned_on && default_check);
				$(this).css("display", turned_on ? "inline-block" : "none")
					.prop("disabled", locked)
					.prop("checked", checked_state);
			});
			update_count();
		});

		let active_filters = get_filter_args();
		if (active_filters.employee || active_filters.designation || active_filters.department
			|| active_filters.company || active_filters.farm) {
			container.find(".bulk-device-cell-check:visible").prop("checked", true);
		}
		update_count();

		if (show_skip_name) {
			container.find("#skip-names-btn").on("click", function() {
				const $btn = $(this);
				const turn_on = container.find(".skip-name-check:checked").length
								< container.find(".skip-name-check").length;
				container.find(".skip-name-check").prop("checked", turn_on);
				$btn.toggleClass("btn-primary btn-default");
			});
		}

		function update_count() {
			let n = container.find('.bulk-device-cell-check:checked').filter(function() {
				return String($(this).data("locked")) !== "1";
			}).length;
			container.find("#selected-count").text(`${n} pick(s)`);
		}

		d._bulk_users_data = users;
	}

}
