# Copyright (c) 2026, Upande LTD and contributors
# For license information, please see license.txt

"""Employee Exchange — staff working at another instance for a while.

Several instances (HQ, Kaitet, Mona, ...) are linked to each other. Someone on
an exchange program is kept in **Exchange Employee**, never in Employee, and
their punches land in **Exchange Checkin**, never in Employee Checkin, so the
host's attendance sheet and payroll never count them.

* **Exchange Setting** holds all of it, one tab each: this instance's code and
  the token the others send with (:func:`generate_api_key`); the **Instances**
  table, one row per instance this one talks to (its URL, the token *it*
  issued, the default company here for its employees, what to send it); and
  the **Company Map**, which places each instance's companies here.
* An Exchange Employee is created from their home instance and payroll number;
  everything else about the person is fetched from home
  (:func:`fetch_home_record`), so it is never typed twice.
* Records made here are queued per destination in **Exchange Sync Log** and
  posted to ``upande_ta.upande_ta.api.exchange.receive`` on the other side. A record
  received from elsewhere is never sent on again: only an instance's own
  records leave it, so three linked instances cannot bounce a punch between
  them. ``origin_id`` (``<instance>:<name>``) makes a re-send an update.

The device side reuses the enrolment code Biometric User uses — the same
USERINFO and BIODATA commands, through the same Node-RED endpoint.
"""

import json

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

#: Role given to the integration user; the receive endpoints admit it.
ROLE = "Exchange Sync"

#: Seconds to wait on another instance before calling it unreachable.
TIMEOUT = 20

#: Doctypes the other side's ``receive`` accepts.
RECEIVABLE = ("Exchange Employee", "Exchange Checkin", "Employee Checkin")

#: What an Exchange Employee carries to the other instances. The host fields
#: (company, unit, department) are the sender's; each receiver sets its own.
EMPLOYEE_FIELDS = (
	"home_instance",
	"payroll_number",
	"full_name",
	"home_employee_id",
	"id_number",
	"phone",
	"gender",
	"date_of_joining",
	"home_company",
	"home_department",
	"home_designation",
	"image",
	"device_pin",
	"status",
	"host_company",
	"program_start",
	"program_end",
	"source_instance",
	"origin_id",
)

TEMPLATE_FIELDS = (
	"source_device_sn",
	"received_on",
	"privilege",
	"card",
	"vice_card",
	"password",
	"user_group",
	"timezone_group",
	"verify_mode",
	"start_datetime",
	"end_datetime",
	*(
		f"{prefix}_{field}"
		for prefix in ("fp", "face", "palm")
		for field in ("valid", "type_code", "bio_no", "bio_index", "major_ver", "minor_ver", "size")
	),
	"fingerprint_template",
	"face_template",
	"palm_template",
)

CHECKIN_FIELDS = (
	"employee_name",
	"payroll_number",
	"home_instance",
	"time",
	"log_type",
	"device_sn",
	"device_location",
	"source_instance",
	"origin_id",
	"employee_origin_id",
)

#: Employee fields tried, in order, for the ID / passport number a home
#: instance sends back. Sites name it differently.
ID_NUMBER_FIELDS = ("custom_id_number", "custom_national_id", "national_id", "id_number", "passport_number")


class RemoteError(Exception):
	"""Another instance could not be reached, or refused the request."""


def instances(enabled_only: bool = False) -> list:
	"""The Instances rows of Exchange Setting."""
	rows = list(frappe.get_single("Exchange Setting").instances)
	return [r for r in rows if cint(r.enabled)] if enabled_only else rows


def get_instance(code: str):
	row = next((r for r in instances() if r.instance_code == (code or "").strip().upper()), None)
	if not row:
		frappe.throw(_("{0} is not in the Instances tab of Exchange Setting.").format(frappe.bold(code)))
	return row


def instance_exists(code: str) -> bool:
	return any(r.instance_code == code for r in instances())


@frappe.whitelist()
def instance_codes() -> list:
	return [r.instance_code for r in instances()]


# ──────────────────────────────────────────────────────────────────────────
# This instance
# ──────────────────────────────────────────────────────────────────────────


def settings():
	return frappe.get_cached_doc("Exchange Setting")


def sync_status() -> dict:
	"""Counts per destination for the Sending tab."""
	rows = frappe.db.sql(
		"""select destination, status, count(*) as n from `tabExchange Sync Log`
		group by destination, status""",
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r.destination, {})[r.status] = r.n
	return out


def my_code() -> str:
	return (settings().instance_code or "").strip().upper()


def is_enabled() -> bool:
	return bool(cint(settings().enabled) and my_code())


def is_own(doc) -> bool:
	"""Whether ``doc`` was made on this instance (and so may be sent on)."""
	return not doc.get("source_instance") or doc.source_instance == my_code()


def ensure_role():
	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 0}).insert(
			ignore_permissions=True
		)


