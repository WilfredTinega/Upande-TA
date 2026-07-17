// Biometric store-keeper verification for Stock Entry transfers.
// Backs the "Biometric Verification" custom fields created in
// upande_ta/upande_ta/overrides/stock_entry.py.
//
// Verification is fully AUTOMATIC and server-driven (no button):
//   • On save, Stock Entry.validate checks for a fresh Biometric Log; if it
//     verifies, this file auto-submits the entry (no prompt).
//   • When a scan lands afterwards, Biometric Logs.after_insert verifies AND
//     submits the recent draft server-side, then pushes it to the open form.
// This file also renders the status pill badge and keeps a soft-block if
// someone tries to submit manually while still unverified.

frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		if (!frm.doc.requires_biometric) return;

		if (frm.is_new()) {
			// New entry: always Pending, and the status is only shown after the
			// first save (no badge on the unsaved form).
			if (frm.doc.biometric_status !== "Pending") {
				frm.set_value("biometric_status", "Pending");
			}
			return;
		}

		render_status_badge(frm);
		render_verify_button(frm);
	},

	after_save(frm) {
		if (!frm.doc.requires_biometric) return;
		render_status_badge(frm);
		// Verification happened during validate — submit automatically.
		if (frm.doc.biometric_status === "Verified" && frm.doc.docstatus === 0) {
			auto_submit(frm);
		} else {
			render_verify_button(frm);
		}
	},

	requires_biometric(frm) {
		if (!frm.is_new() && frm.doc.requires_biometric) {
			render_status_badge(frm);
			render_verify_button(frm);
		}
	},

	bio_employee(frm) {
		if (!frm.doc.bio_employee) return;
		// Changing the receiving employee resets verification; the server
		// re-verifies on the next save.
		frm.set_value("biometric_status", "Pending");
		frm.set_value("biometric_verified_at", "");
		frm.set_value("matched_biometric_log", "");
		render_status_badge(frm);
	},

	// Soft-block: escalation warning instead of a hard stop, only for a MANUAL
	// submit while still unverified. Automatic submits go through frappe.client
	// .submit (below), which does not fire this client trigger.
	before_submit(frm) {
		if (!frm.doc.requires_biometric) return;
		if (frm.doc.biometric_status === "Verified") return;
		if (frm._bypass_bio_check) return;

		// Cancel the default submit + its built-in "Permanently Submit?" dialog.
		frappe.validated = false;

		frappe.confirm(
			__(
				"<b>No biometric verification has been completed.</b><br><br>" +
					"Are you sure you want to submit this stock entry without " +
					"biometric verification?<br>This stock entry might be escalated."
			),
			() => submit_bypassing_check(frm)
		);
	},
});

// ── Manual fallback button ───────────────────────────────────────────────────
// Automatic verification is the norm; this button lets the store-keeper re-run
// the exact same server-side verify → submit flow on demand if the automatic
// path did not catch the scan.

function render_verify_button(frm) {
	frm.remove_custom_button(__("Check Biometric Log"));
	if (frm.is_new() || frm.doc.docstatus !== 0) return;
	if (frm.doc.biometric_status === "Verified") return;

	frm.add_custom_button(__("Check Biometric Log"), () => {
		if (!frm.doc.bio_employee) {
			frappe.throw(__("Please select the Employee (Receiving) first."));
			return;
		}
		// Persist any pending edits first; that save may itself auto-verify.
		if (frm.is_dirty()) {
			frm.save().then(() => run_manual_check(frm));
		} else {
			run_manual_check(frm);
		}
	}).addClass("btn-primary");
}

function run_manual_check(frm) {
	if (frm.doc.biometric_status === "Verified") return; // save already handled it
	frappe.dom.freeze(__("Checking biometric log…"));

	frappe.call({
		method: "upande_ta.upande_ta.overrides.stock_entry.check_biometric_log",
		args: { stock_entry: frm.doc.name },
		callback(r) {
			frappe.dom.unfreeze();
			const res = r.message || {};

			if (res.status === "verified") {
				frappe.show_alert(
					{
						message: res.seconds
							? __("Verified — {0} scanned {1} seconds ago{2}", [
									res.employee,
									res.seconds,
									res.submitted ? __(" — submitted") : "",
							  ])
							: __("Verified{0}", [res.submitted ? __(" — submitted") : ""]),
						indicator: "green",
					},
					7
				);
				frm.reload_doc();
			} else if (res.status === "no_log") {
				frappe.msgprint({
					title: __("No Biometric Log Found"),
					message: __(
						"No biometric record exists at all for <b>{0}</b>.<br><br>" +
							"Please ensure the employee scans on the store device first.",
						[res.employee]
					),
					indicator: "red",
				});
			} else if (res.status === "too_old") {
				frappe.msgprint({
					title: __("Biometric Scan Too Old"),
					message: __(
						"The last scan for <b>{0}</b> was <b>{1} minute(s) ago</b> — outside the {2}-minute window.<br><br>" +
							"Log name: {3}<br>" +
							"Scanned at: {4}<br><br>" +
							"Ask the employee to scan again on the store device, then click <b>Check Biometric Log</b>.",
						[res.employee, res.minutes, res.window, res.log, res.time]
					),
					indicator: "orange",
				});
			} else if (res.status === "not_required") {
				frappe.show_alert(
					{ message: __("Biometric verification is not required for this entry."), indicator: "blue" },
					5
				);
			}
		},
		error() {
			frappe.dom.unfreeze();
		},
	});
}

// ── Automatic submit ─────────────────────────────────────────────────────────

function auto_submit(frm) {
	frappe.show_alert(
		{ message: __("Biometric verified — submitting…"), indicator: "green" },
		5
	);
	submit_bypassing_check(frm);
}

function submit_bypassing_check(frm) {
	frm._bypass_bio_check = true;
	frappe.call({
		method: "frappe.client.submit",
		args: { doc: frm.doc },
		callback(r) {
			frm._bypass_bio_check = false;
			if (r.docs || r.message) {
				frappe.model.sync(r.docs || [r.message]);
				frm.refresh();
			}
		},
		error() {
			frm._bypass_bio_check = false;
		},
	});
}

// ── Status badge ─────────────────────────────────────────────────────────────
// We hide the raw biometric_status input and replace it with a styled pill
// badge, so the value never appears twice.

function render_status_badge(frm) {
	frm.fields_dict["biometric_status"]?.$wrapper
		.find(".control-input-wrapper, .control-value, input, select")
		.hide();

	frm.fields_dict["biometric_status"]?.$wrapper.find(".bio-badge").remove();

	const status = frm.doc.biometric_status || "Pending";
	const map = {
		Verified: { bg: "#E1F5EE", color: "#0F6E56", dot: "#1D9E75", label: "Verified" },
		Failed: { bg: "#FCEBEB", color: "#791F1F", dot: "#E24B4A", label: "Failed" },
		Pending: { bg: "#FAEEDA", color: "#633806", dot: "#BA7517", label: "Pending" },
	};
	const s = map[status] || map["Pending"];

	const badge = $(`
		<div class="bio-badge" style="
			display: inline-flex; align-items: center; gap: 5px;
			background: ${s.bg}; color: ${s.color};
			font-size: 12px; font-weight: 500;
			padding: 3px 10px; border-radius: 20px;
			margin-bottom: 8px;">
			<span style="width:7px;height:7px;border-radius:50%;
						 background:${s.dot};display:inline-block;"></span>
			${s.label}
		</div>
	`);

	frm.fields_dict["biometric_status"]?.$wrapper.append(badge);
}
