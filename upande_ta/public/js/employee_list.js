// Copyright (c) 2026, Upande Limited and contributors
// For license information, please see license.txt
//
// Bulk enrolment from the Employee list.
//
// The form already has Biometric Device > Add to Device for one person, which
// is fine for a new hire and useless for a new device or a new intake — nobody
// opens two hundred employees one at a time. This puts the same action on a
// selection.
//
// The work is done by biometric_user.bulk_command(device_sn, users, ...), which
// already exists for the device-side screens and carries the farm scoping: a
// device tied to a farm refuses employees from another one, and says so per
// employee rather than failing the batch.
//
// Wrapped in an IIFE: every app's Employee list js is concatenated into one
// blob, so a top-level `const` clashing with another app's — or a name as
// generic as `report` — would be a SyntaxError that kills all of them.

(function () {
	frappe.listview_settings["Employee"] = frappe.listview_settings["Employee"] || {};

	const _upande_ta_prev_onload = frappe.listview_settings["Employee"].onload;

	frappe.listview_settings["Employee"].onload = function (listview) {
		if (_upande_ta_prev_onload) {
			_upande_ta_prev_onload.call(this, listview);
		}

		// Grouped under one "Biometric Device" button, the same name the Employee
		// form uses for the single-employee versions — so the bulk actions are
		// found where somebody already knows to look. Not in the Actions menu:
		// that only appears once rows are ticked, which hides the capability
		// from anyone who does not already know it is there.
		const GROUP = __("Biometric Device");

		const $add = listview.page.add_inner_button(__("Add to Device"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Tick the employees to add, then press Add to Device."));
				return;
			}
			pick_device_then_send(listview, names, "Add User");
		}, GROUP);

		// Which of the two second actions is on offer follows the selection.
		//
		// Removing somebody still working is almost never what is meant — the
		// reason to take a person off a device is that they have left, been
		// suspended or gone inactive. So Remove is offered only when EVERY
		// selected employee is in one of those states; otherwise the useful
		// action is Update, which re-pushes their details and fingerprints.
		//
		// A mixed selection deliberately gets Update rather than both: offering
		// Remove there invites removing the active half by accident.
		const INACTIVE = ["Left", "Inactive", "Suspended"];

		const $update = listview.page.add_inner_button(__("Update on Device"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Tick the employees to update, then press Update on Device."));
				return;
			}
			pick_device_then_send(listview, names, "Update User");
		}, GROUP);

		const $remove = listview.page.add_inner_button(__("Remove from Device"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Tick the employees to remove, then press Remove from Device."));
				return;
			}
			pick_device_then_send(listview, names, "Delete User");
		}, GROUP);

		function sync_device_buttons() {
			const rows = listview.get_checked_items() || [];
			// `status` comes off the row the list already loaded. If the column
			// has been removed from the view it is undefined, and an unknown
			// status must not read as "they have left" — so Update stays.
			const all_gone =
				rows.length > 0 && rows.every((r) => INACTIVE.includes(r.status));

			// Toggle the <a> itself, NOT its parent. add_inner_button appends a
			// grouped item straight into .dropdown-menu with no <li> wrapper, so
			// .parent() is the whole menu — toggling that showed or hid every
			// item at once, and the two calls simply fought each other.
			//
			// Everyone selected has left: Remove is the only sensible action, so
			// Add and Update both go. Putting somebody back onto a device is a
			// re-hire, which happens by setting them Active again — the form
			// offers it at that point.
			if ($add) $add.toggle(!all_gone);
			if ($update) $update.toggle(!all_gone);
			if ($remove) $remove.toggle(!!all_gone);
		}

		// on_row_checked is frappe's own selection signal, fired for a single
		// tick and for select-all alike.
		const prev_on_row_checked = listview.on_row_checked
			? listview.on_row_checked.bind(listview)
			: null;
		listview.on_row_checked = function () {
			if (prev_on_row_checked) prev_on_row_checked();
			sync_device_buttons();
		};

		sync_device_buttons();
	};

	function pick_device_then_send(listview, names, command_type) {
		frappe.dom.freeze(__("Reading employees..."));

		Promise.all([
			new Promise((resolve) =>
				frappe.call({
					method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_devices",
					callback: (r) => resolve((r && r.message) || []),
					error: () => resolve([]),
				})
			),
			new Promise((resolve) =>
				frappe.call({
					method: "frappe.client.get_list",
					args: {
						doctype: "Employee",
						filters: [["name", "in", names]],
						fields: ["name", "employee_name", "attendance_device_id", "custom_farm"],
						limit_page_length: 0,
					},
					callback: (r) => resolve((r && r.message) || []),
					error: () => resolve([]),
				})
			),
		]).then(([devices, employees]) => {
			frappe.dom.unfreeze();

			if (!devices.length) {
				frappe.msgprint(__("No biometric devices are configured."));
				return;
			}

			// Which farms the selection covers. A device tied to farms only
			// accepts employees from them (bulk_command enforces it), so a
			// device matching none of these could never take anybody here.
			// Delete is exempt server-side — removing a stale or foreign
			// enrolment is exactly what an operator needs — so it sees them all.
			const farms = new Set(
				employees.map((e) => e.custom_farm).filter((f) => !!f)
			);
			const usable =
				command_type === "Delete User"
					? devices
					: devices.filter((d) => {
							const df = d.farms || [];
							if (!df.length) return true;            // unrestricted
							return df.some((f) => farms.has(f));
					  });

			if (!usable.length) {
				frappe.msgprint({
					title: __("No device for these employees"),
					message: __(
						"Every configured device is tied to a farm, and none of them covers {0}.",
						[Array.from(farms).join(", ") || __("the selected employees")]
					),
					indicator: "orange",
				});
				return;
			}

			show_bulk_dialog(listview, names, command_type, usable, employees, farms);
		});
	}

	// The same multi-device table the Employee form uses, so the bulk flow is the
	// dialog people already know: pick several devices at once, with Skip and
	// Privilege per device. The single-Select dropdown this replaces could only
	// ever send to one device, which meant repeating the whole run per device.
	function show_bulk_dialog(listview, names, command_type, devices, employees, farms) {
		const show_controls = command_type === "Add User" || command_type === "Update User";

		const title = {
			"Add User": __("Add to Biometric Device(s)"),
			"Update User": __("Update on Biometric Device(s)"),
			"Delete User": __("Delete from Biometric Device(s)"),
		}[command_type];

		const primary_label = {
			"Add User": __("Add Selected"),
			"Update User": __("Update Selected"),
			"Delete User": __("Delete Selected"),
		}[command_type];

		const verb = {
			"Add User": __("Add"),
			"Update User": __("Update"),
			"Delete User": __("Delete"),
		}[command_type];

		const target = {
			"Add User": __("to one or more devices"),
			"Update User": __("on one or more devices"),
			"Delete User": __("from one or more devices"),
		}[command_type];

		const farm_note = show_controls
			? __("Showing devices that cover {0}.", [
					Array.from(farms || []).join(", ") || __("the selected employees"),
			  ])
			: "";

		const d = new frappe.ui.Dialog({
			title,
			size: "large",
			fields: [
				{
					fieldname: "intro_html",
					fieldtype: "HTML",
					options: `<p style="margin-bottom:8px">
						${verb} <b>${employees.length}</b> ${__("employee(s)")} ${target}.
						${show_controls ? __("Set Skip and Privilege per device.") : ""}
						<br><span style="color:var(--text-muted);font-size:var(--text-sm)">${farm_note}</span>
					</p>`,
				},
				{
					fieldname: "device_table_html",
					fieldtype: "HTML",
					options: `<div id="ta-bulk-device-table" style="margin-top:8px"></div>`,
				},
			],
			primary_action_label: primary_label,
			primary_action() {
				const $rows = d.$wrapper.find("#ta-bulk-device-table .device-row.is-checked");
				if (!$rows.length) {
					frappe.msgprint(__("Pick at least one device."));
					return;
				}
				const per_device = [];
				$rows.each(function () {
					const $row = $(this);
					per_device.push({
						device_sn: $row.data("sn"),
						privilege: show_controls ? $row.find(".privilege-sel").val() || "0" : "0",
						skip_name: show_controls && $row.find(".skip-name-check").is(":checked") ? 1 : 0,
					});
				});
				d.hide();
				send_bulk(listview, employees, command_type, per_device);
			},
			secondary_action_label: __("Cancel"),
			secondary_action() {
				d.hide();
			},
		});

		d.show();
		render_device_table(d, command_type, devices);
	}

	function render_device_table(d, command_type, devices) {
		const $c = d.$wrapper.find("#ta-bulk-device-table");
		const show_controls = command_type === "Add User" || command_type === "Update User";

		const rows = devices
			.map((dev) => `
				<tr class="device-row" data-sn="${frappe.utils.escape_html(dev.device_sn)}">
					<td style="width:40px;text-align:center"><input type="checkbox" class="device-check"></td>
					<td style="width:180px;font-family:var(--font-mono);font-size:13px">
						${frappe.utils.escape_html(dev.device_sn)}</td>
					<td>${frappe.utils.escape_html(dev.device_location || "")}</td>
					<td style="color:var(--text-muted);font-size:var(--text-sm)">
						${frappe.utils.escape_html((dev.farms || []).join(", ") || __("Any farm"))}</td>
					${show_controls ? '<td style="text-align:center"><input type="checkbox" class="skip-name-check"></td>' : ""}
					${show_controls ? `<td><select class="form-control form-control-sm privilege-sel" style="width:100px">
						<option value="0" selected>${__("User")}</option>
						<option value="14">${__("Admin")}</option></select></td>` : ""}
				</tr>`)
			.join("");

		$c.html(`
			<div style="margin-bottom:8px;display:flex;gap:8px;align-items:center">
				<button class="btn btn-xs btn-default" id="ta-select-all">${__("Select All")}</button>
				<button class="btn btn-xs btn-default" id="ta-deselect-all">${__("Deselect All")}</button>
				${show_controls ? `<button class="btn btn-xs btn-default" id="ta-skip-all">${__("Skip")}</button>` : ""}
				<span style="font-size:12px;color:var(--color-text-secondary)" id="ta-selected-count">0 / ${devices.length}</span>
			</div>
			<div style="max-height:400px;overflow-y:auto;border:1px solid var(--color-border-tertiary);border-radius:8px">
				<table class="table table-sm sticky-head-table" style="margin:0">
					<thead><tr>
						<th style="width:40px"></th>
						<th style="width:180px">${__("Device SN")}</th>
						<th>${__("Location")}</th>
						<th>${__("Farms")}</th>
						${show_controls ? `<th style="width:90px;text-align:center">${__("Skip?")}</th>` : ""}
						${show_controls ? `<th style="width:120px">${__("Privilege")}</th>` : ""}
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>`);

		function update_count() {
			const n = $c.find(".device-check:checked").length;
			$c.find("#ta-selected-count").text(`${n} / ${devices.length}`);
			$c.find(".device-row").each(function () {
				const $row = $(this);
				$row.toggleClass("is-checked", $row.find(".device-check").is(":checked"));
			});
		}

		$c.find("#ta-select-all").on("click", () => {
			$c.find(".device-check").prop("checked", true);
			update_count();
		});
		$c.find("#ta-deselect-all").on("click", () => {
			$c.find(".device-check").prop("checked", false);
			update_count();
		});
		$c.find(".device-check").on("change", update_count);

		if (show_controls) {
			$c.find("#ta-skip-all").on("click", () => {
				const $boxes = $c.find(".skip-name-check");
				$boxes.prop("checked", $boxes.filter(":checked").length < $boxes.length);
			});
		}
	}

	function send_bulk(listview, rows, command_type, per_device) {
		// bulk_command identifies people by their device PIN, not by Employee id.
		// The rows were already read when the devices were filtered by farm, so
		// this does not fetch them again. An employee with no PIN cannot be sent
		// at all — worth saying up front rather than as a row failure.
		rows = rows || [];
		const no_pin = [];
		const base_users = [];

		rows.forEach((row) => {
			const pin = (row.attendance_device_id || "").trim();
			if (!pin) {
				no_pin.push(row.employee_name || row.name);
				return;
			}
			base_users.push({
				user_id: pin,
				employee_name: (row.employee_name || row.name || "").trim(),
			});
		});

		if (!base_users.length) {
			frappe.msgprint({
				title: __("Nothing to send"),
				message: __("None of the selected employees has an Attendance Device ID."),
				indicator: "orange",
			});
			return;
		}

		// One call per device. bulk_command takes a single device_sn, and each
		// device carries its own Privilege and Skip, so they cannot be merged.
		// Run in sequence rather than in parallel: these queue commands onto
		// physical readers, and a burst of parallel writes is how the relay gets
		// overwhelmed.
		let queued = 0;
		const errors = [];
		let done = 0;

		frappe.dom.freeze(
			__("Sending {0} employee(s) to {1} device(s)...", [base_users.length, per_device.length])
		);

		function next() {
			if (done >= per_device.length) {
				frappe.dom.unfreeze();
				report({ queued, errors }, no_pin, command_type, per_device.length);
				listview.refresh();
				return;
			}

			const dev = per_device[done];
			const users = base_users.map((u) =>
				Object.assign({}, u, { privilege: dev.privilege, skip_name: dev.skip_name })
			);

			frappe.call({
				method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.bulk_command",
				args: { device_sn: dev.device_sn, users, command_type },
				callback(res) {
					const m = (res && res.message) || {};
					queued += m.queued || 0;
					// Which device refused somebody matters — the same person can
					// be fine on one reader and out of farm on the next.
					(m.errors || []).forEach((e) =>
						errors.push(Object.assign({ device: dev.device_sn }, e))
					);
					done += 1;
					next();
				},
				error() {
					errors.push({
						device: dev.device_sn,
						user_id: "—",
						reason: __("The call to this device failed"),
					});
					done += 1;
					next();
				},
			});
		}

		next();
	}

	function report(result, no_pin, command_type, device_count) {
		result = result || {};
		const queued = result.queued || 0;
		const errors = result.errors || [];

		let message = __("Queued: {0}", [queued]);
		if (device_count > 1) {
			message += " " + __("across {0} devices", [device_count]);
		}
		if (no_pin.length) {
			message += "<br>" + __("Skipped, no Attendance Device ID: {0}", [no_pin.length]);
		}
		message += "<br>" + __("Failed: {0}", [errors.length]);

		// A count alone leaves nowhere to go next, so name what was refused and why
		// — the farm mismatch in particular is a real decision, not a glitch.
		const rows = errors
			.map((e) => `<tr><td>${frappe.utils.escape_html(String(e.user_id || ""))}</td>`
				+ `<td>${frappe.utils.escape_html(String(e.device || ""))}</td>`
				+ `<td>${frappe.utils.escape_html(String(e.reason || ""))}</td></tr>`)
			.concat(no_pin.map((n) => `<tr><td>${frappe.utils.escape_html(n)}</td>`
				+ `<td>—</td><td>${__("No Attendance Device ID")}</td></tr>`));

		if (rows.length) {
			message +=
				'<div style="max-height:300px;overflow:auto;margin-top:10px;'
				+ 'border:1px solid var(--border-color);border-radius:6px">'
				+ '<table class="table table-bordered" style="margin:0;font-size:var(--text-sm)">'
				+ `<thead><tr><th>${__("Employee / PIN")}</th><th>${__("Device")}</th>`
				+ `<th>${__("Why")}</th></tr></thead>`
				+ `<tbody>${rows.join("")}</tbody></table></div>`;
		}

		frappe.msgprint({
			title: command_type === "Add User" ? __("Added to Device") : __("Removed from Device"),
			message,
			indicator: errors.length || no_pin.length ? "orange" : "green",
		});
	}
})();
