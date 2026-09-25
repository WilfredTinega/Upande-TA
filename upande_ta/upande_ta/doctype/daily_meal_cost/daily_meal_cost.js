// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.ui.form.on("Daily Meal Cost", {
	onload(frm) {
		if (frm.is_new() && !(frm.doc.meals || []).length) {
			["Breakfast", "Lunch", "Supper"].forEach((meal_type) => frm.add_child("meals", { meal_type }));
			frm.refresh_field("meals");
		}
	},
});
