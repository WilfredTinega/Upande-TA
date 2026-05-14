// Copyright (c) 2026, Upande LTD and contributors

frappe.ui.form.on("Employee", {
	before_save(frm) {
		frm.__upande_ta_was_new = frm.is_new();
	},

	after_save(frm) {
		if (!frm.__upande_ta_was_new) return;
		frm.__upande_ta_was_new = false;
		prompt_biometric_enroll(frm);
	},
});

function prompt_biometric_enroll(frm) {
	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.get_devices",
		callback(r) {
			const devices = (r && r.message) || [];
			if (!devices.length) return;

			const pin = (frm.doc.attendance_device_id || "").trim();
			if (!pin) return;

			open_device_picker(frm, devices, pin);
		},
	});
}

function open_device_picker(frm, devices, pin) {
	const rows = devices.map(d => `
		<tr>
			<td style="width:40px;text-align:center">
				<input type="checkbox" class="device-check"
					data-sn="${frappe.utils.escape_html(d.device_sn)}">
			</td>
			<td style="font-family:var(--font-mono);font-size:13px">
				${frappe.utils.escape_html(d.device_sn)}
			</td>
			<td>${frappe.utils.escape_html(d.device_location || "")}</td>
		</tr>
	`).join("");

	const d = new frappe.ui.Dialog({
		title: __("Send to Biometric Device(s)?"),
		size: "large",
		fields: [
			{
				fieldname: "intro_html",
				fieldtype: "HTML",
				options: `<p style="margin-bottom:12px">
					Add <b>${frappe.utils.escape_html(frm.doc.employee_name || frm.doc.name)}</b>
					(PIN <code>${frappe.utils.escape_html(pin)}</code>) to which device(s)?
				</p>`,
			},
			{
				fieldname: "device_table_html",
				fieldtype: "HTML",
				options: `
					<div style="margin-bottom:8px;display:flex;gap:8px;align-items:center">
						<button class="btn btn-xs btn-default" id="dp-select-all">Select All</button>
						<button class="btn btn-xs btn-default" id="dp-deselect-all">Deselect All</button>
					</div>
					<div style="border:1px solid var(--border-color);border-radius:8px;overflow:hidden">
						<table class="table table-sm" style="margin:0">
							<thead style="background:var(--bg-light-gray)">
								<tr>
									<th style="width:40px"></th>
									<th style="width:200px">Device SN</th>
									<th>Location</th>
								</tr>
							</thead>
							<tbody>${rows}</tbody>
						</table>
					</div>
				`,
			},
		],
		primary_action_label: __("Add to Selected"),
		primary_action() {
			const selected = [];
			d.$wrapper.find(".device-check:checked").each(function () {
				selected.push($(this).data("sn"));
			});
			if (!selected.length) {
				frappe.msgprint(__("Pick at least one device, or click Cancel."));
				return;
			}
			frappe.call({
				method: "upande_ta.upande_ta.doctype.biometric_user.biometric_user.add_employees_to_devices",
				args: {
					employees:  JSON.stringify([frm.doc.name]),
					device_sns: JSON.stringify(selected),
				},
				callback(rr) {
					if (rr.exc) return;
					d.hide();
					const queued = (rr.message && rr.message.results || [])
						.reduce((sum, r) => sum + (r.queued || 0), 0);
					frappe.show_alert({
						message: __("Add User queued on {0} device(s).", [selected.length])
							+ ` (${queued} command(s))`,
						indicator: "green",
					}, 6);
				},
			});
		},
		secondary_action_label: __("Skip"),
		secondary_action() {
			d.hide();
		},
	});

	d.show();

	d.$wrapper.on("click", "#dp-select-all", () => {
		d.$wrapper.find(".device-check").prop("checked", true);
	});
	d.$wrapper.on("click", "#dp-deselect-all", () => {
		d.$wrapper.find(".device-check").prop("checked", false);
	});
}
