// Copyright (c) 2026, Upande LTD and contributors

/**
 * Holiday List Assignment — say what period this record actually covers.
 *
 * The doctype has no end date. An assignment runs until the next one for the
 * same employee starts, which is how Holiday Assignment Tool ends a window: it
 * writes the override at `from_date` and a restore at `to_date + 1`.
 *
 * HRMS' own "Holiday List Start" and "Holiday List End" are not this record's
 * dates at all — they are properties on the controller returning the *Holiday
 * List's* period (see hrms/hr/doctype/holiday_list_assignment), so a record
 * written for a three-day override reads as 1 Jan to 31 Dec and looks like the
 * tool ignored the dates it was given. They are hidden here, and the window
 * this assignment really holds is worked out from the assignment that follows.
 */

frappe.ui.form.on("Holiday List Assignment", {
	refresh(frm) {
		// the Holiday List's own period, not this assignment's
		frm.set_df_property("holiday_list_start", "hidden", 1);
		frm.set_df_property("holiday_list_end", "hidden", 1);
		frm.events.describe_period(frm);
	},

	/** "In force from X until Y", Y being the day before the next assignment
	 * starts. Only for a submitted record: a draft holds nothing, and a
	 * cancelled one has already been superseded. */
	describe_period(frm) {
		frm.set_intro("");
		if (frm.is_new() || frm.doc.docstatus !== 1) return;
		if (!frm.doc.from_date || !frm.doc.assigned_to) return;

		const start = frappe.datetime.str_to_user(frm.doc.from_date);

		frappe.db
			.get_list("Holiday List Assignment", {
				filters: {
					applicable_for: frm.doc.applicable_for,
					assigned_to: frm.doc.assigned_to,
					docstatus: 1,
					from_date: [">", frm.doc.from_date],
				},
				fields: ["name", "from_date", "holiday_list"],
				order_by: "from_date asc",
				limit: 1,
			})
			.then((rows) => {
				const next = (rows || [])[0];
				if (!next) {
					frm.set_intro(
						__("In force from {0} onward — nothing later takes over yet.", [start]),
						"blue",
					);
					return;
				}
				frm.set_intro(
					__("In force from {0} until {1}. From {2}, {3} takes over.", [
						start,
						frappe.datetime.str_to_user(frappe.datetime.add_days(next.from_date, -1)),
						frappe.datetime.str_to_user(next.from_date),
						frappe.utils.escape_html(next.holiday_list),
					]),
					"blue",
				);
			});
	},
});
