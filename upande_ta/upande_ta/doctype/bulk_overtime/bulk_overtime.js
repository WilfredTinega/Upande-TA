// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

frappe.ui.form.on("Bulk Overtime", {
	setup(frm) {
		frm.set_query("department", () => ({}));
		frm.set_query("branch", () => ({}));
	},

<<<<<<< HEAD
		// Restrict Branch dropdown to the selected company
		frm.set_query("branch", () => ({
			filters: { company: frm.doc.company },
		}));
		
		// Restrict Employee in child table to selected department/group
		frm.set_query("employee", "bulk_overtime_entries", () => ({
			filters: {
				company: frm.doc.company,
				...(frm.doc.department && { department: frm.doc.department }),
				...(frm.doc.branch && { branch: frm.doc.branch }),
				...(frm.doc.designation && { designation: frm.doc.designation }),
				...(frm.doc.grade && { grade: frm.doc.grade }),
				status: "Active"
			}
		}));
=======
	onload(frm) {
		if (frm.is_new()) {
			if (!frm.doc.to_date) {
				frm.set_value("to_date", frappe.datetime.get_today());
			}
			if (!frm.doc.from_date) {
				frm.set_value("from_date", frappe.datetime.add_days(frappe.datetime.get_today(), -30));
			}
		}
>>>>>>> main
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Get Employees"), () => {
				frm.events.get_employees(frm);
<<<<<<< HEAD
			}).toggleClass(
				"btn-primary",
				!(frm.doc.bulk_overtime_entries || []).length,
			);
			
			// Add button for verification dialog at Draft stage
			frm.add_custom_button(__("Verify Overtime"), () => {
				frm.events.show_verification_dialog(frm);
			});

			// Bulk-fill child row hours_requested from header value
			frm.add_custom_button(__("Apply Requested Hours"), () => {
				frm.events.apply_default_requested_hours(frm);
			});
		}
		
		// Show summary in a more visible way
		if (frm.events.has_overtime_request_field(frm) && frm.doc.overtime_request) {
			frm.set_df_property("overtime_request", "read_only", 1);
			frm.set_df_property("overtime_request", "description", 
				__("System-generated summary of all overtime requests"));
		}
	},

	// ── Employee fetching with proper filters ─────────────────────────────────

=======
			}).toggleClass("btn-primary", !(frm.doc.bulk_overtime_entries || []).length);
		}
	},

>>>>>>> main
	get_employees(frm) {
		const mandatory = ["from_date", "to_date"];
		const missing = mandatory.filter(f => !frm.doc[f]);

		if (missing.length) {
			frappe.msgprint({
				title: __("Missing Fields"),
				indicator: "red",
				message: __("Please fill in: ") + missing.map(f => __(frappe.unscrub(f))).join(", "),
			});
			return;
		}
<<<<<<< HEAD
		
		// Validate that department or branch is selected for filtering
		if (!frm.doc.department && !frm.doc.branch) {
			frappe.confirm(
				__("No department or branch selected. This will fetch ALL active employees in the company. Continue?"),
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
					// Update the overtime request summary after fetching
					frm.events.update_overtime_summary(frm);
					frm.dirty();
					frm.save();
				}
				frm.refresh();
				frm.scroll_to_field("bulk_overtime_entries");
			});
=======

		return frappe.call({
			doc: frm.doc,
			method: "fill_employee_details",
			freeze: true,
			freeze_message: __("Fetching Employees…"),
		}).then(r => {
			if (r.docs?.[0]?.bulk_overtime_entries) {
				frm.dirty();
				frm.save();
			}
			frm.refresh();
			frm.scroll_to_field("bulk_overtime_entries");
		});
