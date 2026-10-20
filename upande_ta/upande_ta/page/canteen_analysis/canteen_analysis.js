// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

/* Canteen Analysis inside the desk, like Attendance Insights: the
 * /canteen-analysis page is shown in the content area, so it opens without
 * leaving the desk or opening a tab. The desk URL's query string (company,
 * farm, meal, view) is handed to the frame, and the frame writes its own
 * choices back to the desk URL. */
frappe.pages["canteen-analysis"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Canteen Analysis"),
		single_column: true,
	});
	page.add_inner_button(__("Daily Meal Cost"), () =>
		frappe.set_route("List", "Daily Meal Cost"),
	);
	page.add_inner_button(__("Meal Checkins"), () => frappe.set_route("List", "Meal Checkin"));
	page.set_primary_action(
		__("Refresh"),
		() => {
			try {
				$frame[0].contentWindow.location.reload();
			} catch (e) {
				$frame.attr("src", "/canteen-analysis" + window.location.search);
			}
		},
		"refresh",
	);
	$(wrapper).find(".layout-main-section").css({ padding: 0 });
	$(wrapper).find(".page-body").css({ "max-width": "none", padding: 0 });

	const $frame = $(
		`<iframe class="ta-canteen-frame" title="${__("Canteen Analysis")}"
			style="width:100%;border:0;display:block"></iframe>`,
	).appendTo(page.main);

	const fit = () => {
		const top = $frame[0].getBoundingClientRect().top;
		$frame.css("height", Math.max(window.innerHeight - top, 400) + "px");
	};
	$(window).on("resize", fit);
	$frame.on("load", fit);

	// Loads the frame for the desk URL's query unless it already shows it
	// (it wrote that query itself, or the desk came back to the same one).
	wrapper.ta_canteen_sync = () => {
		const query = window.location.search;
		// the router copies the query into route_options; nothing here reads
		// them, and a list opened next would take them as filters
		frappe.route_options = null;
		let shown = null;
		try {
			const loc = $frame[0].contentWindow.location;
			if (loc.pathname === "/canteen-analysis") shown = loc.search;
		} catch (e) {
			shown = null;
		}
		if (shown !== query) $frame.attr("src", "/canteen-analysis" + query);
		setTimeout(fit, 0);
	};
};

frappe.pages["canteen-analysis"].on_page_show = function (wrapper) {
	wrapper.ta_canteen_sync && wrapper.ta_canteen_sync();
};
