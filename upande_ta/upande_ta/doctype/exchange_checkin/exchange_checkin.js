// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Exchange Checkin", {
	setup(frm) {
		frm.set_query("exchange_employee", () => ({ filters: { status: "Active" } }));
		frappe.xcall("upande_ta.upande_ta.exchange.device_options").then((devices) => {
			frm.exc_devices = devices || [];
			exc_device_options(frm);
		});
	},

	refresh(frm) {
		exc_device_options(frm);
	},

	device_sn(frm) {
		const device = (frm.exc_devices || []).find((d) => d.device_sn === frm.doc.device_sn);
		frm.set_value("device_location", (device && device.device_location) || "");
	},
});

/** Device lists the devices on Biometric Setting; a punch synced from
 * another instance keeps the device it was made on. */
function exc_device_options(frm) {
	if (!frm.exc_devices) return;
	const codes = frm.exc_devices.map((d) => d.device_sn);
	if (frm.doc.device_sn && !codes.includes(frm.doc.device_sn)) codes.push(frm.doc.device_sn);
	frm.set_df_property("device_sn", "options", [""].concat(codes));
}
