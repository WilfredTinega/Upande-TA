// Copyright (c) 2026, Upande LTD and contributors

const INACTIVE_STATUSES = ["Left", "Suspended", "Inactive"];

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
	`;
	document.head.appendChild(style);
})();

frappe.ui.form.on("Employee", {
	onload(frm) {
		frm.__upande_ta_prev_status = frm.doc.status;
	},

	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.__upande_ta_prev_status === undefined) {
			frm.__upande_ta_prev_status = frm.doc.status;
		}
		render_device_buttons(frm);
	},

	before_save(frm) {
		frm.__upande_ta_was_new = frm.is_new();

		const prev = frm.__upande_ta_prev_status;
		const next = frm.doc.status;
		frm.__upande_ta_status_transition = null;

		if (!frm.__upande_ta_was_new && prev && next && prev !== next) {
			const went_inactive = INACTIVE_STATUSES.includes(next) && !INACTIVE_STATUSES.includes(prev);
			const came_back     = !INACTIVE_STATUSES.includes(next) && INACTIVE_STATUSES.includes(prev);
			if (went_inactive) frm.__upande_ta_status_transition = "deactivated";
			else if (came_back) frm.__upande_ta_status_transition = "reactivated";
		}
	},

	after_save(frm) {
		const transition = frm.__upande_ta_status_transition;
		frm.__upande_ta_status_transition = null;
		frm.__upande_ta_prev_status = frm.doc.status;

		frm.__upande_ta_addable_devices = null;
		frm.__upande_ta_on_devices      = null;

		if (frm.__upande_ta_was_new) {
			frm.__upande_ta_was_new = false;
			open_device_action(frm, "Add User", { silent_if_empty: true });
		} else if (transition === "deactivated") {
			open_device_action(frm, "Delete User");
		} else if (transition === "reactivated") {
			open_device_action(frm, "Add User", { silent_if_empty: true });
		}

		render_device_buttons(frm);
	},
});

// Fetch both device lists, compute which actions are available, render only those buttons.
function render_device_buttons(frm) {
	const pin = (frm.doc.attendance_device_id || "").trim();

	frm.remove_custom_button(__("Add to Device"),    __("Biometric Device"));
	frm.remove_custom_button(__("Update on Device"), __("Biometric Device"));

	if (!pin) return;
	if (frm.doc.status !== "Active") return;

	Promise.all([
		new Promise(resolve => frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_devices",
			callback: r => resolve((r && r.message) || []),
			error:    ()  => resolve([]),
		})),
		new Promise(resolve => frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employee_devices",
			args: { employee: frm.doc.name },
			callback: r => resolve(((r && r.message) || {}).devices || []),
			error:    ()  => resolve([]),
		})),
	]).then(([all_devices, on_devices]) => {
		const on_sns = new Set((on_devices || []).map(d => d.device_sn));
		const addable_devices = (all_devices || []).filter(d => !on_sns.has(d.device_sn));

		frm.__upande_ta_addable_devices = addable_devices;
		frm.__upande_ta_on_devices      = on_devices;

		if (addable_devices.length) {
			frm.add_custom_button(__("Add to Device"), () => {
				open_device_action(frm, "Add User");
			}, __("Biometric Device"));
		}

		if (on_devices.length) {
			frm.add_custom_button(__("Update on Device"), () => {
				open_device_action(frm, "Update User");
			}, __("Biometric Device"));
		}
	});
}

// command_type: "Add User" | "Update User" | "Delete User"
// opts.silent_if_empty: skip dialog when no devices are available (used by after_save)
function open_device_action(frm, command_type, opts) {
	opts = opts || {};

	const pin = (frm.doc.attendance_device_id || "").trim();
	if (!pin) {
		if (!opts.silent_if_empty) {
			frappe.msgprint(__("This employee has no Attendance Device ID."));
		}
		return;
	}

	// Add → only devices the employee is NOT yet on.
	// Update/Delete → only devices the employee IS currently on.
	if (command_type === "Add User") {
		fetch_addable_devices(frm).then(devices => {
			if (!devices.length) {
				if (!opts.silent_if_empty) {
					frappe.msgprint(__("This employee is already on every configured device."));
				}
				return;
			}
			load_template_pins_then_show(frm, command_type, pin, devices);
		});
	} else {
		fetch_on_devices(frm).then(devices => {
			if (!devices.length) {
				frappe.msgprint(__("This employee is not registered on any device."));
				return;
			}
			load_template_pins_then_show(frm, command_type, pin, devices);
		});
	}
}

function fetch_addable_devices(frm) {
	if (frm.__upande_ta_addable_devices) {
		return Promise.resolve(frm.__upande_ta_addable_devices);
	}
	return Promise.all([
		new Promise(resolve => frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_devices",
			callback: r => resolve((r && r.message) || []),
			error:    ()  => resolve([]),
		})),
		new Promise(resolve => frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employee_devices",
			args: { employee: frm.doc.name },
			callback: r => resolve(((r && r.message) || {}).devices || []),
			error:    ()  => resolve([]),
		})),
	]).then(([all_devices, on_devices]) => {
		const on_sns = new Set((on_devices || []).map(d => d.device_sn));
		return (all_devices || []).filter(d => !on_sns.has(d.device_sn));
	});
}

function fetch_on_devices(frm) {
	if (frm.__upande_ta_on_devices) {
		return Promise.resolve(frm.__upande_ta_on_devices);
	}
	return new Promise(resolve => frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_employee_devices",
		args: { employee: frm.doc.name },
		callback: r => resolve(((r && r.message) || {}).devices || []),
		error:    ()  => resolve([]),
	}));
}

function load_template_pins_then_show(frm, command_type, pin, devices) {
	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_setting.biometric_setting.get_templated_pins_per_device",
		callback(tr) {
			const data = (tr && tr.message) || { devices: [], pins_by_device: {} };
			const template_devices = data.devices || [];
			const pins_by_device = {};
			for (const sn_key in (data.pins_by_device || {})) {
				pins_by_device[sn_key] = new Set(data.pins_by_device[sn_key] || []);
			}
			show_dialog(frm, command_type, pin, devices, template_devices, pins_by_device);
		},
		error() {
			show_dialog(frm, command_type, pin, devices, [], {});
		},
	});
}

function show_dialog(frm, command_type, pin, devices, all_template_devices, pins_by_device) {
	show_multi_device_dialog(frm, command_type, pin, devices, all_template_devices, pins_by_device);
}

// Multi-select devices. Add/Update show per-device Skip and Privilege. Delete does not.
function show_multi_device_dialog(frm, command_type, pin, devices, all_template_devices, pins_by_device) {
	const full_name = (frm.doc.employee_name || frm.doc.name || "").trim();
	const show_controls = command_type === "Add User" || command_type === "Update User";

	const title = {
		"Add User":    __("Add to Biometric Device(s)"),
		"Update User": __("Update on Biometric Device(s)"),
		"Delete User": __("Delete from Biometric Device(s)"),
	}[command_type];

	const primary_label = {
		"Add User":    __("Add Selected"),
		"Update User": __("Update Selected"),
		"Delete User": __("Delete Selected"),
	}[command_type];

	const intro_verb = {
		"Add User":    __("Add"),
		"Update User": __("Update"),
		"Delete User": __("Delete"),
	}[command_type];

	const intro_target = {
		"Add User":    __("to one or more devices"),
		"Update User": __("on one or more devices"),
		"Delete User": __("from one or more devices"),
	}[command_type];

	const intro_tail = show_controls
		? __("Set Skip and Privilege per device.")
		: "";

	const d = new frappe.ui.Dialog({
		title: title,
		size: "large",
		fields: [
			{
				fieldname: "intro_html",
				fieldtype: "HTML",
				options: `<p style="margin-bottom:8px">
					${intro_verb} <b>${frappe.utils.escape_html(full_name)}</b>
					(PIN <code>${frappe.utils.escape_html(pin)}</code>)
					${intro_target}. ${intro_tail}
				</p>`,
			},
			{
				fieldname: "device_table_html",
				fieldtype: "HTML",
				options: `<div id="emp-multi-device-table" style="margin-top:8px"></div>`,
			},
		],
		primary_action_label: primary_label,
		primary_action() {
			const $container = d.$wrapper.find("#emp-multi-device-table");
			const $rows = $container.find(".device-row.is-checked");
			if (!$rows.length) {
				frappe.msgprint(__("Pick at least one device."));
				return;
			}

			const per_device = [];
			$rows.each(function () {
				const $row = $(this);
				per_device.push({
					device_sn: $row.data("sn"),
					privilege: show_controls ? ($row.find(".privilege-sel").val() || "0") : "0",
					skip_name: show_controls && $row.find(".skip-name-check").is(":checked") ? 1 : 0,
				});
			});

			run_multi_device_command(frm, command_type, pin, per_device, d);
		},
		secondary_action_label: __("Cancel"),
		secondary_action() { d.hide(); },
	});

	d.show();
	render_multi_device_table(d, command_type, pin, devices, all_template_devices, pins_by_device);
}

function render_multi_device_table(d, command_type, pin, devices, all_template_devices, pins_by_device) {
	const $container = d.$wrapper.find("#emp-multi-device-table");
	const show_controls = command_type === "Add User" || command_type === "Update User";

	const other_devices = (all_template_devices || []).filter(dev =>
		!devices.some(target => target.device_sn === dev.device_sn)
	);

	const other_device_cols = other_devices.map(dev =>
		`<th style="width:110px;text-align:center"
			title="${frappe.utils.escape_html(dev.device_sn)}">
			${frappe.utils.escape_html(dev.device_location || dev.device_sn)}
		</th>`
	).join("");

	const skip_col = show_controls
		? `<th style="width:90px;text-align:center">Skip?</th>` : "";
	const privilege_col = show_controls
		? `<th style="width:120px">Privilege</th>` : "";

	const rows = devices.map(dev => {
		const other_cells = other_devices.map(o => {
			const pins = pins_by_device[o.device_sn] || new Set();
			const has = pins.has(pin);
			return `<td style="text-align:center">${
				has
					? `<span style="color:var(--green-500)">✓</span>`
					: `<span style="color:var(--text-muted)">—</span>`
			}</td>`;
		}).join("");

		const skip_cell = show_controls ? `
			<td style="text-align:center">
				<input type="checkbox" class="skip-name-check">
			</td>` : "";

		const privilege_cell = show_controls ? `
			<td>
				<select class="form-control form-control-sm privilege-sel"
						style="width:100px">
					<option value="0" selected>User</option>
					<option value="14">Admin</option>
				</select>
			</td>` : "";

		return `
			<tr class="device-row" data-sn="${frappe.utils.escape_html(dev.device_sn)}">
				<td style="width:40px;text-align:center">
					<input type="checkbox" class="device-check">
				</td>
				<td style="width:180px;font-family:var(--font-mono);font-size:13px">
					${frappe.utils.escape_html(dev.device_sn)}
				</td>
				<td>${frappe.utils.escape_html(dev.device_location || "")}</td>
				${skip_cell}
				${privilege_cell}
				${other_cells}
			</tr>`;
	}).join("");

	const skip_toggle = show_controls
		? `<button class="btn btn-xs btn-default" id="emp-skip-names-btn"
				title="Toggle Skip">Skip</button>` : "";

	$container.html(`
		<div style="margin-bottom:8px;display:flex;gap:8px;align-items:center">
			<button class="btn btn-xs btn-default" id="emp-select-all-btn">Select All</button>
			<button class="btn btn-xs btn-default" id="emp-deselect-all-btn">Deselect All</button>
			${skip_toggle}
			<span style="font-size:12px;color:var(--color-text-secondary)"
				  id="emp-selected-count">0 / ${devices.length}</span>
		</div>
		<div style="max-height:400px;overflow-y:auto;
			border:1px solid var(--color-border-tertiary);border-radius:8px">
			<table class="table table-sm sticky-head-table" style="margin:0">
				<thead>
					<tr>
						<th style="width:40px"></th>
						<th style="width:180px">Device SN</th>
						<th>Location</th>
						${skip_col}
						${privilege_col}
						${other_device_cols}
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
	`);

	function update_count() {
		const $boxes = $container.find(".device-check");
		const n = $boxes.filter(":checked").length;
		$container.find("#emp-selected-count").text(`${n} / ${devices.length}`);
		$container.find(".device-row").each(function () {
			const $row = $(this);
			$row.toggleClass("is-checked", $row.find(".device-check").is(":checked"));
		});
	}

	$container.find("#emp-select-all-btn").on("click", () => {
		$container.find(".device-check").prop("checked", true);
		update_count();
	});
	$container.find("#emp-deselect-all-btn").on("click", () => {
		$container.find(".device-check").prop("checked", false);
		update_count();
	});
	$container.find(".device-check").on("change", update_count);

	if (show_controls) {
		$container.find("#emp-skip-names-btn").on("click", () => {
			const $boxes = $container.find(".skip-name-check");
			const turn_on = $boxes.filter(":checked").length < $boxes.length;
			$boxes.prop("checked", turn_on);
		});
	}
}

// Dispatch one bulk_command per device with that device's own privilege + skip_name.
function run_multi_device_command(frm, command_type, pin, per_device, dialog) {
	const full_name = (frm.doc.employee_name || frm.doc.name || "").trim();
	const verb = {
		"Add User":    __("Add"),
		"Update User": __("Update"),
		"Delete User": __("Delete"),
	}[command_type];

	let queued_total = 0;
	let failed_total = 0;
	let remaining = per_device.length;

	per_device.forEach(spec => {
		const user_payload = [{
			user_id:       pin,
			employee_name: full_name,
			privilege:     spec.privilege || "0",
			skip_name:     spec.skip_name ? 1 : 0,
		}];

		frappe.call({
			method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.bulk_command",
			args: {
				device_sn:    spec.device_sn,
				users:        JSON.stringify(user_payload),
				command_type: command_type,
			},
			callback(rr) {
				if (!rr.exc && rr.message) {
					queued_total += rr.message.queued || 0;
					failed_total += rr.message.failed || 0;
				} else {
					failed_total += 1;
				}
				remaining -= 1;
				if (remaining === 0) {
					dialog.hide();
					frappe.show_alert({
						message: __("{0} queued on {1} device(s).", [verb, per_device.length])
							+ ` (${queued_total} command(s)${failed_total ? `, ${failed_total} failed` : ""})`,
						indicator: failed_total ? "orange" : "green",
					}, 6);
					frm.__upande_ta_addable_devices = null;
					frm.__upande_ta_on_devices      = null;
					render_device_buttons(frm);
				}
			},
		});
	});
}