@frappe.whitelist()
def generate_api_key() -> dict:
	"""Give the integration user a fresh key and secret, and return the token
	the other instances paste into their Instances row for this one.

	The secret is returned once and never stored in readable form here."""
	frappe.only_for(("System Manager", "HR Manager"))
	setting = frappe.get_single("Exchange Setting")
	code = (setting.instance_code or "").strip().upper()
	if not code:
		frappe.throw(_("Set This Instance Code first."))

	ensure_role()
	user_id = setting.integration_user or _integration_user(code)
	user = frappe.get_doc("User", user_id)
	if not any(r.role == ROLE for r in user.roles):
		user.append("roles", {"role": ROLE})
	user.api_key = user.api_key or frappe.generate_hash(length=15)
	secret = frappe.generate_hash(length=15)
	user.api_secret = secret
	user.save(ignore_permissions=True)

	setting.integration_user = user.name
	setting.api_key = user.api_key
	setting.api_key_generated_on = now_datetime()
	setting.save(ignore_permissions=True)
	return {"api_key": user.api_key, "token": f"{user.api_key}:{secret}"}


def _integration_user(code: str) -> str:
	email = f"exchange-sync-{code.lower()}@upande-exchange.local"
	if frappe.db.exists("User", email):
		return email
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Exchange Sync",
			"last_name": code,
			"send_welcome_email": 0,
			"enabled": 1,
			"roles": [{"role": ROLE}],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return user.name


def require_sync_caller(*extra_roles):
	"""Refuse a request unless this instance takes part and the caller is
	the integration user (or someone trusted with the same)."""
	if not is_enabled():
		frappe.throw(_("Employee Exchange is switched off on this instance."), frappe.PermissionError)
	allowed = {ROLE, "System Manager", *extra_roles}
	if frappe.session.user == "Guest" or not allowed.intersection(frappe.get_roles()):
		frappe.throw(_("Not permitted to use Employee Exchange."), frappe.PermissionError)


# ──────────────────────────────────────────────────────────────────────────
# Calling another instance
# ──────────────────────────────────────────────────────────────────────────


def call_instance(instance, method: str, *, params=None, payload=None):
	"""Call ``upande_ta.upande_ta.api.exchange.<method>`` on another instance with the
	token it issued. Raises :class:`RemoteError` with a reason a person can act
	on; the technical detail goes to the Error Log."""
	inst = get_instance(instance) if isinstance(instance, str) else instance
	name = inst.instance_code
	api_key, secret = split_token(inst.get_password("api_token", raise_exception=False))
	if not api_key or not secret:
		raise RemoteError(_("Enter the API Key:Secret {0} gave you.").format(name))

	url = f"{inst.base_url.rstrip('/')}/api/method/upande_ta.upande_ta.api.exchange.{method}"
	headers = {"Authorization": f"token {api_key}:{secret}", "Accept": "application/json"}
	try:
		if payload is None:
			response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
		else:
			response = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
	except requests.exceptions.Timeout as e:
		_log_remote(name, method, url, e)
		raise RemoteError(
			_("{0} did not answer in time. Check that it is running, then try again.").format(name)
		) from None
	except requests.exceptions.SSLError as e:
		_log_remote(name, method, url, e)
		if "CERTIFICATE_VERIFY_FAILED" in str(e):
			raise RemoteError(
				_("{0}'s security certificate is not valid. Ask its administrator to renew it.").format(name)
			) from None
		raise RemoteError(
			_(
				"Could not make a secure connection to {0}. Check the Base URL (https or http), then try again."
			).format(name)
		) from None
	except requests.exceptions.RequestException as e:
		_log_remote(name, method, url, e)
		raise RemoteError(
			_("Could not connect to {0} at {1}. Check the Base URL and that {0} is running.").format(
				name, inst.base_url
			)
		) from None

	if response.status_code >= 400:
		_log_remote(name, method, url, f"{response.status_code} {response.reason}\n\n{response.text[:5000]}")
		raise RemoteError(_refusal(name, response)) from None
	try:
		return response.json().get("message")
	except ValueError:
		_log_remote(name, method, url, f"{response.status_code}, not JSON:\n\n{response.text[:5000]}")
		raise RemoteError(
			_("{0} answered, but not like an ERP site. Check the Base URL.").format(name)
		) from None


def split_token(token: str | None) -> tuple:
	"""``(key, secret)`` from ``key:secret``, also as copied with its
	``token`` prefix from Generate API Key."""
	token = (token or "").strip()
	if token.lower().startswith("token "):
		token = token[6:].strip()
	key, sep, secret = token.partition(":")
	return (key.strip(), secret.strip()) if sep else ("", "")


def _refusal(name: str, response) -> str:
	"""What to tell a person when another instance said no."""
	code = response.status_code
	if code in (401, 403):
		said = _server_message(response)
		if said and code == 403:
			return _("{0} refused: {1}").format(name, said)
		return _(
			"{0} did not accept the API Key:Secret. Generate a new token on {0} and enter it here."
		).format(name)
	if code == 404 or "Failed to get method" in _server_message(response):
		return _(
			"{0} does not have Employee Exchange. Install the latest Upande T&A there, or check the Base URL."
		).format(name)
	if code >= 500:
		return _("{0} hit an error. Its administrator can see the details in its Error Log.").format(name)
	said = _server_message(response)
	return _("{0} refused: {1}").format(name, said) if said else _("{0} refused the request.").format(name)


