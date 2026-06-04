frappe.listview_settings["Biometric Template"] = {
	hide_name_column: false,
	onload(listview) {
		listview.page.btn_primary && listview.page.btn_primary.hide();
	},
};
