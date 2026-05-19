// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

frappe.ui.form.on("Bulk Overtime", {
	setup(frm) {
		// Restrict Department dropdown to the selected company
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company },
		}));

		// Restrict Shift Approver to users with approver-related roles
		frm.events.setup_shift_approver_query(frm);

		// Restrict Employee in child table to selected department/group
		frm.set_query("employee", "bulk_overtime_entries", () => ({
			filters: {
				company: frm.doc.company,
				...(frm.doc.department && { department: frm.doc.department }),
				...(frm.doc.designation && { designation: frm.doc.designation }),
				status: "Active"
			}
		}));
	},

	setup_shift_approver_query(frm) {
		const default_approver_roles = [
			"Expense Approver",
			"Leave Approver",
			"Wiki Approver",
			"General Manager",
			"HOD",
			"HR Manager",
			"System Manager",
		];

		// Use the default approver roles (field removed)
		const roles_to_use = default_approver_roles;

		frappe.db.get_list("Has Role", {
			filters: { role: ["in", roles_to_use] },
			fields: ["parent"],
			limit_page_length: 0,
		}).then((users) => {
			const approver_users = [...new Set(users.map((user) => user.parent))];

			const name_filter = approver_users.length
				? approver_users
				: ["__no_match__"];

			frm.set_query("shift_approver", () => ({
				filters: {
					enabled: 1,
					user_type: "System User",
					name: ["in", name_filter],
				},
			}));
		}).catch(() => {
			frm.set_query("shift_approver", () => ({
				filters: {
					enabled: 1,
					user_type: "System User",
					name: ["in", ["__no_match__"]],
				},
			}));
			frappe.show_alert({
				message: __("Could not load approver list. Shift Approver field has been restricted."),
				indicator: "orange",
			}, 5);
		});
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Get Employees"), () => {
				frm.events.get_employees(frm);
			}).toggleClass(
				"btn-primary",
				!(frm.doc.bulk_overtime_entries || []).length,
			);

			// Add button for verification/sync dialog at Draft stage
			frm.add_custom_button(__("Verify Overtime"), () => {
				frm.events.show_verification_dialog(frm);
			});

			// Bulk-fill child row hours_requested from header value
			frm.add_custom_button(__("Apply Requested Hours"), () => {
				frm.events.apply_default_requested_hours(frm);
			});
		}
	},

	get_employees(frm) {
		// Validate whether a department is selected for filtering
		if (!frm.doc.department) {
			frappe.confirm(
				__("No department selected. This will fetch ALL active employees in the company. Continue?"),
				() => {
					frm.events.call_fill_employee_details(frm);
				}
			);
		} else {
			frm.events.call_fill_employee_details(frm);
		}
	},

	call_fill_employee_details(frm) {
		return frappe
			.call({
				doc: frm.doc,
				method: "fill_employee_details",
				freeze: true,
				freeze_message: __("Fetching Employees…"),
			})
			.then((r) => {
				if (r.docs?.[0]?.bulk_overtime_entries) {
					frm.events.apply_default_requested_hours(frm, false);
					frm.dirty();
				}
				frm.refresh();
				frm.scroll_to_field("bulk_overtime_entries");
			});
	},

	// ── Verification / Attendance Sync Dialog ─────────────────────────────────

	show_verification_dialog(frm) {
		const entries = frm.doc.bulk_overtime_entries || [];
		if (!entries.length) {
			frappe.msgprint(__("No overtime entries to verify. Please fetch employees first."));
			return;
		}

		// Sync attendance data first, then show the dialog
		frappe.call({
			doc: frm.doc,
			method: "sync_attendance_data",
			freeze: true,
			freeze_message: __("Syncing Attendance Data…"),
		}).then((r) => {
			const updated_rows = r.message?.updated_rows || [];
			updated_rows.forEach(({ idx, hours_done, overtime_type }) => {
				const row = frm.doc.bulk_overtime_entries[idx];
				if (row) {
					row.hours_done = hours_done;
					row.overtime_type = overtime_type;
				}
			});
			frm.refresh_field("bulk_overtime_entries");

			const synced_entries = frm.doc.bulk_overtime_entries || [];

			let dialog = new frappe.ui.Dialog({
					title: __('Verify Overtime Hours'),
					fields: [
						{
							fieldname: 'verification_note',
							fieldtype: 'HTML',
							options: `
								<div class="alert alert-info">
									<strong>Verification Instructions:</strong><br>
									• Attendance data has been synced — "Actual Hours Done" shows overtime from T&A records<br>
									• You may adjust the actual hours if needed<br>
									• Only entries with hours_done > 0 will be processed on submission<br>
									• Leave as 0 if the employee did not work overtime on that date
								</div>
							`
						},
						{
							fieldname: 'entries_html',
							fieldtype: 'HTML',
							options: frm.events.generate_verification_table(frm, synced_entries)
						}
					],
					primary_action_label: __('Confirm & Process'),
					primary_action: function () {
						frm.events.process_verification(frm, dialog);
					}
				});

				dialog.show();
			});
	},

	generate_verification_table(frm, entries) {
		let html = `
			<div style="max-height: 500px; overflow-y: auto;">
				<table class="table table-bordered">
					<thead>
						<tr>
							<th>Employee ID</th>
							<th>Employee Name</th>
							<th>Date</th>
							<th>Requested Hours</th>
							<th>Actual Hours Done</th>
							<th>Status</th>
						</tr>
					</thead>
					<tbody>
		`;

		entries.forEach((entry, idx) => {
			const hours_done = entry.hours_done || 0;
			const status = hours_done > 0 ? 'To Process' : 'Zero - Skip';
			html += `
				<tr>
					<td>${entry.employee}</td>
					<td>${entry.employee_name || ''}</td>
					<td>${entry.overtime_date}</td>
					<td>${entry.hours_requested || 0}</td>
					<td>
						<input type="number" 
						       class="form-control hours-done-input" 
						       data-idx="${idx}"
						       value="${hours_done}"
						       step="0.5"
						       min="0">
					</td>
					<td class="status-${idx}">${status}</td>
				</tr>
			`;
		});

		html += `
					</tbody>
				</table>
			</div>
			<div class="alert alert-warning mt-2">
				<strong>Note:</strong> Only rows with "Actual Hours Done" > 0 will be processed when confirmed.
			</div>
		`;

		// Add JavaScript to handle input changes
		setTimeout(() => {
			document.querySelectorAll('.hours-done-input').forEach(input => {
				input.addEventListener('change', function () {
					const idx = this.dataset.idx;
					const value = parseFloat(this.value) || 0;
					const statusCell = document.querySelector(`.status-${idx}`);
					if (statusCell) {
						statusCell.textContent = value > 0 ? 'To Process' : 'Zero - Skip';
						statusCell.style.color = value > 0 ? 'green' : 'gray';
					}
				});
			});
		}, 100);

		return html;
	},

	process_verification(frm, dialog) {
		// Get values from dialog inputs
		const inputs = dialog.$wrapper.find('.hours-done-input');
		const updates = [];

		inputs.each(function () {
			const idx = $(this).data('idx');
			const hours_done = parseFloat($(this).val()) || 0;
			updates.push({ idx, hours_done });
		});

		// Update child table with hours_done
		updates.forEach(update => {
			if (frm.doc.bulk_overtime_entries[update.idx]) {
				frm.doc.bulk_overtime_entries[update.idx].hours_done = update.hours_done;
			}
		});

		// Filter out entries with hours_done = 0 for processing
		const entries_to_process = frm.doc.bulk_overtime_entries.filter(entry => (entry.hours_done || 0) > 0);
		const zero_entries = frm.doc.bulk_overtime_entries.filter(entry => (entry.hours_done || 0) === 0);

		let message = `<b>Verification Summary:</b><br>`;
		message += `Entries to process: ${entries_to_process.length}<br>`;
		message += `Entries skipped (hours_done = 0): ${zero_entries.length}<br>`;

		if (entries_to_process.length === 0) {
			message += `<br><b class="text-danger">No entries with hours_done > 0. Nothing will be processed.</b>`;
			frappe.msgprint(message);
			dialog.hide();
			return;
		}

		message += `<br>Do you want to proceed with processing ${entries_to_process.length} overtime entries?`;

		frappe.confirm(
			message,
			() => {
				frm.dirty();
				frm.save().then(() => {
					frappe.msgprint({
						title: __("Success"),
						indicator: "green",
						message: __("Overtime verification completed. {0} entries processed.", [entries_to_process.length])
					});
				});
				dialog.hide();
			},
			() => {
				dialog.hide();
			}
		);
	},

	department(frm) {
		frm.events.clear_entries(frm);
		frm.set_value("shift_approver", "");
	},
	designation(frm) { frm.events.clear_entries(frm); },
	from_date(frm) { frm.events.clear_entries(frm); },
	to_date(frm) { frm.events.clear_entries(frm); },

	default_requested_hours(frm) {
		if (frm.doc.docstatus !== 0) return;
		if (!(frm.doc.bulk_overtime_entries || []).length) return;
		frm.events.apply_default_requested_hours(frm, false);
	},

	clear_entries(frm) {
		frm.clear_table("bulk_overtime_entries");
		frm.set_value("number_of_employees", 0);
		frm.refresh_field("bulk_overtime_entries");
	},

	apply_default_requested_hours(frm, show_message = true) {
		const entries = frm.doc.bulk_overtime_entries || [];
		if (!entries.length) {
			if (show_message) frappe.msgprint(__("No employees in the table. Click Get Employees first."));
			return;
		}

		const value = flt(frm.doc.default_requested_hours);
		if (!value) {
			if (show_message) {
				frappe.msgprint(__("Set Default Requested Hours first."));
			}
			return;
		}

		entries.forEach((row) => {
			row.hours_requested = value;
		});

		frm.refresh_field("bulk_overtime_entries");
		frm.dirty();

		if (show_message) {
			frappe.show_alert(
				{
					message: __("Applied {0} requested hours to {1} employees.", [value, entries.length]),
					indicator: "green",
				},
				5
			);
		}
	},
});