def _server_message(response) -> str:
	"""The message the other instance raised with frappe.throw, if any:
	those are written for people. Tracebacks are not."""
	try:
		messages = response.json().get("_server_messages")
		first = json.loads(json.loads(messages)[0]) if messages else {}
		return frappe.utils.strip_html(first.get("message", "")).strip()[:300]
	except (ValueError, TypeError, IndexError, AttributeError):
		return ""


def _log_remote(name: str, method: str, url: str, detail):
	# deferred: the request that hit this often rolls back right after
	frappe.log_error(
		title=f"Employee Exchange: {name} {method}"[:140],
		message=f"{url}\n\n{detail}",
		defer_insert=True,
	)


def _throw_remote(e: RemoteError):
	frappe.throw(str(e), title=_("Other Instance"))


# ──────────────────────────────────────────────────────────────────────────
# Fetching an employee from their home instance
# ──────────────────────────────────────────────────────────────────────────


def employee_for_lookup(payroll_number: str, company: str | None = None) -> dict | None:
	"""The home side of a lookup: the employee with that payroll number, with
	only what the other instance needs to know about them."""
	field = (settings().payroll_number_field or "attendance_device_id").strip()
	meta = frappe.get_meta("Employee")
	if not meta.has_field(field) and field != "name":
		frappe.throw(
			_("Employee has no field {0}. Fix Payroll Number Field in Exchange Setting.").format(field)
		)
	id_field = next((f for f in ID_NUMBER_FIELDS if meta.has_field(f)), None)
	fields = [
		"name",
		"employee_name",
		"gender",
		"date_of_joining",
		"company",
		"department",
		"designation",
		"cell_number",
		"image",
	]
	if id_field:
		fields.append(id_field)
	value = str(payroll_number).strip()
	scope = {"company": company} if company else {}
	rows = frappe.get_all("Employee", filters={field: value, **scope}, fields=fields, limit=2)
	if not rows and field != "name":
		# the Employee ID (HR-EMP-00003) works as well as the payroll number
		rows = frappe.get_all("Employee", filters={"name": value, **scope}, fields=fields, limit=2)
	if not rows:
		return None
	if len(rows) > 1:
		frappe.throw(_("More than one employee has payroll number {0} here.").format(payroll_number))
	row = rows[0]
	image = row.image or ""
	if image.startswith("/files/"):
		image = frappe.utils.get_url(image)
	elif not image.startswith("http"):
		image = ""
	return {
		"home_employee_id": row.name,
		"full_name": row.employee_name,
		"gender": row.gender,
		"date_of_joining": str(row.date_of_joining) if row.date_of_joining else None,
		"home_company": row.company,
		"home_department": row.department,
		"home_designation": row.designation,
		"phone": row.cell_number,
		"id_number": row.get(id_field) if id_field else None,
		"image": image,
	}


def templates_for_lookup(payroll_number: str, company: str | None = None) -> list:
	"""The home side of a template fetch: the biometric templates kept here
	(Biometric Template, one per device) for the employee a lookup finds."""
	found = employee_for_lookup(payroll_number, company)
	if not found:
		return []
	BioTemplate = frappe.qb.DocType("Bio Template")
	BiometricTemplate = frappe.qb.DocType("Biometric Template")
	fields = [f for f in TEMPLATE_FIELDS if f not in ("source_device_sn", "received_on")]
	rows = (
		frappe.qb.from_(BioTemplate)
		.join(BiometricTemplate)
		.on(BioTemplate.parent == BiometricTemplate.name)
		.select(
			BiometricTemplate.device_sn,
			BiometricTemplate.device_location,
			BioTemplate.modified,
			*[BioTemplate[f] for f in fields],
		)
		.where(BioTemplate.parenttype == "Biometric Template")
		.where(BioTemplate.employee == found["home_employee_id"])
		.where(BioTemplate.deleted == 0)
		.orderby(BioTemplate.modified)
	).run(as_dict=True)
	out = []
	for r in rows:
		if not any(cint(r.get(f"{p}_valid")) for p in ("fp", "face", "palm")):
			continue
		row = {f: _plain(r.get(f)) for f in fields}
		row["source_device_sn"] = r.device_sn
		row["device_location"] = r.device_location
		out.append(row)
	return out


def home_templates(doc) -> list:
	"""The templates the employee's home instance keeps for them, one per
	home device (with its location). Raises RemoteError."""
	try:
		rows = call_instance(
			doc.home_instance,
			"get_templates",
			params=_lookup_params(doc.home_instance, doc.payroll_number),
		)
	except RemoteError as e:
		if "does not have Employee Exchange" in str(e):
			# it answers the lookup, so only the template part is missing
			raise RemoteError(
				_("{0} does not send templates yet. Update Upande T&A there.").format(doc.home_instance)
			) from None
		raise
	return rows or []


def fetch_home_templates(doc, devices: list | None = None) -> int:
	"""Copy the employee's templates from their home instance onto ``doc``,
	one row per home device, replacing that device's row; only the home
	devices in ``devices`` when given. Returns how many rows changed.
	Raises RemoteError."""
	rows = home_templates(doc)
	if devices is not None:
		rows = [r for r in rows if r.get("source_device_sn") in set(devices)]
	changed = 0
	for data in rows:
		row = next((r for r in doc.templates if r.source_device_sn == data.get("source_device_sn")), None)
		if not row:
			row = doc.append("templates", {"source_device_sn": data.get("source_device_sn")})
		values = {f: data.get(f) for f in TEMPLATE_FIELDS if f not in ("source_device_sn", "received_on")}
		if any(row.get(f) != v for f, v in values.items()):
			row.update(values)
			row.received_on = now_datetime()
			changed += 1
	return changed


