// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Checkin Poll", {
	refresh: function(frm) {
		const $btn = frm.fields_dict.poll && frm.fields_dict.poll.$wrapper.find("button");
		if ($btn && $btn.length) {
			$btn.html('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>Poll Devices');
			$btn.css({
				"background":     "linear-gradient(135deg, #2490ef 0%, #1a73d9 100%)",
				"color":          "#fff",
				"border":         "none",
				"padding":        "8px 22px",
				"font-weight":    "600",
				"font-size":      "13px",
				"letter-spacing": "0.3px",
				"border-radius":  "6px",
				"box-shadow":     "0 2px 6px rgba(36, 144, 239, 0.35)",
				"cursor":         "pointer",
				"transition":     "all 0.2s ease",
				"margin-top":     "8px"
			});
			$btn.hover(
				function() {
					$(this).css({
						"transform":  "translateY(-1px)",
						"box-shadow": "0 4px 12px rgba(36, 144, 239, 0.5)",
						"background": "linear-gradient(135deg, #1a73d9 0%, #1561c0 100%)"
					});
				},
				function() {
					$(this).css({
						"transform":  "translateY(0)",
						"box-shadow": "0 2px 6px rgba(36, 144, 239, 0.35)",
						"background": "linear-gradient(135deg, #2490ef 0%, #1a73d9 100%)"
					});
				}
			);
		}
	},

	poll: function(frm) {
		if (!frm.doc.start_date || !frm.doc.end_date) {
			frappe.msgprint("Set both Start Date and End Date.");
			return;
		}
		if (frm.doc.start_date > frm.doc.end_date) {
			frappe.msgprint("Start Date cannot be after End Date.");
			return;
		}
		if (!frm.doc.devices || !frm.doc.devices.length) {
			frappe.msgprint("Add at least one device to poll.");
			return;
		}

		const run_poll = () => {
			frappe.call({
				method: "upande_ta.upande_ta.doctype.checkin_poll.checkin_poll.poll_devices",
				args:   { doc_name: frm.doc.name },
				freeze:         true,
				freeze_message: "Sending poll commands to devices...",
				callback: function(r) {
					if (!r.exc && r.message) {
						const m = r.message;
						frappe.show_alert({
							message: __("Poll queued for {0} device(s){1} ({2} → {3}).",
								[m.queued, m.failed ? `, ${m.failed} failed` : "",
								 frm.doc.start_date, frm.doc.end_date]),
							indicator: m.failed ? "orange" : "blue"
						}, 10);
						frm.reload_doc();
					}
				}
			});
		};

		if (frm.is_dirty() || frm.is_new()) {
			frm.save().then(run_poll);
		} else {
			run_poll();
		}
	}
});
