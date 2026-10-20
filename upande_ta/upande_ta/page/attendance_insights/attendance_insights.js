// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

/* Attendance Insights inside the desk, like the Exchange Dashboard: the
 * /attendance-insights page is shown in the content area, so it opens without
 * leaving the desk or opening a tab. The page keeps its own styles and
 * script; a frame keeps them from clashing with the desk's. */
frappe.pages["attendance-insights"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Attendance Insights"),
		single_column: true,
	});
	// the dashboard carries its own tabs and filters: no desk title bar above it
	$(wrapper).find(".page-head").hide();
	$(wrapper).find(".layout-main-section").css({ padding: 0 });
	$(wrapper).find(".page-body").css({ "max-width": "none", padding: 0 });

	const $frame = $(
		`<iframe class="ta-insights-frame" title="${__("Attendance Insights")}"
			src="/attendance-insights" style="width:100%;border:0;display:block"></iframe>`,
	).appendTo(page.main);

	const fit = () => {
		const top = $frame[0].getBoundingClientRect().top;
		$frame.css("height", Math.max(window.innerHeight - top, 400) + "px");
	};
	$(window).on("resize", fit);
	$frame.on("load", fit);
	setTimeout(fit, 0);
	wrapper.ta_insights_fit = fit;
};

frappe.pages["attendance-insights"].on_page_show = function (wrapper) {
	wrapper.ta_insights_fit && setTimeout(wrapper.ta_insights_fit, 0);
};