def fetch_home_templates_quietly(doc) -> int:
	"""fetch_home_templates when the details are fetched: a home on an older
	version (no template endpoint) or unreachable never stops the fetch."""
	try:
		doc.flags.templates_fetched = fetch_home_templates(doc)
	except RemoteError:
		# call_instance has put the detail in the Error Log
		doc.flags.templates_fetched = 0
	return doc.flags.templates_fetched


def fetch_home_record(doc, reset_host: bool = False) -> bool:
	"""Fill ``doc`` (an Exchange Employee) from its home instance. False when
	home has nobody with that payroll number. Raises RemoteError."""
	data = call_instance(
		doc.home_instance, "get_employee", params=_lookup_params(doc.home_instance, doc.payroll_number)
	)
	doc.last_fetched_on = now_datetime()
	if not data:
		doc.link_status = "Not Found"
		return False
	apply_home_record(doc, data)
	if reset_host or not doc.host_company:
		apply_company_map(doc, doc.home_instance, data.get("home_company"))
	fetch_home_templates_quietly(doc)
	return True


def not_found_message(instance: str, payroll_number: str) -> str:
	company = get_instance(instance).company
	if company:
		return _("{0} has no employee with payroll number or Employee ID {1} in {2}.").format(
			instance, payroll_number, company
		)
	return _("{0} has no employee with payroll number or Employee ID {1}.").format(instance, payroll_number)


def _lookup_params(instance: str, payroll_number: str) -> dict:
	"""Only that instance's chosen company is searched."""
	params = {"payroll_number": payroll_number}
	company = get_instance(instance).company
	if company:
		params["company"] = company
	return params


def apply_home_record(doc, data):
	for field in (
		"full_name",
		"home_employee_id",
		"id_number",
		"phone",
		"gender",
		"date_of_joining",
		"home_company",
		"home_department",
		"home_designation",
		"image",
	):
		doc.set(field, data.get(field))
	doc.link_status = "Linked"
	if not doc.device_pin:
		doc.device_pin = doc.payroll_number


def apply_company_map(doc, instance: str, their_company: str | None):
	"""Set the host company, unit and department from that instance's map:
	the row for their company there, else its defaults."""
	host = company_here(instance, their_company)
	doc.host_company = host.company
	doc.custom_farm = host.farm
	doc.department = host.department


def company_here(instance: str, their_company: str | None):
	inst = get_instance(instance)
	row = next(
		(
			r
			for r in frappe.get_single("Exchange Setting").company_map
			if r.instance == inst.instance_code and their_company and r.home_company == their_company
		),
		None,
	)
	return frappe._dict(
		company=row.company if row else None,
		farm=row.farm if row else None,
		department=row.department if row else None,
		mapped=bool(row),
	)


@frappe.whitelist()
def lookup_home_employee(home_instance: str, payroll_number: str) -> dict:
	"""For the form: what home says about this payroll number, and where the
	company map would place them here — before the record is saved."""
	frappe.has_permission("Exchange Employee", "create", throw=True)
	try:
		data = call_instance(
			home_instance, "get_employee", params=_lookup_params(home_instance, payroll_number)
		)
	except RemoteError as e:
		_throw_remote(e)
	if not data:
		return {"found": False, "message": not_found_message(home_instance, payroll_number)}
	host = company_here(home_instance, data.get("home_company"))
	return {"found": True, "employee": data, "host": host}


def refresh_active_employees():
	"""Nightly: re-read every active exchange employee from home, so a name or
	job changed there shows up here."""
	if not is_enabled():
		return
	for name in frappe.get_all(
		"Exchange Employee",
		filters={"status": "Active", "source_instance": my_code()},
		pluck="name",
	):
		try:
			doc = frappe.get_doc("Exchange Employee", name)
			if fetch_home_record(doc):
				doc.flags.from_refresh = True
				doc.save(ignore_permissions=True)
			else:
				doc.db_set({"link_status": "Not Found", "last_fetched_on": doc.last_fetched_on})
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Exchange: refresh {name}")


# ──────────────────────────────────────────────────────────────────────────
# Sending to the other instances
# ──────────────────────────────────────────────────────────────────────────


def queue_sync(doc):
	"""One Sync Log row per destination that takes ``doc``'s doctype. A
	record already waiting for a destination is not queued twice. A punch
	goes after its employee: one never sent to that instance (added to the
	Instances tab later, say) is queued first, so the punch arrives linked."""
	if not is_enabled() or not is_own(doc):
		return
	queued = []
	for instance in instances(enabled_only=True):
		for remote_doctype in targets(instance, doc):
			if doc.doctype == "Exchange Checkin" and doc.exchange_employee:
				_queue_employee_first(doc.exchange_employee, instance, queued)
			name = _queue(doc.doctype, doc.name, instance.instance_code, remote_doctype)
			if name:
				queued.append(name)
	if queued and not frappe.flags.in_test:
		# sent as soon as the save is committed; a failure is retried by
		# scheduled_push
		frappe.db.after_commit.add(lambda: _push_logs(queued))


