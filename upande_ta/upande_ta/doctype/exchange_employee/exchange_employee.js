// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

const EXE_FETCHED = [
	"full_name",
	"home_employee_id",
	"id_number",
	"phone",
	"gender",
	"date_of_joining",
	"home_company",
	"home_department",
	"home_designation",
	"image",
];

frappe.ui.form.on("Exchange Employee", {
	setup(frm) {
		frm.set_query("department", () => ({ filters: { company: frm.doc.host_company } }));
		frm.set_query("custom_farm", () =>
			frm.doc.host_company ? { filters: { company: frm.doc.host_company } } : {},
		);
		frappe.xcall("upande_ta.upande_ta.exchange.instance_codes").then((codes) => {
			frm.exe_instance_codes = codes || [];
			exe_instance_options(frm);
		});
		frappe.xcall("upande_ta.upande_ta.exchange.device_options").then((devices) => {
			frm.exe_devices = devices || [];
			frm.fields_dict.devices.grid.update_docfield_property(
				"device_sn",
				"options",
				[""].concat(frm.exe_devices.map((d) => d.device_sn)).join("\n"),
			);
		});
	},

	refresh(frm) {
		exe_instance_options(frm);
		frm.set_intro("");
		if (frm.doc.link_status === "Not Found") {
			frm.set_intro(
				__("{0} has no employee with payroll number or Employee ID {1}.", [
					frm.doc.home_instance,
					frm.doc.payroll_number,
				]),
				"orange",
			);
		}
		const my_code = (frm.doc.__onload || {}).my_code;
		const received = !!(
			frm.doc.source_instance &&
			my_code &&
			frm.doc.source_instance !== my_code
		);
		frm.toggle_display(
			"fetch_from_home",
			!received && !!(frm.doc.home_instance && frm.doc.payroll_number),
		);
		["home_instance", "payroll_number", "status", "program_start", "program_end"].forEach(
			(f) => frm.set_df_property(f, "read_only", received ? 1 : 0),
		);
		if (received) {
			frm.set_intro(
				__(
					"Made on {0}: their details and program dates are changed there. The company here, the Device PIN and the devices are managed on this form.",
					[frm.doc.source_instance],
				),
				"blue",
			);
		}
		if (frm.doc.home_instance) {
			frm.set_df_property(
				"fetch_from_home",
				"label",
				__("Fetch from {0}", [frm.doc.home_instance]),
			);
			frm.set_df_property(
				"home_section",
				"label",
				__("From {0} (read only)", [frm.doc.home_instance]),
			);
		}
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Enroll on Devices"),
			() => exe_pick_devices(frm, "enroll"),
			__("Biometric"),
		);
		frm.add_custom_button(
			__("Update on Devices"),
			() => exe_run(frm, "update_on_devices"),
			__("Biometric"),
		);
		frm.add_custom_button(
			__("Fetch Templates"),
			() => exe_fetch_templates(frm),
			__("Biometric"),
		);
		frm.add_custom_button(
			__("Delete from Devices"),
			() => exe_pick_devices(frm, "delete"),
			__("Biometric"),
		);
		frm.add_custom_button(__("View Checkins"), () =>
			frappe.set_route("List", "Exchange Checkin", { exchange_employee: frm.doc.name }),
		);
	},

	home_instance(frm) {
		frm.events.lookup(frm);
		frm.trigger("refresh");
	},

	payroll_number(frm) {
		frm.events.lookup(frm);
	},

	fetch_from_home(frm) {
		if (frm.is_new()) {
			frm.events.lookup(frm, true);
			return;
		}
		frm.call({
			method: "fetch_home_details",
			doc: frm.doc,
			args: { reset_host: 0 },
			freeze: true,
		}).then((r) => {
			frm.reload_doc();
			const found = r.message && r.message.found;
			frappe.show_alert({
				message: found
					? __("Fetched from {0}: {1} template(s) updated", [
							frm.doc.home_instance,
							(r.message && r.message.templates) || 0,
						])
					: r.message && r.message.message,
				indicator: found ? "green" : "orange",
			});
		});
	},

	/** On a new record, fill the person in as soon as both fields are set,
	 * so HR sees who they are adding before saving. */
	lookup(frm, loud) {
		if (!frm.is_new() || !frm.doc.home_instance || !frm.doc.payroll_number) return;
		frappe
			.xcall("upande_ta.upande_ta.exchange.lookup_home_employee", {
				home_instance: frm.doc.home_instance,
				payroll_number: frm.doc.payroll_number,
			})
			.then((r) => {
				if (!r.found) {
					EXE_FETCHED.forEach((f) => frm.set_value(f, null));
					frm.set_value("link_status", "Not Found");
					frappe.show_alert({ message: r.message, indicator: "orange" });
					return;
				}
				EXE_FETCHED.forEach((f) => frm.set_value(f, r.employee[f] || null));
				frm.set_value("link_status", "Linked");
				frm.set_value("last_fetched_on", frappe.datetime.now_datetime());
				if (!frm.doc.device_pin && /^\d+$/.test(frm.doc.payroll_number)) {
					frm.set_value("device_pin", frm.doc.payroll_number);
				}
				frm.set_value("host_company", r.host.company || null);
				frm.set_value("custom_farm", r.host.farm || null);
				frm.set_value("department", r.host.department || null);
				if (loud || !r.host.mapped) {
					frappe.show_alert({
						message: r.host.mapped
							? __("Fetched {0} from {1}", [
									r.employee.full_name,
									frm.doc.home_instance,
								])
							: __(
									"{0} on {1} has no row in the Company Map of Exchange Setting. Set the Host Company by hand, or add the row.",
									[r.employee.home_company, frm.doc.home_instance],
								),
						indicator: r.host.mapped ? "green" : "orange",
					});
				}
			});
	},
});

