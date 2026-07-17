// Biometric store-keeper verification for Stock Entry transfers.
// Backs the "Biometric Verification" custom fields created in
// upande_ta/upande_ta/overrides/stock_entry.py.
//
// Verification is automatic and server-driven; SUBMISSION is always confirmed
// by a human through a Yes/No modal. The Biometric Setting toggle
// `enable_automatic_submission_on_stock_entry` only decides how that modal is
// triggered:
//   • ENABLED  → the modal pops automatically once the entry is verified
//                (on save, or when a live scan lands on the open form).
//   • DISABLED → the modal is raised only when the store-keeper clicks the
//                "Check Biometric Log" / "Submit Stock Entry" button.
// Clicking Yes submits; No leaves it as a verified draft.

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
		setup_realtime(frm);
		setup_bio_poller(frm);
		// Whenever we see a verified draft (from a save, a live scan, or just
		// opening it), offer to submit — see maybe_offer_submit.
		maybe_offer_submit(frm);
	},

	after_save(frm) {
		if (!frm.doc.requires_biometric) return;
		render_status_badge(frm);
		render_verify_button(frm);
		maybe_offer_submit(frm);
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
		frm.__bio_auto_prompted = false;
		frm.__bio_modal_open = false;
		render_status_badge(frm);
		render_verify_button(frm);
	},

	// Soft-block: escalation warning instead of a hard stop, only for a MANUAL
	// standard submit while still unverified. Our own submit goes through
	// frappe.client.submit (below), which does not fire this client trigger.
	before_submit(frm) {
		if (!frm.doc.requires_biometric) return;
		if (is_verified(frm)) return;
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

// "Verified" is only real once it is recorded (biometric_verified_at set);
// a bare biometric_status === "Verified" with no timestamp is stale/unsaved.
function is_verified(frm) {
	return frm.doc.biometric_status === "Verified" && !!frm.doc.biometric_verified_at;
}

// ── Verify button ─────────────────────────────────────────────────────────────
// Always labelled "Check Biometric Log". When not yet verified it re-checks the
// scan (then raises the modal); when already verified it raises the modal
// directly. This is the manual trigger, and the only submit path when automatic
// submission is off.

function render_verify_button(frm) {
	frm.remove_custom_button(__("Check Biometric Log"));
	if (frm.is_new() || frm.doc.docstatus !== 0) return;

	frm.add_custom_button(__("Check Biometric Log"), () => {
		if (is_verified(frm)) {
			show_submit_modal(frm);
			return;
		}
		if (!frm.doc.bio_employee) {
			frappe.throw(__("Please select the Employee (Receiving) first."));
			return;
		}
		// Persist any pending edits first; that save may itself verify.
		if (frm.is_dirty()) {
			frm.save().then(() => run_manual_check(frm));
		} else {
			run_manual_check(frm);
		}
	}).addClass("btn-primary");
}

// Listen for the server's "verified" nudge (fired when a Biometric Log for the
// receiving employee is inserted/saved) and surface the submit modal.
function setup_realtime(frm) {
	frappe.realtime.off("biometric_stock_entry_verified");
	frappe.realtime.on("biometric_stock_entry_verified", (data) => {
		if (!data || !cur_frm || cur_frm.doctype !== "Stock Entry") return;
		if (data.stock_entry !== cur_frm.doc.name) return;
		sync_bio_then_offer(cur_frm);
	});
}

// Fallback so the popup NEVER fails to show after an insert/save on the
// Biometric Log: while a requires_biometric draft is open and unverified, poll
// the server; the moment it flips to Verified, surface the modal. Realtime
// (above) is the fast path; this guarantees delivery even if realtime is down.
function setup_bio_poller(frm) {
	if (frm.__bio_poller) {
		clearInterval(frm.__bio_poller);
		frm.__bio_poller = null;
	}
	if (frm.is_new() || frm.doc.docstatus !== 0 || !frm.doc.requires_biometric) return;
	if (is_verified(frm)) return;

	frm.__bio_poller = setInterval(() => {
		// Stop if we navigated away or the doc is no longer a matching draft.
		if (!cur_frm || cur_frm.doc.name !== frm.doc.name || cur_frm.doc.docstatus !== 0) {
			clearInterval(frm.__bio_poller);
			frm.__bio_poller = null;
			return;
		}
		frappe.db
			.get_value("Stock Entry", frm.doc.name, [
				"biometric_status",
				"biometric_verified_at",
			])
			.then((r) => {
				const v = (r && r.message) || {};
				if (v.biometric_status === "Verified" && v.biometric_verified_at) {
					clearInterval(frm.__bio_poller);
					frm.__bio_poller = null;
					sync_bio_then_offer(frm);
				}
			});
	}, 5000);
}

// Bring the biometric fields up to date, then offer the submit modal. Reloads
// when clean; when dirty, patches just the biometric fields in-memory so unsaved
// edits are preserved (and refresh/maybe_offer_submit still run).
function sync_bio_then_offer(frm) {
	if (!frm.is_dirty()) {
		frm.reload_doc(); // refresh() then runs maybe_offer_submit
		return;
	}
	frappe.db
		.get_value("Stock Entry", frm.doc.name, [
			"biometric_status",
			"biometric_verified_at",
			"matched_biometric_log",
		])
		.then((r) => {
			const v = (r && r.message) || {};
			if (v.biometric_status) frm.doc.biometric_status = v.biometric_status;
			if (v.biometric_verified_at) frm.doc.biometric_verified_at = v.biometric_verified_at;
			if (v.matched_biometric_log) frm.doc.matched_biometric_log = v.matched_biometric_log;
			render_status_badge(frm);
			render_verify_button(frm);
			maybe_offer_submit(frm);
		});
}

function run_manual_check(frm) {
	// The preceding save may already have verified it.
	if (is_verified(frm)) {
		show_submit_modal(frm);
		return;
	}

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
							? __("Verified — {0} scanned {1} seconds ago", [res.employee, res.seconds])
							: __("Verified — {0}", [res.employee]),
						indicator: "green",
					},
					7
				);
				// Reflect the Verified state, then raise the submit modal.
				frm.reload_doc().then(() => show_submit_modal(frm));
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