def _queue(reference_doctype, reference_name, destination, remote_doctype) -> str | None:
	filters = {
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"destination": destination,
		"remote_doctype": remote_doctype,
	}
	if frappe.db.exists("Exchange Sync Log", {**filters, "status": "Queued"}):
		return None
	return (
		frappe.get_doc({"doctype": "Exchange Sync Log", **filters, "status": "Queued"})
		.insert(ignore_permissions=True)
		.name
	)


def _queue_employee_first(employee, instance, queued):
	if not cint(instance.send_employees):
		return
	sent = frappe.db.exists(
		"Exchange Sync Log",
		{
			"reference_doctype": "Exchange Employee",
			"reference_name": employee,
			"destination": instance.instance_code,
			"status": ["in", ["Sent", "Queued", "Sending"]],
		},
	)
	if sent or not is_own(frappe.get_cached_doc("Exchange Employee", employee)):
		return
	name = _queue("Exchange Employee", employee, instance.instance_code, "Exchange Employee")
	if name:
		queued.append(name)


def _push_logs(log_names: list):
	for name in log_names:
		try:
			push_one(name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Employee Exchange: push {name}", defer_insert=True)


def targets(instance, doc) -> list:
	"""The doctypes ``doc`` is posted as on ``instance``."""
	if doc.doctype == "Exchange Employee":
		return ["Exchange Employee"] if cint(instance.send_employees) else []
	if doc.doctype != "Exchange Checkin" or not cint(instance.send_checkins):
		return []
	# the employee's home instance makes their Employee Checkin itself when the
	# punch arrives (receive_checkin)
	return ["Exchange Checkin"]


def push_queued(destination: str | None = None, include_failed: bool = False) -> dict:
	"""Send what is waiting, oldest first. Each row is committed on its own,
	so one instance being down never holds the others back."""
	if not is_enabled():
		return {"sent": 0, "failed": 0}
	setting = settings()
	if include_failed:
		# a failed row waits Retry Every minutes before it is tried again, and
		# one left Sending by a worker that died is picked up the same way
		cutoff = add_to_date(now_datetime(), minutes=-max(cint(setting.retry_minutes) or 5, 1))
		filters = [
			["status", "in", ["Queued", "Failed", "Sending"]],
			["attempts", "<", max(cint(setting.max_attempts) or 20, 1)],
		]
	else:
		cutoff = None
		filters = [["status", "=", "Queued"]]
	if destination:
		filters.append(["destination", "=", destination])
	rows = frappe.get_all(
		"Exchange Sync Log",
		filters=filters,
		fields=["name", "status", "last_attempt", "modified"],
		order_by="creation asc",
		limit=max(cint(setting.batch_size) or 200, 1),
	)
	sent = failed = 0
	for row in rows:
		if row.status == "Failed" and row.last_attempt and get_datetime(row.last_attempt) > cutoff:
			continue
		if row.status == "Sending" and get_datetime(row.modified) > cutoff:
			continue
		result = push_one(row.name)
		if result:
			sent += 1
		elif result is False:
			failed += 1
		frappe.db.commit()
	return {"sent": sent, "failed": failed}


def scheduled_push():
	push_queued(include_failed=True)


def push_one(log_name: str) -> bool | None:
	"""Send one Sync Log row. None when another worker already has it."""
	if not _claim(log_name):
		return None
	log = frappe.get_doc("Exchange Sync Log", log_name)
	values = {"attempts": cint(log.attempts) + 1, "last_attempt": now_datetime()}
	if not frappe.db.exists(log.reference_doctype, log.reference_name):
		values.update(status="Failed", response=_("The record was deleted here."))
		log.db_set(values)
		return False
	instance = next((r for r in instances() if r.instance_code == log.destination), None)
	if not instance:
		values.update(
			status="Failed", response=_("{0} is no longer in the Instances tab.").format(log.destination)
		)
		log.db_set(values)
		return False
	doc = frappe.get_doc(log.reference_doctype, log.reference_name)
	try:
		result = call_instance(
			instance, "receive", payload={"doctype": log.remote_doctype, "data": build_payload(doc)}
		)
	except RemoteError as e:
		values.update(status="Failed", response=str(e)[:1000])
		log.db_set(values)
		instance.db_set({"connection_status": "Failed", "last_error": str(e)[:1000]})
		return False
	values.update(status="Sent", response=json.dumps(result, default=str)[:1000])
	log.db_set(values)
	instance.db_set({"connection_status": "Connected", "last_success": now_datetime(), "last_error": None})
	return True


def _claim(log_name: str) -> bool:
	"""Mark a row Sending before calling out, so the scheduled retry and a
	push started by a save never send the same row at once."""
	row = frappe.db.sql(
		"select status from `tabExchange Sync Log` where name = %s for update skip locked",
		log_name,
	)
	if not row or row[0][0] not in ("Queued", "Failed", "Sending"):
		frappe.db.rollback()
		return False
	frappe.db.set_value("Exchange Sync Log", log_name, "status", "Sending", update_modified=True)
	frappe.db.commit()
	return True


def build_payload(doc) -> dict:
	if doc.doctype == "Exchange Employee":
		data = {f: _plain(doc.get(f)) for f in EMPLOYEE_FIELDS}
		data["templates"] = [{f: _plain(row.get(f)) for f in TEMPLATE_FIELDS} for row in doc.templates]
		return data
	if doc.doctype == "Exchange Checkin":
		return {f: _plain(doc.get(f)) for f in CHECKIN_FIELDS}
	frappe.throw(_("{0} is not sent to other instances.").format(doc.doctype))


def _plain(value):
	return str(value) if hasattr(value, "isoformat") else value


# ──────────────────────────────────────────────────────────────────────────
# Receiving from another instance
# ──────────────────────────────────────────────────────────────────────────


def receive(doctype: str, data: dict) -> dict:
	if doctype not in RECEIVABLE:
		frappe.throw(_("This instance does not take {0}.").format(doctype))
	data = frappe._dict(data)
	if not data.get("origin_id"):
		frappe.throw(_("The record has no origin_id."))
	if data.get("source_instance") == my_code():
		# our own record coming back: nothing to do, and never send it on
		return {"status": "ignored", "reason": "own record"}
	if doctype == "Exchange Employee":
		return receive_employee(data)
	if doctype == "Exchange Checkin":
		return receive_checkin(data)
	return receive_as_employee_checkin(data)


def receive_employee(data) -> dict:
	name = frappe.db.get_value("Exchange Employee", {"origin_id": data.origin_id})
	doc = frappe.get_doc("Exchange Employee", name) if name else frappe.new_doc("Exchange Employee")
	for field in EMPLOYEE_FIELDS:
		if field in ("host_company", "device_pin"):
			continue
		doc.set(field, data.get(field))
	doc.link_status = "Linked"

	if not name:
		if (
			data.get("home_instance") == my_code()
			and data.get("home_company")
			and frappe.db.exists("Company", data.home_company)
		):
			# one of ours, working elsewhere: they stay under their own company
			doc.host_company = data.home_company
		elif instance_exists(data.source_instance):
			apply_company_map(doc, data.source_instance, data.get("host_company"))
		doc.device_pin = _free_pin(data.get("device_pin"))

	doc.set("templates", [])
	for row in data.get("templates") or []:
		doc.append("templates", {f: row.get(f) for f in TEMPLATE_FIELDS})

	doc.flags.from_sync = True
	doc.flags.ignore_links = True
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)
	link_waiting_checkins(doc)
	return {"status": "updated" if name else "created", "name": doc.name}


