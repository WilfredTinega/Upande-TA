// Copyright (c) 2026, Upande LTD and contributors

frappe.listview_settings["Gate Pass"] = {
	add_fields: ["workflow_state", "returned", "docstatus"],

	get_indicator(doc) {
		const colours = {
			Draft: "grey",
			"Pending Supervisor": "orange",
			"Pending HOD": "orange",
			"Pending HR": "orange",
			Approved: "green",
			Rejected: "red",
		};

		const state = doc.workflow_state || "Draft";
		return [__(state), colours[state] || "grey", "workflow_state,=," + state];
	},
};