// ── Submit confirmation modal ─────────────────────────────────────────────────

let _auto_submit_enabled = null;

function get_auto_submit_enabled() {
	if (_auto_submit_enabled !== null) return Promise.resolve(_auto_submit_enabled);
	return frappe.db
		.get_single_value("Biometric Setting", "enable_automatic_submission_on_stock_entry")
		.then((v) => {
			_auto_submit_enabled = Boolean(Number(v));
			return _auto_submit_enabled;
		});
}

// Auto path: whenever this is a verified draft, raise the submit modal — but
// only when automatic submission is enabled. When disabled, the button raises
// it instead. One-shot per form load (__bio_auto_prompted); "No" reverts to
// Pending, so a verified draft never lingers to be re-prompted.
function maybe_offer_submit(frm) {
	if (!frm.doc.requires_biometric || frm.doc.docstatus !== 0) return;
	if (!is_verified(frm)) return;
	if (frm.__bio_auto_prompted) return;

	get_auto_submit_enabled().then((enabled) => {
		if (!enabled) return;
		if (frm.__bio_auto_prompted) return;
		frm.__bio_auto_prompted = true;
		show_submit_modal(frm);
	});
}

function show_submit_modal(frm) {
	if (frm.doc.docstatus !== 0 || !is_verified(frm)) return;
	if (frm.__bio_modal_open) return;
	frm.__bio_modal_open = true;

	frappe.confirm(
		__("Biometric verification is complete for {0}. Submit this stock entry now?", [
			frm.doc.bio_employee_name || frm.doc.bio_employee,
		]),
		() => {
			frm.__bio_modal_open = false;
			submit_bypassing_check(frm);
		},
		() => {
			// No: don't leave a verified draft in limbo — send it back to Pending.
			frm.__bio_modal_open = false;
			revert_to_pending(frm);
		}
	);
}

function revert_to_pending(frm) {
	frappe.call({
		method: "upande_ta.upande_ta.overrides.stock_entry.revert_biometric",
		args: { stock_entry: frm.doc.name },
		callback() {
			frm.__bio_auto_prompted = false;
			frm.reload_doc();
		},
	});
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
			// Submission failed (e.g. insufficient stock). Frappe already shows the
			// error; don't leave a verified draft in limbo — revert to Pending so
			// it must be re-verified once the issue (stock) is resolved.
			frappe.show_alert(
				{
					message: __(
						"Submission failed — status reset to Pending. Resolve the issue (e.g. insufficient stock) and verify again."
					),
					indicator: "red",
				},
				8
			);
			revert_to_pending(frm);
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

	// Show "Verified" only once it is actually recorded (biometric_verified_at
	// has a value). Until then it is always Pending — never a stale/unsaved
	// Verified carried over from a copy/amend or an in-memory set.
	let status = frm.doc.biometric_status || "Pending";
	if (status === "Verified" && !frm.doc.biometric_verified_at) {
		status = "Pending";
	}
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
