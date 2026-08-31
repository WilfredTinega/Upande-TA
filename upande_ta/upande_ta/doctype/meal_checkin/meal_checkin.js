// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

// Auto-print a centered 72mm thermal meal receipt on save, on the kitchen
// terminal that has the TM-T20III.
//
// Preferred path is QZ Tray: save the printer once on /printer-settings and the
// receipt prints silently, with the site signing every request (see
// upande_ta/upande_ta/api/qz.py). Where QZ Tray is not installed this falls back
// to the browser print dialog, which is only silent under Chrome
// --kiosk-printing with the TM-T20III as the default printer.
frappe.ui.form.on("Meal Checkin", {
	after_save: function (frm) {
		var meals = ["Breakfast", "Lunch", "Supper", "Dinner"];
		var d = frm.doc;
		if (!d.log_type || meals.indexOf(d.log_type) === -1) return;

		frappe.db.get_value("Employee", d.employee, "employee_name").then(function (r) {
			var emp =
				r && r.message && r.message.employee_name
					? r.message.employee_name
					: d.employee || "-";
			var payroll = d.payroll_number || d.employee || "-";
			var ts = d.time ? frappe.datetime.str_to_user(d.time) : "-";
			var tqr = (d.time || "").replace(/[^0-9]/g, "").slice(0, 12);
			var qr =
				"https://api.qrserver.com/v1/create-qr-code/?size=150x150&margin=0&data=" +
				encodeURIComponent(
					"KENTROUT_" + d.name + "_" + payroll + "_" + d.log_type + "_" + tqr
				);

			var css =
				"<style>" +
				"@page{size:72mm auto;margin:0}" +
				"*{box-sizing:border-box}" +
				"body{margin:0;padding:0;width:72mm;font-family:Arial,Helvetica,sans-serif;color:#000}" +
				".r{width:72mm;padding:3mm 3mm;text-align:center}" +
				".hdr{font-size:15px;font-weight:800;letter-spacing:1px}" +
				".sub{font-size:10px;margin-bottom:4px}" +
				".meal{font-size:20px;font-weight:800;margin:6px 0}" +
				"table{margin:6px auto;border-collapse:collapse;font-size:12px}" +
				"td{padding:2px 8px;vertical-align:top}" +
				"td.k{text-align:left;color:#333}" +
				"td.v{text-align:left;font-weight:700;word-break:break-word}" +
				".foot{font-size:9px;color:#333;margin-top:6px;word-break:break-all}" +
				"hr{border:none;border-top:1px dashed #000;margin:5px 0}" +
				"img{display:block;margin:8px auto 0}" +
				"</style>";

			var body =
				'<div class="r">' +
				'<div class="hdr">KENTROUT FARM</div>' +
				'<div class="sub">Meal Receipt</div>' +
				"<hr>" +
				'<div class="meal">' +
				String(d.log_type).toUpperCase() +
				"</div>" +
				"<table>" +
				'<tr><td class="k">Employee</td><td class="v">' +
				emp +
				"</td></tr>" +
				'<tr><td class="k">Payroll No</td><td class="v">' +
				payroll +
				"</td></tr>" +
				'<tr><td class="k">Time</td><td class="v">' +
				ts +
				"</td></tr>" +
				"</table>" +
				'<img src="' +
				qr +
				'" width="128" height="128">' +
				"<hr>" +
				'<div class="foot">Receipt ' +
				d.name +
				"</div>" +
				"</div>";

			var html =
				'<!doctype html><html><head><meta charset="utf-8">' +
				css +
				"</head><body>" +
				body +
				"</body></html>";

			var bridge = window.upande_ta && window.upande_ta.qz;
			if (bridge && bridge.isTerminal()) {
				bridge.printHtml(html).catch(function (err) {
					console.warn("QZ Tray receipt failed, falling back to the print dialog", err);
					dialogPrint(html);
				});
				return;
			}

			dialogPrint(html);
		});
	},
});

// Browser print dialog fallback: render the receipt in a hidden iframe and print
// it, once the QR image has settled.
function dialogPrint(html) {
	var ifr = document.createElement("iframe");
	ifr.style.position = "fixed";
	ifr.style.right = "0";
	ifr.style.bottom = "0";
	ifr.style.width = "0";
	ifr.style.height = "0";
	ifr.style.border = "0";
	document.body.appendChild(ifr);

	var idoc = ifr.contentWindow.document;
	idoc.open();
	idoc.write(html);
	idoc.close();

	var done = false;
	var doPrint = function () {
		if (done) return;
		done = true;
		try {
			ifr.contentWindow.focus();
			ifr.contentWindow.print();
		} catch (e) {
			console.error("Meal receipt print failed", e);
		}
		setTimeout(function () {
			if (ifr && ifr.parentNode) ifr.parentNode.removeChild(ifr);
		}, 2000);
	};

	var img = idoc.images && idoc.images[0];
	if (img) {
		img.onload = doPrint;
		img.onerror = doPrint;
	}
	setTimeout(doPrint, 1500);
}
