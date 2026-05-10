// Copyright (c) 2026, Upande LTD and contributors

frappe.ui.form.on("Biometric Template", {
	onload(frm) {
		load_device_options(frm);
	},

	refresh(frm) {
		load_device_options(frm);
	},

	device_sn(frm) {
		const match = (frm._device_options || []).find(d => d.device_sn === frm.doc.device_sn);
		frm.set_value("device_name", match ? (match.device_location || "") : "");
	},
});

function load_device_options(frm) {
	frappe.call({
		method: "upande_ta.upande_ta.doctype.biometric_template.biometric_template.get_setting_devices",
		callback: (r) => {
			const devices = (r && r.message) || [];
			frm._device_options = devices;
			const opts_str = "\n" + devices.map(d => d.device_sn).filter(Boolean).join("\n");
			frm.set_df_property("device_sn", "options", opts_str);
		},
	});
}
