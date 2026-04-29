// Copyright (c) 2026, Upande LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Poll BioData", {
	refresh: function(frm) {
		const $btn = frm.fields_dict.get_biodata && frm.fields_dict.get_biodata.$wrapper.find("button");
		if ($btn && $btn.length) {
			$btn.html('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>Get BioData');
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

	get_biodata: function(frm) {
		if (!frm.doc.device_sn) {
			frappe.msgprint("Please select a device first.");
			return;
		}

		const open_dialog = () => {
			let d = new frappe.ui.Dialog({
				title: `Poll BioData — ${frm.doc.device_name || frm.doc.device_sn}`,
				fields: [
					{
						fieldname: "info",
						fieldtype: "HTML",
						options: `<div style="padding:8px 0;color:var(--text-muted)">
							Leave Employee empty to poll <strong>all enrolled users</strong> on the device.<br>
							Pick an employee to poll only their templates.
						</div>`
					},
					{
						fieldname: "employee",
						fieldtype: "Link",
						label:     "Employee (optional — leave empty for all)",
						options:   "Employee",
						get_query() {
							return {
								filters: {
									status: "Active",
									attendance_device_id: ["is", "set"]
								}
							};
						},
						onchange() {
							const emp = d.get_value("employee");
							if (!emp) {
								d.set_value("pin", "");
								d.set_df_property("pin", "description", "");
								return;
							}
							frappe.db.get_value("Employee", emp, "attendance_device_id").then(r => {
								const pin = (r && r.message && r.message.attendance_device_id) || "";
								d.set_value("pin", pin);
								d.set_df_property(
									"pin",
									"description",
									pin ? "" : "This employee has no Attendance Device ID set."
								);
							});
						}
					},
					{
						fieldname: "pin",
						fieldtype: "Data",
						label:     "PIN",
						read_only: 1
					}
				],
				primary_action_label: "Request BioData",
				primary_action(values) {
					if (values.employee && !values.pin) {
						frappe.msgprint("The selected employee has no Attendance Device ID — set one or leave the employee empty to poll all.");
						return;
					}
					frappe.call({
						method: "upande_ta.upande_ta.doctype.poll_biodata.poll_biodata.request_biodata",
						args: {
							doc_name: frm.doc.name,
							pin:      values.pin || null
						},
						freeze:         true,
						freeze_message: "Sending biodata poll command...",
						callback(r) {
							if (!r.exc) {
								d.hide();
								frappe.show_alert({
									message: values.pin
										? `BioData poll queued for PIN ${values.pin} on ${frm.doc.device_sn}. Templates will arrive within 30 seconds.`
										: `BioData poll queued for all users on ${frm.doc.device_sn}. Templates will arrive within 30 seconds.`,
									indicator: "blue"
								}, 10);
							}
						}
					});
				}
			});
			d.show();
		};

		if (frm.is_dirty() || frm.is_new()) {
			frm.save().then(open_dialog);
		} else {
			open_dialog();
		}
	}
});
