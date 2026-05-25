// Copyright (c) 2026, Upande LTD and Contributors
// See license.txt

frappe.ui.form.on("Bulk Overtime", {
	setup(frm) {
		// Restrict Department dropdown to the selected company
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company },
		}));

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

	refresh(frm) {
		frm.events.ensure_draft_editable(frm);
		frm.events.set_overtime_date_limits(frm);
		frm.events.set_verification_intro(frm);

		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Get Employees"), () => {
				frm.events.get_employees(frm);
			}).toggleClass(
				"btn-primary",
				!(frm.doc.bulk_overtime_entries || []).length,
			);

			if (frm.events.can_verify_overtime(frm)) {
				frm.add_custom_button(__("Verify Overtime"), () => {
					frm.events.show_verification_dialog(frm);
				});
			}

			// Bulk-fill child row hours_requested from header value
			frm.add_custom_button(__("Apply Requested Hours"), () => {
				frm.events.apply_default_requested_hours(frm);
			});
		}
	},

	can_verify_overtime(frm) {
		if (!frm.doc.to_date) return false;
		return frappe.datetime.get_diff(frappe.datetime.get_today(), frm.doc.to_date) > 0;
	},

	get_verification_available_from(frm) {
		return frappe.datetime.add_days(frm.doc.to_date, 1);
	},

	set_verification_intro(frm) {
		frm.set_intro("");

		if (frm.doc.docstatus !== 0 || frm.is_new() || !frm.doc.to_date) return;

		if (frm.events.can_verify_overtime(frm)) {
			frm.set_intro(
				__(
					"The overtime period has ended. You can verify actual hours worked using the <b>Verify Overtime</b> button."
				),
				"blue"
			);
			return;
		}

		frm.set_intro(
			__(
				"Overtime verification will be available from <b>{0}</b> (after the overtime period ending {1}).",
				[
					frappe.datetime.str_to_user(frm.events.get_verification_available_from(frm)),
					frappe.datetime.str_to_user(frm.doc.to_date),
				]
			),
			"orange"
		);
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
				frm.events.set_overtime_date_limits(frm);
				frm.refresh();
				frm.scroll_to_field("bulk_overtime_entries");
			});
	},

	// ── Verification / Attendance Sync Dialog ─────────────────────────────────

	show_verification_dialog(frm) {
		if (!frm.events.can_verify_overtime(frm)) {
			frappe.msgprint({
				title: __("Verification Not Available"),
				indicator: "orange",
				message: __(
					"Overtime verification is only available from {0} onwards (after the overtime period ending {1}).",
					[
						frappe.datetime.str_to_user(frm.events.get_verification_available_from(frm)),
						frappe.datetime.str_to_user(frm.doc.to_date),
					]
				),
			});
			return;
		}

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

	validate(frm) {
		frm.events.validate_overtime_dates_in_range(frm);
	},

	ensure_draft_editable(frm) {
		if (frm.doc.docstatus !== 0 || frm.is_new()) return;
		frm.enable_save();
	},

	setup_overtime_date_picker_listener(frm) {
		if (frm._bulk_ot_date_picker_bound) return;

		const grid = frm.fields_dict?.bulk_overtime_entries?.grid;
		if (!grid) return;

		frm._bulk_ot_date_picker_bound = true;
		grid.wrapper.on(
			"focusin",
			'[data-fieldname="overtime_date"] input',
			function () {
				const $input = $(this);
				frm.events.apply_overtime_datepicker($input, frm);

				// Also hook into the datepicker's own show event
				// This fires AFTER the calendar renders, guaranteeing limits apply
				const tryBindShow = (attempts = 0) => {
					const dp = $input.data("datepicker");
					if (dp) {
						$input.off("show.datepicker").on("show.datepicker", function () {
							frm.events.apply_overtime_datepicker($input, frm);
						});
						return;
					}
					if (attempts < 10) setTimeout(() => tryBindShow(attempts + 1), 50);
				};
				tryBindShow();
			}
		);
	},

	// CHANGED: use T00:00:00 suffix to force local time parsing, avoiding UTC timezone shift (EAT+3)
	// CHANGED: air-datepicker v3 — use onSelect callback to trigger change/blur so Frappe picks up the value
	apply_overtime_datepicker($input, frm) {
		if (!frm.doc.from_date || !frm.doc.to_date || !$input?.length) return;

		const tryApply = (attempts = 0) => {
			const datepicker = $input.data("datepicker");
			if (datepicker) {
				// Set date range limits and hook onSelect so Frappe picks up the value
				datepicker.update({
					minDate: new Date(frm.doc.from_date + "T00:00:00"),
					maxDate: new Date(frm.doc.to_date + "T00:00:00"),
					onSelect({ date, formattedDate, datepicker: dp }) {
						setTimeout(() => {
							$input.trigger("change");
							$input.trigger("blur");
						}, 50);
					},
				});
				datepicker.renderAll();
				return;
			}
			// Datepicker not ready yet — retry up to 10 times at 50ms intervals
			if (attempts < 10) {
				setTimeout(() => tryApply(attempts + 1), 50);
			}
		};

		tryApply();
	},

	set_overtime_date_limits(frm) {
		const table = "bulk_overtime_entries";
		const field = "overtime_date";
		const grid = frm.fields_dict?.[table]?.grid;
		if (!grid) return;

		// Do not set min_date/max_date on the docfield — that can lock grid dates after save.
		frm.set_df_property(table, "min_date", null, frm.doc.name, field);
		frm.set_df_property(table, "max_date", null, frm.doc.name, field);

		frm.events.setup_overtime_date_picker_listener(frm);
	},

	// REMOVED: bulk_overtime_entries_on_form_rendered — non-standard event name, never fires.
	// form_render is now handled in the child DocType handler below.

	validate_overtime_dates_in_range(frm) {
		const from = frm.doc.from_date;
		const to = frm.doc.to_date;
		if (!from || !to) return;

		for (const row of frm.doc.bulk_overtime_entries || []) {
			const ot = row.overtime_date;
			if (!ot) continue;

			if (ot < from || ot > to) {
				frappe.validated = false;
				frappe.throw(
					__(
						"Row {0}: Overtime Date must be between {1} and {2}.",
						[
							row.idx,
							frappe.datetime.str_to_user(from),
							frappe.datetime.str_to_user(to),
						]
					)
				);
			}
		}
	},

	department(frm) {
		frm.events.clear_entries(frm);
		frm.set_value("shift_approver", "");
	},
	designation(frm) { frm.events.clear_entries(frm); },
	from_date(frm) {
		frm.events.clear_entries(frm);
		frm.events.set_overtime_date_limits(frm);
		frm.events.set_verification_intro(frm);
	},
	to_date(frm) {
		frm.events.clear_entries(frm);
		frm.events.set_overtime_date_limits(frm);
		frm.events.set_verification_intro(frm);
	},

	bulk_overtime_entries_add(frm) {
		frm.events.set_overtime_date_limits(frm);
	},

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

frappe.ui.form.on("Bulk Overtime Entry", {
	// ADDED: form_render fires each time a child row is expanded/opened in the grid.
	// This is the correct Frappe v15/v16 hook for applying datepicker limits per row.
	form_render(frm, cdt, cdn) {
		const grid_row = frm.fields_dict.bulk_overtime_entries.grid.get_row(cdn);
		const field = grid_row?.on_grid_fields_dict?.overtime_date;
		if (field?.$input) {
			frm.events.apply_overtime_datepicker(field.$input, frm);
		}
	},

	overtime_date(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const ot = row.overtime_date;
		const from = frm.doc.from_date;
		const to = frm.doc.to_date;
		if (!ot || !from || !to) return;

		if (ot < from || ot > to) {
			frappe.msgprint({
				message: __(
					"Overtime Date must be between {0} and {1}.",
					[
						frappe.datetime.str_to_user(from),
						frappe.datetime.str_to_user(to),
					]
				),
				indicator: "red",
			});
			frappe.model.set_value(cdt, cdn, "overtime_date", null);
		}
	},
});