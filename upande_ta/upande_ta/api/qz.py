# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt
"""QZ Tray request signing, so a hosted site can print silently to a local printer.

QZ Tray is only reachable from the browser (``wss://localhost:8181``), never from
the server, so the printing chain is: this site's page -> QZ Tray on the same
computer -> the printer. Nothing has to reach into the LAN.

QZ Tray refuses to print unattended unless every call is signed. The browser must
not hold the signing key, so qz-tray.js hands us the SHA-256 hex digest of
``{call, params, timestamp}`` and we return an RSA-SHA512 signature over it; the
browser only ever sees the public certificate. QZ Tray verifies the signature,
then checks whether it trusts the certificate: install the same certificate as
``override.crt`` in QZ Tray's install directory (``/opt/qz-tray`` on Linux) and
the allow/deny dialog stops appearing.

Configure both halves of the key pair in the site config:

	"qz_certificate": "-----BEGIN CERTIFICATE-----\\n...",
	"qz_private_key": "-----BEGIN PRIVATE KEY-----\\n..."

Either value may instead be an absolute path to the PEM file on the server.
"""

import base64
import os

import frappe
from frappe import _

# qz-tray.js signs a hex digest; anything much larger is not a QZ payload.
MAX_SIGN_BYTES = 4096

# Realtime event the bridge page listens on for server-initiated print jobs.
PRINT_EVENT = "upande_ta_qz_print"

# Keyed by site: the PEMs live in the site config, one key pair per site.
_KEY_CACHE = {}


def _read_pem(conf_key: str) -> str:
	"""PEM text from the site config, or from the file it points at."""
	value = (frappe.conf.get(conf_key) or "").strip()
	if not value:
		return ""
	if value.startswith("/") and os.path.isfile(value):
		with open(value) as handle:
			return handle.read().strip()
	return value


def _private_key():
	site = frappe.local.site
	cached = _KEY_CACHE.get(site)
	if cached:
		return cached

	pem = _read_pem("qz_private_key")
	if not pem:
		frappe.throw(_("qz_private_key is not set in the site config"))

	from cryptography.hazmat.primitives.serialization import load_pem_private_key

	try:
		key = load_pem_private_key(pem.encode(), password=None)
	except Exception:
		frappe.throw(_("qz_private_key in the site config is not a readable PEM private key"))

	_KEY_CACHE[site] = key
	return key


@frappe.whitelist()
def certificate() -> str:
	"""Public certificate the bridge page presents to QZ Tray."""
	cert = _read_pem("qz_certificate")
	if not cert:
		frappe.throw(_("qz_certificate is not set in the site config"))
	return cert


@frappe.whitelist()
def sign(data: str) -> str:
	"""Sign one QZ Tray call. ``data`` is the digest qz-tray.js asked us to sign."""
	if not isinstance(data, str) or not data.strip():
		frappe.throw(_("Nothing to sign"))
	if len(data) > MAX_SIGN_BYTES:
		frappe.throw(_("Payload too large to sign"))

	from cryptography.hazmat.primitives import hashes
	from cryptography.hazmat.primitives.asymmetric import padding

	# SHA512 here must match qz.security.setSignatureAlgorithm("SHA512") on the page.
	signature = _private_key().sign(data.encode(), padding.PKCS1v15(), hashes.SHA512())
	return base64.b64encode(signature).decode()


def send_to_terminal(data, printer: str | None = None, user: str | None = None, after_commit=True):
	"""Push a print job to a bridge page from server code.

	``data`` is a QZ Tray print payload (a list of raw command strings, or
	``[{"type": "html", "format": "plain", "data": "<div>..."}]``). Only browsers
	that have a printer saved on the Printer Settings page act on the event, so
	this is a no-op unless a terminal is actually open.

	``after_commit`` keeps a rolled back transaction from printing paper.
	"""
	if not data:
		return

	frappe.publish_realtime(
		PRINT_EVENT,
		{"printer": printer, "data": data},
		user=user or frappe.session.user,
		after_commit=after_commit,
	)