>>>>>>> main
	},
	
	// ── Overtime Request Summary Field (Gap 1 fix) ───────────────────────────
	
	update_overtime_summary(frm) {
		const entries = frm.doc.bulk_overtime_entries || [];
		if (!entries.length) {
			frm.events.set_overtime_request(frm, "");
			return;
		}
		
		// Group entries by employee
		const employee_map = new Map();
		entries.forEach(entry => {
			if (!employee_map.has(entry.employee)) {
				employee_map.set(entry.employee, {
					employee_name: entry.employee_name,
					dates: []
				});
			}
			employee_map.get(entry.employee).dates.push({
				date: entry.overtime_date,
				hours: entry.hours_requested
			});
		});
		
		// Build summary text
		let summary = "OVERTIME REQUEST SUMMARY\n";
		summary += "=" .repeat(50) + "\n\n";
		
		for (let [employee_id, data] of employee_map) {
			summary += `Employee: ${employee_id} - ${data.employee_name}\n`;
			summary += `Overtime Dates:\n`;
			data.dates.forEach(date_info => {
				summary += `  • ${date_info.date}: ${date_info.hours} hours\n`;
			});
			summary += `Total Hours: ${data.dates.reduce((sum, d) => sum + (parseFloat(d.hours) || 0), 0)}\n`;
			summary += "-".repeat(30) + "\n";
		}
		
		summary += `\nGenerated on: ${frappe.datetime.now_datetime()}`;
		frm.events.set_overtime_request(frm, summary);
	},
	
	// ── Verification Dialog (Gap 3 fix) ───────────────────────────────────────
	
	show_verification_dialog(frm) {
		const entries = frm.doc.bulk_overtime_entries || [];
		if (!entries.length) {
			frappe.msgprint(__("No overtime entries to verify. Please fetch employees first."));
			return;
		}
		
		// Create a dialog for verification
		let dialog = new frappe.ui.Dialog({
			title: __('Verify Overtime Hours'),
			fields: [
				{
					fieldname: 'verification_note',
					fieldtype: 'HTML',
					options: `
						<div class="alert alert-info">
							<strong>Verification Instructions:</strong><br>
							• Enter the actual hours worked for each employee on each date<br>
							• Hours done starts at 0 - please fill in the actual overtime performed<br>
							• Only entries with hours_done > 0 will be processed<br>
							• Leave as 0 if the employee did not work overtime on that date
						</div>
					`
				},
				{
					fieldname: 'entries_html',
					fieldtype: 'HTML',
					options: frm.events.generate_verification_table(frm, entries)
				}
			],
			primary_action_label: __('Confirm & Process'),
			primary_action: function() {
				frm.events.process_verification(frm, dialog);
			}
		});
		
		dialog.show();
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
			const status = hours_done > 0 ? '✅ To Process' : '⏸️ Zero - Skip';
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
				input.addEventListener('change', function() {
					const idx = this.dataset.idx;
					const value = parseFloat(this.value) || 0;
					const statusCell = document.querySelector(`.status-${idx}`);
					if (statusCell) {
						statusCell.textContent = value > 0 ? '✅ To Process' : '⏸️ Zero - Skip';
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
		
		inputs.each(function() {
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
		message += `✅ Entries to process: ${entries_to_process.length}<br>`;
		message += `⏸️ Entries skipped (hours_done = 0): ${zero_entries.length}<br>`;
		
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
				// Update summary with actual hours
				frm.events.update_overtime_summary_with_actual(frm);
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
	
	update_overtime_summary_with_actual(frm) {
		const entries = (frm.doc.bulk_overtime_entries || []).filter(e => (e.hours_done || 0) > 0);
		if (!entries.length) {
			frm.events.set_overtime_request(frm, "No verified overtime entries with hours > 0.");
			return;
		}
		
		// Group by employee
		const employee_map = new Map();
		entries.forEach(entry => {
			if (!employee_map.has(entry.employee)) {
				employee_map.set(entry.employee, {
					employee_name: entry.employee_name,
					dates: []
				});
			}
			employee_map.get(entry.employee).dates.push({
				date: entry.overtime_date,
				requested: entry.hours_requested || 0,
				actual: entry.hours_done
			});
		});
		
		let summary = "VERIFIED OVERTIME SUMMARY (Actual Hours > 0)\n";
		summary += "=" .repeat(60) + "\n\n";
		
		for (let [employee_id, data] of employee_map) {
			summary += `Employee: ${employee_id} - ${data.employee_name}\n`;
			summary += `Overtime Details:\n`;
			data.dates.forEach(date_info => {
				summary += `  • ${date_info.date}: Requested ${date_info.requested}h | Actual ${date_info.actual}h\n`;
			});
			const total_actual = data.dates.reduce((sum, d) => sum + d.actual, 0);
			summary += `Total Actual Hours: ${total_actual}\n`;
			summary += "-".repeat(35) + "\n";
		}
		
		summary += `\nVerified on: ${frappe.datetime.now_datetime()}`;
		frm.events.set_overtime_request(frm, summary);
	},

	branch(frm) { frm.events.clear_entries(frm); },
	department(frm) { frm.events.clear_entries(frm); },
	designation(frm) { frm.events.clear_entries(frm); },
	grade(frm) { frm.events.clear_entries(frm); },
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
		frm.events.set_overtime_request(frm, "");
	},

	has_overtime_request_field(frm) {
		return Boolean(frm.get_field("overtime_request"));
	},

	set_overtime_request(frm, value) {
		if (!frm.events.has_overtime_request_field(frm)) return;
		frm.set_value("overtime_request", value || "");
		frm.refresh_field("overtime_request");
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
		frm.events.update_overtime_summary(frm);
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