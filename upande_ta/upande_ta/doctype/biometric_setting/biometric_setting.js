// Copyright (c) 2026, Upande LTD and contributors

frappe.ui.form.on("Biometric Setting", {
	refresh: function(frm) {
		make_primary(frm, "get_checkin");
		make_primary(frm, "get_bio");
		refresh_device_options(frm);
		render_users_tab(frm);
		render_biodata_tab(frm);
		render_scheduled_job_links(frm);
		guard_devices_delete(frm);
	},

	users_device_picker: function(frm) {
		const match = (frm.doc.devices || []).find(d => d.device_sn === frm.doc.users_device_picker);
		frm.set_value("device_location", match ? (match.device_location || "") : "");
		render_users_tab(frm);
	},

	biodata_device_picker: function(frm) {
		const match = (frm.doc.devices || []).find(d => d.device_sn === frm.doc.biodata_device_picker);
		frm.set_value("biodata_device_location", match ? (match.device_location || "") : "");
		render_biodata_tab(frm);
	},

	enable_checkin:           autosave_on_change,
	enable_users:             autosave_on_change,
	enable_bio_templates:     autosave_on_change,
	enable_cleanup:           autosave_on_change,
	enable_flip:              autosave_on_change,
	checkin_event_frequency:  autosave_on_change,
	users_event_frequency:    autosave_on_change,
	biodata_event_frequency:  autosave_on_change,
	cleanup_event_frequency:  autosave_on_change,
	flip_event_frequency:     autosave_on_change,
	checkin_cron_format:      autosave_on_change,
	users_cron_format:        autosave_on_change,
	biodata_cron_format:      autosave_on_change,
	cleanup_cron_format:      autosave_on_change,
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
		const sn = frm.doc.biodata_device_picker;
		if (!sn) {
			frappe.msgprint("Pick a device above first.");
			return;
		}

		const open_dialog = () => {
			open_bulk_user_dialog(
				"Poll BioData",
				sn,
				frm.doc.biodata_device_location || sn,
				() => render_biodata_tab(frm)
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
						const lines = blocked_sns.map(sn => {
							const links = blocked[sn].map(name => {
								const safe = frappe.utils.escape_html(name);
								const href = `/app/biometric-template/${encodeURIComponent(name)}`;
								return `<a href="${href}" target="_blank">${safe}</a>`;
							}).join(", ");
							return `<li><b>${frappe.utils.escape_html(sn)}</b> → ${blocked[sn].length} template(s): ${links}</li>`;
						}).join("");
						frappe.msgprint({
							title: __("Cannot delete device(s)"),
							indicator: "red",
							message: __("The following device(s) have Biometric Template records. Delete the template(s) first:") +
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

function refresh_device_options(frm) {
	const opts = (frm.doc.devices || [])
		.map(d => d.device_sn)
		.filter(sn => sn);
	const opts_str = "\n" + opts.join("\n");

	frm.set_df_property("users_device_picker", "options", opts_str);
	frm.set_df_property("biodata_device_picker", "options", opts_str);

	const grid = frm.fields_dict.poll_devices && frm.fields_dict.poll_devices.grid;
	if (grid) {
		grid.update_docfield_property("device", "options", opts_str);
		grid.refresh();
	}
}

function render_users_tab(frm) {
	const wrapper = frm.fields_dict.users_html && frm.fields_dict.users_html.$wrapper;
	if (!wrapper) return;

	const sn = frm.doc.users_device_picker;
	if (!sn) {
		wrapper.html(`<div style="padding:20px;color:var(--text-muted)">
			Pick a device above to view and manage its users.
		</div>`);
		return;
	}

	const device_match = (frm.doc.devices || []).find(d => d.device_sn === sn);
	const loc = (device_match && device_match.device_location) || sn;

	wrapper.html(`
		<div style="display:flex;gap:8px;margin:12px 0;flex-wrap:wrap">
			<button class="btn btn-sm btn-primary" id="btn-bulk-add">Add</button>
			<button class="btn btn-sm btn-primary" id="btn-bulk-delete">Delete</button>
			<button class="btn btn-sm btn-primary" id="btn-hydrate-templates">
				Sync
			</button>
		</div>
		<div id="users-table-container">
			<p style="color:var(--text-muted)">Loading users on ${frappe.utils.escape_html(loc)}...</p>
		</div>
	`);

	const open_bulk = (cmd) => {
		if (!frm.doc.enable_users) {
			frappe.msgprint(__("Enable Users"));
			return;
		}
		open_bulk_user_dialog(cmd, sn, loc, () => render_users_tab(frm));
	};

	wrapper.find("#btn-bulk-add").on("click", () => open_bulk("Add User"));
	wrapper.find("#btn-bulk-delete").on("click", () => open_bulk("Delete User"));
	wrapper.find("#btn-hydrate-templates").on("click", () => {
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

	const sn = frm.doc.biodata_device_picker;
	if (!sn) {
		wrapper.html(`<div style="padding:20px;color:var(--text-muted)">
			Pick a device above to view its biometric templates.
		</div>`);
		return;
	}

	const device_match = (frm.doc.devices || []).find(d => d.device_sn === sn);
	const loc = (device_match && device_match.device_location) || sn;

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
	users:   "users_event_frequency",
	biodata: "biodata_event_frequency",
	cleanup: "cleanup_event_frequency",
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
	device: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const match = (frm.doc.devices || []).find(d => d.device_sn === row.device);
		frappe.model.set_value(cdt, cdn, "device_name", match ? (match.device_location || "") : "");
	}
});

function open_bulk_user_dialog(command_type, default_sn, default_location, on_success) {
	let dialog_title = {
		"Add User":    "Bulk Add Users to Device",
		"Delete User": "Bulk Delete Users from Device",
		"Poll BioData": "Poll BioData from Device"
	}[command_type];

	let indicator = {
		"Add User":    "green",
		"Delete User": "red",
		"Poll BioData": "blue"
	}[command_type];

	const is_poll = command_type === "Poll BioData";

	let d = new frappe.ui.Dialog({
		title: dialog_title,
		size: "extra-large",
		fields: [
			{
				fieldname: "device_sn",
				fieldtype: "Select",
				label: "Target Device",
				reqd: 1,
				change() {
					let raw = d.get_value("device_sn");
					if (raw) load_users(raw.split(" — ")[0].trim());
				}
			},
			{ fieldname: "col_break_filter_0", fieldtype: "Column Break" },
			{
				fieldname: "filter_department",
				fieldtype: "Autocomplete",
				label: "Department",
				options: [],
				columns: 2,
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
				get_query() {
					let f = { status: "Active" };
					let dept = d.get_value("filter_department");
					let desg = d.get_value("filter_designation");
					if (dept) f.department  = dept;
					if (desg) f.designation = desg;
					return { filters: f };
				},
				change() { reload_with_filters(); }
			},
			{ fieldname: "col_break_filter_3", fieldtype: "Column Break" },
			{
				fieldname: "clear_filters_html",
				fieldtype: "HTML",
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
			let checked = get_checked_users();
			if (!checked.length) {
				frappe.msgprint("Select at least one user.");
				return;
			}

			let raw = d.get_value("device_sn") || "";
			let sn  = raw.split(" — ")[0].trim();
			let loc = raw.split(" — ")[1] || sn;

			if (is_poll) {
				const pins = checked.map(u => u.user_id).filter(Boolean);
				if (!pins.length) {
					frappe.msgprint("None of the selected employees have a PIN.");
					return;
				}
				frappe.confirm(`Poll BioData for ${pins.length} user(s) on ${loc}?`, () => {
					run_with_progress(
						__("Polling BioData ({0} users)", [pins.length]),
						__("Queuing biodata poll commands..."),
						{
							method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.request_biodata",
							args: {
								device_sn: sn,
								pins:      JSON.stringify(pins)
							},
							callback(r) {
								if (!r.exc) {
									d.hide();
									if (on_success) on_success();
									const n = (r.message && r.message.queued && r.message.queued.length) || 0;
									frappe.show_alert({
										message: `${n} biodata queries queued for ${pins.length} PIN(s) on ${loc}. Templates will arrive within 30 seconds.`,
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
				? `Delete ${checked.length} user(s) from ${loc}?`
				: `${command_type.split(" ")[0]} ${checked.length} user(s) on ${loc}?`;

			frappe.confirm(label, () => {
				run_with_progress(
					__("{0} ({1} users)", [command_type, checked.length]),
					__("Queuing {0} commands...", [command_type]),
					{
						method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.bulk_command",
						args: {
							device_sn:    sn,
							users:        JSON.stringify(checked),
							command_type: command_type
						},
						callback(r) {
							if (!r.exc) {
								d.hide();
								if (on_success) on_success();
								let msg = `${r.message.queued} command(s) queued successfully.`;
								if (r.message.failed > 0) msg += ` ${r.message.failed} failed.`;
								frappe.show_alert({ message: msg, indicator: indicator }, 8);
							}
						}
					}
				);
			});
		}
	});

	const apply_toolbar_layout = () => {
		const $deviceField = d.$wrapper.find(`[data-fieldname="device_sn"]`).first();
		if (!$deviceField.length) return;
		const $col = $deviceField.closest(".form-column");
		const $row = $col.parent();
		if (!$row.length) return;

		$row.css({
			"display":               "grid",
			"grid-template-columns": "2fr 1fr 1fr 1fr 56px",
			"gap":                   "20px",
			"align-items":           "end",
			"width":                 "100%",
			"padding":               "0",
			"margin":                "0"
		});

		const $cols = $row.children(".form-column");
		$cols.css({
			"padding":     "0",
			"margin":      "0",
			"min-width":   "0",
			"max-width":   "100%",
			"width":       "100%",
			"float":       "none",
			"display":     "block",
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
				let options = r.message.map(dev =>
					`${dev.device_sn} — ${dev.device_location || "No location"}`
				);
				d.set_df_property("device_sn", "options", options);
				if (default_sn && default_location) {
					d.set_value("device_sn", `${default_sn} — ${default_location}`);
					load_users(default_sn);
				}
				d.refresh_field("device_sn");
				apply_toolbar_layout();
			}
		}
	});

	refresh_filter_options();

	function refresh_filter_options() {
		let department  = d.get_value("filter_department")  || null;
		let designation = d.get_value("filter_designation") || null;
		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_active_filter_options",
			args: { department, designation },
			callback(r) {
				let opts = r.message || {};
				let valid_designations = opts.designations || [];
				set_autocomplete_options("filter_designation", valid_designations);
				set_autocomplete_options("filter_department",  opts.departments  || []);
				set_filter_label("filter_department",  "Department",  opts.department_count);
				set_filter_label("filter_designation", "Designation", opts.designation_count);
				set_filter_label("filter_employee",    "Employee",    opts.employee_count);

				if (designation && !valid_designations.includes(designation)) {
					d.set_value("filter_designation", "");
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
		reload_with_filters();
	});

	function reload_with_filters() {
		toggle_clear_btn();
		refresh_filter_options();
		validate_employee_against_cascade();
		let raw = d.get_value("device_sn");
		if (raw) load_users(raw.split(" — ")[0].trim());
	}

	function validate_employee_against_cascade() {
		let emp  = d.get_value("filter_employee");
		let dept = d.get_value("filter_department");
		let desg = d.get_value("filter_designation");
		if (!emp || (!dept && !desg)) return;

		let filters = { name: emp };
		if (dept) filters.department  = dept;
		if (desg) filters.designation = desg;

		frappe.db.get_list("Employee", { filters, limit: 1 }).then(rows => {
			if (!rows || !rows.length) {
				d.set_value("filter_employee", "");
			}
		});
	}

	function toggle_clear_btn() {
		let any = d.get_value("filter_employee")
			   || d.get_value("filter_designation")
			   || d.get_value("filter_department");
		d.$wrapper.find("#clear-filters-btn").css("display", any ? "flex" : "none");
	}

	function get_filter_args() {
		return {
			employee:    d.get_value("filter_employee")    || null,
			designation: d.get_value("filter_designation") || null,
			department:  d.get_value("filter_department")  || null
		};
	}

	function load_users(sn) {
		let container = d.$wrapper.find("#bulk-user-table");
		container.html(`<p style="color:var(--color-text-secondary)">Loading...</p>`);

		let filters = get_filter_args();
		d._current_sn = sn;

		const proceed = () => _load_users_inner(sn, filters, container);

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

	function _load_users_inner(sn, filters, container) {
		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_device_users",
			args: { device_sn: sn },
			callback(r) {
				let device_users = r.message || [];
				let has_filters = filters.employee || filters.designation || filters.department;

				if (command_type === "Add User" || command_type === "Poll BioData") {
					frappe.call({
						method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employees",
						args: Object.assign({ status: "Active" }, filters),
						callback(er) {
							let employees = er.message || [];
							let rows;
							if (command_type === "Add User") {
								let device_pins = new Set(device_users.map(u => u.user_id));
								rows = employees.filter(e => !device_pins.has(e.user_id));
							} else {
								rows = employees;
							}
							render_table(rows.map(e => ({
								user_id:       e.user_id,
								employee_name: e.full_name,
								privilege:     "0"
							})), command_type);
						}
					});

				} else if (command_type === "Delete User") {
					if (!has_filters) {
						render_table(device_users, command_type);
						return;
					}
					frappe.call({
						method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employees",
						args: Object.assign({ status: "Active" }, filters),
						callback(er) {
							let allowed_pins = new Set((er.message || []).map(e => e.user_id));
							let deletable    = device_users.filter(u => allowed_pins.has(u.user_id));
							render_table(deletable, command_type);
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

		let show_privilege = action === "Add User";
		let show_skip_name = action === "Add User";
		let skip_name_col  = show_skip_name ? `<th style="width:90px;text-align:center">Skip?</th>` : "";
		let privilege_col  = show_privilege ? `<th style="width:120px">Privilege</th>` : "";
		let template_devices = d._template_devices || [];
		let pins_by_device   = d._template_pins_by_device || {};
		if (d._current_sn) {
			template_devices = template_devices.filter(dev => dev.device_sn !== d._current_sn);
		}

		let device_cols = template_devices.map(dev =>
			`<th style="width:110px;text-align:center" title="${frappe.utils.escape_html(dev.device_sn)}">
				${frappe.utils.escape_html(dev.device_location || dev.device_sn)}
			</th>`
		).join("");

		let rows = users.map((u, i) => {
			let skip_name_cell = show_skip_name ? `
				<td style="text-align:center">
					<input type="checkbox" class="skip-name-check" data-idx="${i}">
				</td>` : "";

			let privilege_cell = show_privilege ? `
				<td>
					<select class="form-control form-control-sm privilege-sel"
							data-idx="${i}" style="width:100px">
						<option value="0"  ${u.privilege === "0"  ? "selected" : ""}>User</option>
						<option value="14" ${u.privilege === "14" ? "selected" : ""}>Admin</option>
					</select>
				</td>` : "";

			let device_cells = template_devices.map(dev => {
				const pins = pins_by_device[dev.device_sn] || new Set();
				const has = pins.has(u.user_id);
				return `<td style="text-align:center">${has ? `<span style="color:var(--green-500)">✓</span>` : `<span style="color:var(--text-muted)">—</span>`}</td>`;
			}).join("");

			let status_badge = u.status ? `
				<span style="font-size:11px;padding:2px 6px;border-radius:4px;
					background:var(--color-background-success);
					color:var(--color-text-success)">
					${u.status}
				</span>` : "";

			return `
				<tr data-idx="${i}">
					<td style="width:40px;text-align:center">
						<input type="checkbox" class="user-check" data-idx="${i}">
					</td>
					<td style="width:130px;font-family:var(--font-mono);font-size:13px">
						${frappe.utils.escape_html(u.user_id || "")}
					</td>
					<td>
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
				<button class="btn btn-xs btn-default" id="select-all-btn">Select All</button>
				<button class="btn btn-xs btn-default" id="deselect-all-btn">Deselect All</button>
				${skip_names_toggle}
				<span style="font-size:12px;color:var(--color-text-secondary)"
					  id="selected-count">0 / ${users.length}</span>
			</div>
			<div style="max-height:400px;overflow-y:auto;
				border:1px solid var(--color-border-tertiary);border-radius:8px">
				<table class="table table-sm" style="margin:0">
					<thead style="position:sticky;top:0;
						background:var(--color-background-secondary)">
						<tr>
							<th style="width:40px"></th>
							<th style="width:130px">PIN</th>
							<th>Name</th>
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
			container.find(".user-check").prop("checked", true);
			update_count();
		});
		container.find("#deselect-all-btn").on("click", () => {
			container.find(".user-check").prop("checked", false);
			update_count();
		});
		container.find(".user-check").on("change", update_count);

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
			let n = container.find(".user-check:checked").length;
			container.find("#selected-count").text(`${n} / ${users.length}`);
		}

		d._bulk_users_data = users;
	}

	function get_checked_users() {
		let container = d.$wrapper.find("#bulk-user-table");
		let checked   = [];
		container.find(".user-check:checked").each(function() {
			let idx  = parseInt($(this).data("idx"));
			let user = d._bulk_users_data[idx];
			let priv = container.find(`.privilege-sel[data-idx="${idx}"]`).val()
					   || user.privilege || "0";
			let skip_name = container.find(`.skip-name-check[data-idx="${idx}"]`).is(":checked");
			checked.push({
				user_id:       user.user_id,
				employee_name: user.employee_name,
				privilege:     priv,
				row_name:      user.row_name || null,
				skip_name:     skip_name ? 1 : 0
			});
		});
		return checked;
	}
}
