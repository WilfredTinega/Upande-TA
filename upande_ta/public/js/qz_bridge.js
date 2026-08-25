// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

// Signed QZ Tray bridge.
//
// QZ Tray listens on wss://localhost:8181 and only accepts calls from the
// browser, so a hosted site prints to a local printer through the operator's own
// tab -- nothing needs to reach into the site network. Every call is signed by
// the site (see upande_ta/upande_ta/api/qz.py); the browser only handles the
// public certificate. Install that same certificate as override.crt in QZ Tray's
// install directory and printing is silent, with no allow/deny dialog.
//
//   upande_ta.qz.printHtml(html)              // 72mm receipt, saved printer
//   upande_ta.qz.printRaw([escpos, ...])      // raw ESC/POS commands
//   upande_ta.qz.printer() / setPrinter(name) // per-computer printer choice
//
// A computer becomes a print terminal by saving a printer on /printer-settings.
// Those browsers also listen for jobs pushed from server code via
// qz.send_to_terminal(), so printing does not depend on who submitted the form.

/* global qz */

window.upande_ta = window.upande_ta || {};

window.upande_ta.qz = (function () {
	var LIB_URL = "/assets/upande_ta/js/lib/qz-tray.js";
	var PRINTER_KEY = "meal_receipt_printer";
	var CERT_METHOD = "upande_ta.upande_ta.api.qz.certificate";
	var SIGN_METHOD = "upande_ta.upande_ta.api.qz.sign";
	var PRINT_EVENT = "upande_ta_qz_print";
	var RECEIPT_WIDTH_MM = 72;

	var loading = null;
	var readying = null;
	var listening = false;

	function server(method, args) {
		if (!window.frappe || !frappe.call) {
			return Promise.reject(new Error("frappe.call is unavailable on this page"));
		}
		return frappe.call({ method: method, args: args || {} }).then(function (r) {
			if (!r || !r.message) {
				throw new Error(method + " returned nothing");
			}
			return r.message;
		});
	}

	function loadLib() {
		if (window.qz) {
			return Promise.resolve(window.qz);
		}
		if (loading) {
			return loading;
		}
		loading = new Promise(function (resolve, reject) {
			var tag = document.createElement("script");
			tag.src = LIB_URL;
			tag.onload = function () {
				window.qz
					? resolve(window.qz)
					: reject(new Error("qz-tray.js loaded but qz is undefined"));
			};
			tag.onerror = function () {
				loading = null;
				reject(new Error("could not load " + LIB_URL));
			};
			document.head.appendChild(tag);
		});
		return loading;
	}

	function configure() {
		// Resolved server side so the signing key never reaches the browser.
		qz.security.setCertificatePromise(function (resolve, reject) {
			server(CERT_METHOD).then(resolve).catch(reject);
		});

		// Must match the algorithm api/qz.py signs with.
		qz.security.setSignatureAlgorithm("SHA512");

		qz.security.setSignaturePromise(function (toSign) {
			return function (resolve, reject) {
				server(SIGN_METHOD, { data: toSign })
					.then(function (signature) {
						resolve(String(signature).trim());
					})
					.catch(reject);
			};
		});
	}

	// Resolves with a connected qz. Memoised, so every caller shares one socket.
	function ready() {
		if (window.qz && qz.websocket.isActive()) {
			return Promise.resolve(window.qz);
		}
		if (readying) {
			return readying;
		}
		readying = loadLib()
			.then(function () {
				if (qz.websocket.isActive()) {
					return window.qz;
				}
				configure();
				// Default host list is localhost / localhost.qz.io, both covered by
				// QZ Tray's own certificate. Do not pass an explicit host without
				// qz.websocket.setUsingSurf(false) -- surf rewrites it to
				// localhost.qz.surf, which that certificate does not cover.
				return qz.websocket.connect({ retries: 2, delay: 1 }).then(function () {
					return window.qz;
				});
			})
			.catch(function (err) {
				readying = null;
				throw err;
			});
		return readying;
	}

	function printer() {
		try {
			return window.localStorage.getItem(PRINTER_KEY) || "";
		} catch (e) {
			return "";
		}
	}

	function setPrinter(name) {
		try {
			window.localStorage.setItem(PRINTER_KEY, name);
			return true;
		} catch (e) {
			return false;
		}
	}

	function isTerminal() {
		return !!printer();
	}

	function resolvePrinter(name) {
		var chosen = name || printer();
		if (chosen) {
			return Promise.resolve(chosen);
		}
		return ready().then(function () {
			return qz.printers.getDefault();
		});
	}

	function printers() {
		return ready().then(function () {
			return qz.printers.find();
		});
	}

	function print(data, name, options) {
		if (!data || !data.length) {
			return Promise.reject(new Error("nothing to print"));
		}
		return ready()
			.then(function () {
				return resolvePrinter(name);
			})
			.then(function (target) {
				if (!target) {
					throw new Error("no printer selected and no system default");
				}
				return qz.print(qz.configs.create(target, options || {}), data);
			});
	}

	function printRaw(commands, name) {
		return print(commands, name);
	}

	function printHtml(html, name, widthMm) {
		var width = widthMm || RECEIPT_WIDTH_MM;
		return print([{ type: "html", format: "plain", data: html }], name, {
			units: "mm",
			size: { width: width, height: null },
			margins: 0,
		});
	}

	// Print jobs raised by server code (see qz.send_to_terminal). Only computers
	// that have a printer saved arm this, so a job cannot surprise a random tab.
	function listen() {
		if (listening || !window.frappe || !frappe.realtime || !frappe.realtime.on) {
			return false;
		}
		listening = true;
		frappe.realtime.on(PRINT_EVENT, function (job) {
			if (!job || !job.data) {
				return;
			}
			print(job.data, job.printer).catch(function (err) {
				console.error("QZ print job failed", err);
			});
		});
		return true;
	}

	// Arm the terminal quietly, after the page has settled.
	if (typeof window !== "undefined") {
		setTimeout(function () {
			if (!isTerminal()) {
				return;
			}
			listen();
			ready().catch(function (err) {
				console.warn("QZ Tray not ready on this terminal:", err.message || err);
			});
		}, 1500);
	}

	return {
		PRINTER_KEY: PRINTER_KEY,
		PRINT_EVENT: PRINT_EVENT,
		ready: ready,
		printer: printer,
		setPrinter: setPrinter,
		isTerminal: isTerminal,
		printers: printers,
		print: print,
		printRaw: printRaw,
		printHtml: printHtml,
		listen: listen,
	};
})();