frappe.ui.form.on("Exchange Employee Device", {
	device_sn(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const device = (frm.exe_devices || []).find((d) => d.device_sn === row.device_sn);
		frappe.model.set_value(
			cdt,
			cdn,
			"device_location",
			(device && device.device_location) || "",
		);
	},
});

/** Home Instance lists the instances on Exchange Setting → Instances. A
 * record received from an instance not listed here keeps its own value. */
function exe_instance_options(frm) {
	if (!frm.exe_instance_codes) return;
	const codes = frm.exe_instance_codes.slice();
	if (frm.doc.home_instance && !codes.includes(frm.doc.home_instance)) {
		codes.push(frm.doc.home_instance);
	}
	frm.set_df_property("home_instance", "options", [""].concat(codes));
}

function exe_run(frm, method, args) {
	return frm.call({ method, doc: frm.doc, args: args || {}, freeze: true }).then((r) => {
		frm.reload_doc();
		const m = r.message || {};
		const parts = [];
		if (m.devices !== undefined) parts.push(__("{0} device(s)", [m.devices]));
		if (m.templates !== undefined) parts.push(__("{0} template(s)", [m.templates]));
		frappe.show_alert({
			message: __("Sent to Node-RED: {0}", [parts.join(", ") || __("done")]),
			indicator: "green",
		});
	});
}

/** Fetch Templates: from the devices at their home instance holding them,
 * and from the devices here. */
function exe_fetch_templates(frm) {
	frm.call({ method: "template_sources", doc: frm.doc, freeze: true }).then((r) => {
		const src = r.message || {};
		const home = src.home || [];
		const local = src.local || [];
		if (!home.length && !local.length) {
			frappe.msgprint(
				src.home_error || __("No devices with templates for {0}.", [frm.doc.full_name]),
			);
			return;
		}
		const fields = [];
		if (home.length) {
			fields.push({
				fieldtype: "MultiCheck",
				fieldname: "home_devices",
				label: __("At {0}", [frm.doc.home_instance]),
				columns: 1,
				options: home.map((x) => ({
					label: `${x.device_location || x.device_sn} (${x.device_sn}) · ${x.modalities.join(", ")}${
						x.stored ? " · " + __("stored here") : ""
					}`,
					value: x.device_sn,
					checked: x.stored ? 0 : 1,
				})),
			});
		} else if (src.home_error) {
			fields.push({
				fieldtype: "HTML",
				fieldname: "home_error",
				options: `<p class="text-muted">${frappe.utils.escape_html(src.home_error)}</p>`,
			});
		}
		if (local.length) {
			fields.push({
				fieldtype: "MultiCheck",
				fieldname: "local_devices",
				label: __("Here"),
				columns: 1,
				options: local.map((x) => ({
					label: `${x.device_location || x.device_sn} (${x.device_sn})${
						x.on_device ? " · " + __("on device") : ""
					}${x.online ? "" : " · " + __("offline")}`,
					value: x.device_sn,
					checked: 0,
				})),
			});
		}
		const d = new frappe.ui.Dialog({
			title: __("Fetch Templates"),
			fields,
			primary_action_label: __("Fetch Templates"),
			primary_action(values) {
				const home_devices = values.home_devices || [];
				const local_devices = values.local_devices || [];
				if (!home_devices.length && !local_devices.length) {
					frappe.msgprint(__("Pick at least one device."));
					return;
				}
				d.hide();
				frm.call({
					method: "fetch_templates_from",
					doc: frm.doc,
					args: { home_devices, local_devices },
					freeze: true,
				}).then((res) => {
					frm.reload_doc();
					const m = res.message || {};
					frappe.show_alert({
						message: [
							home_devices.length &&
								__("{0} template(s) from {1}", [
									m.copied || 0,
									frm.doc.home_instance,
								]),
							local_devices.length &&
								__("Asked {0} device(s) here", [m.requested || 0]),
						]
							.filter(Boolean)
							.join(" · "),
						indicator: "green",
					});
				});
			},
		});
		d.show();
	});
}

/** One dialog for the device actions: enroll and delete take several devices. */
function exe_pick_devices(frm, action) {
	frm.call({ method: "device_choices", doc: frm.doc }).then((r) => {
		const devices = r.message || [];
		if (!devices.length) {
			frappe.msgprint(__("No devices in Biometric Setting yet."));
			return;
		}
		const titles = {
			enroll: __("Enroll on Devices"),
			delete: __("Delete from Devices"),
		};
		const label = (d) =>
			`${d.device_location || d.device_sn} (${d.device_sn})${d.on_device ? " · " + __("on device") : ""}${
				d.online ? "" : " · " + __("offline")
			}`;

		const choices = devices.filter((x) => (action === "delete" ? x.on_device : true));
		if (!choices.length) {
			frappe.msgprint(__("{0} is not on any device.", [frm.doc.full_name]));
			return;
		}
		const d = new frappe.ui.Dialog({
			title: titles[action],
			fields: [
				{
					fieldtype: "MultiCheck",
					fieldname: "devices",
					label: __("Devices"),
					reqd: 1,
					columns: 1,
					options: choices.map((x) => ({
						label: label(x),
						value: x.device_sn,
						checked: action === "delete" ? 1 : x.on_device ? 0 : 1,
					})),
				},
			],
			primary_action_label: titles[action],
			primary_action(values) {
				if (!(values.devices || []).length) {
					frappe.msgprint(__("Pick at least one device."));
					return;
				}
				d.hide();
				exe_run(frm, action === "delete" ? "delete_from_devices" : "enroll_on_devices", {
					devices: values.devices,
				});
			},
		});
		d.show();
	});
}
