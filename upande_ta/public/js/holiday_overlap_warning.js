// Copyright (c) 2026, Upande LTD and contributors

/**
 * The confirmation Holiday Assignment Tool and Bulk Week Off show before they
 * write Holiday List Assignments, built from holiday_overlap.preview on the
 * server: days an existing assignment will lose (back-dated ones flagged), a
 * later assignment that takes over again, and days left with no week off.
 */
frappe.provide("upande_ta.holiday_overlap");

(function () {
	const esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));
	const day = (value) => frappe.datetime.str_to_user(value);
	const span = (from, to) => (from === to ? day(from) : __("{0} to {1}", [day(from), day(to)]));

	const listed = (items, limit = 6) =>
		items.slice(0, limit).join(", ") + (items.length > limit ? __(" and {0} more", [items.length - limit]) : "");

	function render(result) {
		const employees = (result && result.employees) || [];
		if (!employees.length) return "";

		const blocks = employees.map((e) => {
			const lines = [];
			(e.replaced || []).forEach((r) => {
				lines.push(
					(r.backdated ? `<span class="indicator-pill orange">${__("Back-dated")}</span> ` : "") +
						__("{0}: replaces {1}", [span(r.from_date, r.to_date), `<b>${esc(r.holiday_list)}</b>`]) +
						(r.new ? ` → ${esc(r.new)}` : ` → <b class="text-danger">${__("nothing")}</b>`)
				);
			});
			(e.later || []).forEach((r) => {
				lines.push(
					__("{0} takes over again from {1}", [`<b>${esc(r.holiday_list)}</b>`, day(r.from_date)])
				);
			});
			if ((e.blank_days || []).length) {
				lines.push(
					`<span class="text-danger">${__("No holiday list, shown blank: {0}", [
						listed(e.blank_days.map(day)),
					])}</span>`
				);
			}
			if ((e.weeks_without_week_off || []).length) {
				lines.push(
					`<span class="text-danger">${__("No week off in the weeks of: {0}", [
						listed(e.weeks_without_week_off.map(day)),
					])}</span>`
				);
			}
			return `<div style="margin-bottom:10px;"><b>${esc(e.employee_name)}</b> <span class="text-muted">${esc(
				e.employee
			)}</span><div style="margin-left:12px;">${lines.join("<br>")}</div></div>`;
		});

		const more =
			result.total > employees.length
				? `<div class="text-muted">${__("... and {0} more employee(s)", [result.total - employees.length])}</div>`
				: "";
		return `<div style="max-height:50vh; overflow:auto;">${blocks.join("")}${more}</div>`;
	}

	/** Ask before going on when the preview found anything. Resolves when the
	 * user goes ahead (or there was nothing to say), rejects when they do not. */
	function confirm(result, question) {
		const body = render(result);
		return new Promise((resolve, reject) => {
			if (!body) {
				resolve(false);
				return;
			}
			const dialog = frappe.confirm(`${body}<hr>${question}`, () => resolve(true), () => reject());
			if (dialog && dialog.set_title) dialog.set_title(__("Existing Week Offs Affected"));
		});
	}

	upande_ta.holiday_overlap.render = render;
	upande_ta.holiday_overlap.confirm = confirm;
})();
