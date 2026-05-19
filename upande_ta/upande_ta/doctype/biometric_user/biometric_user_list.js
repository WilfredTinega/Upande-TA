frappe.listview_settings["Biometric User"] = {
	hide_name_column: false,
	onload(listview) {
		listview.page.btn_primary && listview.page.btn_primary.hide();
	},
};