def _free_pin(pin):
	"""Their PIN, unless someone here already answers to it on a device."""
	pin = (pin or "").strip()
	if not pin:
		return None
	if frappe.db.exists("Employee", {"attendance_device_id": pin}):
		return None
	if frappe.db.exists("Exchange Employee", {"device_pin": pin}):
		return None
	return pin


def same_punch(doctype, employee_field, employee, time, log_type) -> str | None:
	"""An existing punch of ``employee`` at exactly ``time`` with the same log
	type (both blank counts as the same); the same punch is never kept twice."""
	return frappe.db.get_value(
		doctype,
		{
			employee_field: employee,
			"time": get_datetime(time),
			"log_type": log_type or ["in", ["", None]],
		},
	)


def receive_checkin(data) -> dict:
	existing = frappe.db.get_value("Exchange Checkin", {"origin_id": data.origin_id})
	if existing:
		return {"status": "exists", "name": existing}
	employee = frappe.db.get_value(
		"Exchange Employee",
		{"origin_id": data.get("employee_origin_id")},
		["name", "host_company"],
		as_dict=True,
	)
	same = employee and same_punch(
		"Exchange Checkin", "exchange_employee", employee.name, data.get("time"), data.get("log_type")
	)
	if same:
		result = {"status": "exists", "name": same}
	else:
		doc = frappe.new_doc("Exchange Checkin")
		for field in CHECKIN_FIELDS:
			doc.set(field, data.get(field))
		doc.arrived_via = "Sync"
		if employee:
			doc.exchange_employee = employee.name
			doc.company = employee.host_company
		doc.flags.from_sync = True
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)
		result = {"status": "created", "name": doc.name}
	if data.get("home_instance") == my_code():
		# one of our own employees punching on another instance: their
		# attendance here counts it as a real Employee Checkin
		result.update(make_employee_checkin_quietly(data))
	return result


def make_employee_checkin_quietly(data) -> dict:
	"""make_employee_checkin, where a failure (no Employee with that payroll
	number, an inactive one) never loses the punch: it goes to the Error Log."""
	try:
		frappe.db.savepoint("exchange_employee_checkin")
		return {"employee_checkin": make_employee_checkin(data)["name"]}
	except Exception as e:
		frappe.db.rollback(save_point="exchange_employee_checkin")
		frappe.clear_last_message()
		frappe.log_error(
			title=f"Employee Exchange: Employee Checkin for {data.get('payroll_number')}",
			message=frappe.get_traceback(),
			defer_insert=True,
		)
		return {"employee_checkin_error": str(e)[:300]}


def receive_as_employee_checkin(data) -> dict:
	"""Sent by instances on an older version, which posted the Employee
	Checkin separately; it is made on arrival of the punch now."""
	if data.get("home_instance") != my_code():
		frappe.throw(_("Employee Checkin is only made on the employee's home instance."))
	return make_employee_checkin(data)


def make_employee_checkin(data) -> dict:
	"""The Employee Checkin of an employee of this instance for a punch made
	on another one, once per employee, time and log type."""
	field = (settings().payroll_number_field or "attendance_device_id").strip()
	employee = frappe.db.get_value("Employee", {field: data.get("payroll_number")})
	if not employee:
		frappe.throw(_("No employee here has payroll number {0}.").format(data.get("payroll_number")))
	existing = same_punch("Employee Checkin", "employee", employee, data.time, data.get("log_type"))
	if existing:
		return {"status": "exists", "name": existing}
	doc = frappe.get_doc(
		{
			"doctype": "Employee Checkin",
			"employee": employee,
			"time": data.time,
			"log_type": data.get("log_type") or None,
			"device_id": data.get("device_location") or data.get("device_sn"),
		}
	)
	doc.insert(ignore_permissions=True)
	return {"status": "created", "name": doc.name}


def link_waiting_checkins(employee):
	"""Punches that arrived before the person did are linked once they do."""
	for name in frappe.get_all(
		"Exchange Checkin",
		filters={"employee_origin_id": employee.origin_id, "exchange_employee": ["is", "not set"]},
		pluck="name",
	):
		frappe.db.set_value(
			"Exchange Checkin",
			name,
			{"exchange_employee": employee.name, "company": employee.host_company},
			update_modified=False,
		)


# ──────────────────────────────────────────────────────────────────────────
# Punches from this instance's devices
# ──────────────────────────────────────────────────────────────────────────


def record_device_punch(pin, time, device_sn=None, log_type=None) -> str | None:
	"""Save a device punch as an Exchange Checkin when the PIN is an active
	exchange employee's and no Employee here owns it. Returns its name, or
	None when the punch is not an exchange employee's."""
	pin = str(pin or "").strip()
	if not pin or not time or not is_enabled():
		return None
	if frappe.db.exists("Employee", {"attendance_device_id": pin}):
		return None
	employee = frappe.db.get_value(
		"Exchange Employee", {"device_pin": pin, "status": "Active"}, ["name"], as_dict=True
	)
	if not employee:
		return None
	log_type = (log_type or "").strip().upper() or None
	if log_type not in (None, "IN", "OUT"):
		log_type = None
	existing = same_punch("Exchange Checkin", "exchange_employee", employee.name, time, log_type)
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Exchange Checkin",
			"exchange_employee": employee.name,
			"time": get_datetime(time),
			"log_type": log_type,
			"device_sn": device_sn,
			"device_location": device_location(device_sn),
			"arrived_via": "Device",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def device_location(device_sn) -> str | None:
	if not device_sn:
		return None
	return frappe.db.get_value(
		"Biometric Device",
		{"parenttype": "Biometric Setting", "device_sn": device_sn},
		"device_location",
	)


# ──────────────────────────────────────────────────────────────────────────
# Biometric devices
# ──────────────────────────────────────────────────────────────────────────


def registered_devices() -> list:
	return frappe.get_all(
		"Biometric Device",
		filters={"parenttype": "Biometric Setting"},
		fields=["device_sn", "device_location", "status"],
		order_by="idx asc",
	)


@frappe.whitelist()
def device_options() -> list:
	"""The devices on Biometric Setting, for picking one on a form."""
	if not (
		frappe.has_permission("Exchange Employee", "read")
		or frappe.has_permission("Exchange Checkin", "read")
	):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return registered_devices()


def _require_device_commands():
	if not cint(frappe.db.get_single_value("Biometric Setting", "enable_users")):
		frappe.throw(_("Turn on Enable Users in Biometric Setting first."))


def push_to_device(doc, device_sn: str) -> int:
	"""Put the person on one device: their user record, then each stored
	template the device can hold. Returns the number of templates sent."""
	from upande_ta.upande_ta.doctype.biometric_user import biometric_user as bu

	caps = bu._device_capabilities(device_sn)
	tpl = best_template(doc, device_sn)
	cmd_id = frappe.generate_hash(length=10)
	bu._post_to_nodered(
		{
			"command_id": cmd_id,
			"command_type": "Add User",
			"device_sn": device_sn,
			"user_id": doc.device_pin,
			"employee_name": doc.full_name,
			"command": bu._build_userinfo_command(cmd_id, doc.device_pin, doc.full_name, "0", tpl, caps),
		}
	)
	if not tpl:
		return 0

	device_codes = bu._device_type_codes(device_sn)
	sent = 0
	for label, type_code, no_f, idx_f, valid_f, major_f, minor_f, type_f, tmp_f in bu._BIO_TYPES:
		template = tpl.get(tmp_f)
		if not template or not cint(tpl.get(valid_f)):
			continue
		if not bu._device_supports(caps, bu._MODALITY_CAPABILITY[label]):
			continue
		# the target device's own code wins over the one the template came with
		type_value = device_codes.get(label) or tpl.get(type_f) or type_code
		cmd_id = frappe.generate_hash(length=10)
		bu._post_to_nodered(
			{
				"command_id": cmd_id,
				"command_type": f"Add BioData ({label})",
				"device_sn": device_sn,
				"user_id": doc.device_pin,
				"employee_name": doc.full_name,
				"command": (
					f"C:{cmd_id}:DATA UPDATE BIODATA"
					f"\tPin={doc.device_pin}"
					f"\tNo={tpl.get(no_f) or 0}"
					f"\tIndex={tpl.get(idx_f) or 0}"
					f"\tValid=1"
					f"\tDuress=0"
					f"\tType={type_value}"
					f"\tMajorVer={tpl.get(major_f) or 0}"
					f"\tMinorVer={tpl.get(minor_f) or 0}"
					f"\tFormat=0"
					f"\tTmp={template}"
				),
			}
		)
		sent += 1
	return sent


def delete_from_device(doc, device_sn: str):
	from upande_ta.upande_ta.doctype.biometric_user import biometric_user as bu

	cmd_id = frappe.generate_hash(length=10)
	bu._post_to_nodered(
		{
			"command_id": cmd_id,
			"command_type": "Delete User",
			"device_sn": device_sn,
			"user_id": doc.device_pin,
			"employee_name": doc.full_name,
			"command": f"C:{cmd_id}:DATA DELETE USERINFO\tPIN={doc.device_pin}",
		}
	)


def best_template(doc, device_sn):
	"""The template row captured on this device, else the fullest one."""
	rows = list(doc.templates or [])
	if not rows:
		return None
	own = next((r for r in rows if r.source_device_sn == device_sn), None)
	if own:
		return own
	return max(rows, key=lambda r: sum(cint(r.get(f"{p}_valid")) for p in ("fp", "face", "palm")))


def store_device_template(user_id, device_sn, values: dict) -> dict | None:
	"""``store_biotemplate`` for a PIN that is no Employee's: keep the
	template on the exchange employee with that PIN. None when there is none."""
	name = frappe.db.get_value("Exchange Employee", {"device_pin": user_id})
	if not name:
		return None
	doc = frappe.get_doc("Exchange Employee", name)
	row = next((r for r in doc.templates if r.source_device_sn == device_sn), None)
	if not row:
		row = doc.append("templates", {"source_device_sn": device_sn})
	changed = False
	meta = frappe.get_meta("Exchange Employee Template")
	for field, value in values.items():
		if meta.has_field(field) and row.get(field) != value:
			row.set(field, value)
			changed = True
	if changed:
		row.received_on = now_datetime()
		device = next((d for d in doc.devices if d.device_sn == device_sn), None)
		if device:
			device.on_device = 1
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	return {
		"status": "updated" if changed else "unchanged",
		"message": f"Template for exchange employee {doc.name} from {device_sn} "
		+ ("saved." if changed else "already up to date."),
		"user_id": user_id,
		"device_sn": device_sn,
	}


# ──────────────────────────────────────────────────────────────────────────
# Buttons on the Instances rows of Exchange Setting
# ──────────────────────────────────────────────────────────────────────────


def _require_setting_writer():
	frappe.has_permission("Exchange Setting", "write", throw=True)


@frappe.whitelist()
def test_connection(instance_code: str) -> dict:
	_require_setting_writer()
	inst = get_instance(instance_code)
	try:
		result = call_instance(inst, "ping")
	except RemoteError as e:
		inst.db_set({"connection_status": "Failed", "last_error": str(e)[:1000]})
		frappe.db.commit()
		_throw_remote(e)
	code = (result or {}).get("instance_code")
	if code and code != inst.instance_code:
		frappe.throw(
			_("That URL answers as {0}, not {1}. Check the Base URL or the Instance Code.").format(
				frappe.bold(code), frappe.bold(inst.instance_code)
			)
		)
	inst.db_set({"connection_status": "Connected", "last_error": None})
	return {"instance_code": code}


@frappe.whitelist()
def push_now(instance_code: str) -> dict:
	_require_setting_writer()
	return push_queued(destination=get_instance(instance_code).instance_code, include_failed=True)


@frappe.whitelist()
def remote_companies(
	base_url: str,
	api_token: str | None = None,
	row_name: str | None = None,
	instance_code: str | None = None,
	quiet: int = 0,
) -> list:
	"""The companies on another instance, read with the URL and token typed
	into its Instances row, before the row is saved. A token left masked is
	the one already stored for that row. ``quiet`` (the form opening) returns
	nothing instead of an error."""
	_require_setting_writer()
	token = api_token or ""
	if (not token or set(token) == {"*"}) and row_name:
		token = frappe.utils.password.get_decrypted_password(
			"Exchange Instance", row_name, "api_token", raise_exception=False
		)
	base_url = (base_url or "").strip().rstrip("/")
	probe = frappe._dict(
		instance_code=(instance_code or "").strip().upper() or base_url.split("//")[-1].split("/")[0],
		base_url=base_url,
		get_password=lambda *a, **k: token,
	)
	if not probe.base_url or not all(split_token(token)):
		return []
	try:
		return [c for c in call_instance(probe, "get_companies") or [] if not c.startswith("_Test")]
	except RemoteError as e:
		if cint(quiet):
			return []
		_throw_remote(e)
