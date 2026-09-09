# Upande TA (T&A)

**Upande Time & Attendance** — a Frappe/ERPNext app that turns ZKTeco biometric
devices into ERPNext Employee Checkins, and layers HR tooling on top of HRMS:
bulk overtime payroll, bulk weekly-off reassignment, a live attendance
dashboard, and an enhanced Monthly Attendance Sheet.

> **Compatibility:** Frappe/HRMS **v16 only** (`>=16.0.0,<17.0.0`), Python
> `>=3.10`. The app overrides HRMS **Overtime Slip**, which does not exist in
> v15; supporting v15 would mean making that override conditional on the
> doctype existing. The bounded, comma-separated `frappe-dependencies`
> declaration in `pyproject.toml` is required by Frappe Cloud — without it the
> app is rejected with *"invalid version format"*.

---

## Architecture

The app does **not** talk to biometric hardware directly (no `pyzk`/socket
code). It uses a push/relay model built around **Node-RED** and ZKTeco
"PUSH SDK" text commands:

```
                    HTTP POST (JSON command strings)
   ┌───────────┐   ───────────────────────────────►   ┌──────────┐   ┌─────────────┐
   │  Frappe   │        "C:<id>:DATA QUERY ATTLOG…"   │ Node-RED │◄─►│ ZKTeco      │
   │(upande_ta)│                                      │  relay   │   │ devices     │
   └───────────┘   ◄───────────────────────────────   └──────────┘   │ (by serial) │
                    allow_guest endpoints + Employee                 └─────────────┘
                    Checkin REST API (device → Frappe)
```

- **Outbound** (Frappe → devices): the single bridge is `_post_to_nodered()` in
  `biometric_user.py` — it reads `server_ip`, `server_port`, `end_point` from
  **Biometric Setting** and POSTs the command JSON with a 10 s timeout. Failures
  are logged and swallowed, never raised.
- **Inbound** (devices → Frappe): Node-RED holds the real device connections and
  calls this app's `allow_guest` endpoints (`store_biotemplate`,
  `store_device_status`) and, for punches, Frappe's standard **Employee
  Checkin** REST API.
- **Device identity** is the **serial number** (`device_sn`), never an IP;
  **employee mapping** is `Employee.attendance_device_id` = the device PIN.

Everything the app configures lives on the **Biometric Setting** single.

---

## 1. Biometric device integration

### Doctypes

| Doctype | Kind | Role |
|---|---|---|
| **Biometric Setting** | Single | Control panel: server config, `devices` registry, `poll_devices`, schedules, dashboard filter toggles. |
| **Biometric Device** | Child (`devices`) | `device_sn`, `device_location`, `farms`, `status`, `last_seen`, and the `supports_*` capability flags. |
| **Biometric Checkin** | Child (`poll_devices`) | Manual poll request: `device`, `device_sn`, `command_id`, `status`. |
| **Biometric User** | Parent (1/device) | Enrollment **roster** — which PINs/employees are on a device. |
| **Bio User** | Child (`users`) | One row per enrolled PIN: `user_id`, `employee`, `privilege`, `status`. |
| **Biometric Template** | Parent (1/device) | The biometric **payloads** per employee. |
| **Bio Template** | Child (`bio_templates`) | Fingerprint/face/palm template, size, version and device-reported `*_type_code`, plus card/password/verify settings. |
| **Biometric Logs** | Standalone | Legacy/manual log; not written by current code, but read by the upande_scp flow. |

`Bio User` / `Bio Template` are child rows of `Biometric User` /
`Biometric Template` — both current, not legacy-vs-new. Parents cannot be
created manually; they are auto-provisioned per device on Biometric Setting
save, guarded by a `before_insert` flag.

### Configuration & device status

- Devices are registered in **Biometric Setting → Server Settings → `devices`**;
  each row requires `device_sn`, `device_location`, `farms`.
- On save, `on_update` runs `_sync_scheduled_jobs()`,
  `_ensure_biometric_user_parents()` and `_normalize_poll_device_values()`.
- **Removing a device is guarded** — `_block_removing_devices_with_links()`
  refuses while Bio User / Bio Template rows remain, and links them for cleanup.
- **Online status**: Node-RED calls `store_device_status()`; a device is
  **offline** after **> 1 minute** of silence
  (`mark_stale_devices_offline_scheduled`, cron every minute, publishes a
  realtime event so form badges update live).

### Check-in pull flow

1. **Request punches** — `poll_devices()` (manual, per `poll_devices` row) or
   `run_checkin()` (scheduled, rolling window from `checkin_event_frequency`)
   POST `C:<id>:DATA QUERY ATTLOG\tStartTime=…\tEndTime=…`.
2. **Return punches** — Node-RED creates Employee Checkins via Frappe's standard
   API (device → employee via `attendance_device_id`).
3. **Deduplicate** — `prevent_duplicate()` rejects a checkin with the same
   `employee` + `time` + `log_type` (blank and None treated as equivalent),
   making repeated ATTLOG pulls idempotent.
4. **IN/OUT normalization** — readers mislabel direction both ways: ZKTeco
   punches often all arrive as IN, and the gate terminals stamp every scan OUT.
   `normalize_checkin_directions()` groups scans into the employee's **assigned
   shift window** — the `shift` / `shift_actual_start` HRMS stamps on each
   checkin, which covers every Shift Type and keeps a night worker's scans
   either side of midnight in one window; a scan with no shift resolved falls
   back to its calendar day. Then two passes: a trailing IN is flipped to OUT,
   and a window with **no IN at all** has its earliest scan flipped to IN. Pass
   2 runs second so `OUT,IN` → `OUT,OUT` → `IN,OUT`. Both need ≥2 scans; middle
   scans and blank log_types are never touched. Scheduled as
   `run_flip_last_in`, manual via `flip_checkins_for_date` (HR/System Manager).
   `auto_close_open_ins` remains as an alias.

### Enrollment sync

- **Templates in** (device → Frappe): `store_biotemplate()` (`allow_guest`)
  upserts a Bio Template row keyed by (parent, employee), honouring an optional
  per-device PIN filter, and records the device's own BIODATA `Type=` code.
- **Request templates** (Frappe → device): `run_biodata_sync()` /
  `request_biodata*` POST five ZKTeco queries per device (FINGERTMP, FACE,
  BIOPHOTO, USERINFO, palm).
- **Roster management** (Frappe → device): `bulk_command()` is the core engine —
  Add/Update/Delete users with **farm scoping** (Add/Update only for employees
  whose `custom_farm` ∈ the device's `farms`), pushing USERINFO + BIODATA.
  `bulk_command_per_device()` adds MariaDB deadlock retry/backoff.
- **PIN changes**: `Employee.on_update` → `handle_pin_change()` re-keys all Bio
  User / Bio Template rows and **enqueues** a background resync, so a dead
  device cannot block the Employee save.

**What gets pushed to which device** — three filters, all keyed on the serial:

1. **Capabilities** — the `supports_fingerprint` / `_face` / `_palm` / `_card` /
   `_password` checkboxes on the Biometric Device row. All default on; unticking
   one keeps that credential out of every command, so a face-only terminal is
   never sent a fingerprint. Card and password are sent **blank rather than
   omitted** (USERINFO is positional-by-key and some devices reject the whole
   record). Two ways to set them:

   - **On add, from the serial number.** `capability_profile_for_serial()` maps
     the serial's prefix to a terminal family — `NYU` / `PYA` / `TDBD` are
     fingerprint + face readers, `WJA` / `CO8D` are face terminals. The prefix
     is not a published model code, but it groups reliably: the WJA/CO8D
     readers have never returned a single fingerprint across ~1,000 Bio
     Template rows. Applied client-side only to an unsaved row, so it can never
     overwrite a flag someone has already set; an unrecognised prefix leaves
     the defaults alone.
   - **After the fact, from the evidence.** All five credentials live on the
     same Bio Template row, so **Detect Capabilities** (Bio Data tab) maps the
     flags from what each device has actually delivered. It dry-runs first and
     shows the counts; a flag only switches on with ≥5 rows *and* 2% of the
     roster (one stray card number is noise), and a device that has delivered
     **no** template is skipped entirely — a missing sensor and a broken upload
     look identical, and switching its flags off would silently stop every
     future push. This is why the serial profile exists: it can seed a terminal
     that has never uploaded anything.

2. **Template source** — `_get_template_row()` merges **per credential**, not
   per row. The device's own row wins for each modality it actually holds, and
   anything it lacks is filled from the employee's newest row elsewhere. The
   distinction matters: `store_biotemplate` also creates a Bio Template row for
   the plain USERINFO record, so a device very often holds an *empty shell* row
   for someone. Preferring that row wholesale meant an employee enrolled on
   another terminal got nothing pushed, silently. Type codes are deliberately
   not merged — those are read from the device's own row so a neighbour's code
   is never echoed to this terminal.

   A borrowed template must also be **algorithm-compatible**. Templates only
   load on an engine of the same version, and `Type=` cannot tell versions
   apart: this fleet runs three face algorithms — 40.1 (756-char), 35.x
   (748-char) and 5.6 (344-char) — and all three report `Type=9`. A 40.1
   template pushed to a 5.6 terminal is accepted and then silently discarded,
   which is why a face could appear to "never save". `_device_algo_versions()`
   learns each device's majority `MajorVer.MinorVer` per modality from its own
   uploads; a mismatched source is refused outright, and a device that has
   never delivered that modality still gets a speculative push (no worse than
   pushing nothing). Fingerprints on this fleet are uniform (13.0, 1400-char)
   and travel freely.
3. **Already on device** — a modality the device already holds byte-for-byte is
   skipped; `force` bypasses this when the device has just lost the user (fresh
   add, re-add, PIN re-key).

**Names are fitted, not truncated.** ZKTeco caps USERINFO `Name=` at 24
characters, and a blind slice mangled surnames ("Martin Bundotich Kipkoech" →
"Martin Bundotich Kipkoec"). `_device_name()` sends **first and last only,
always** — so a person reads the same on every terminal — degrading to
`F. Last` then the surname alone if that still will not fit. ERPNext's own rows
keep the full name; only the device command is fitted.

**`Type=` is resolved per device.** Firmwares disagree — these readers report
`1` for fingerprint and `9` for face, while a Horus E1 reports `2` for
fingerprint — and a template pushed under the wrong code is filed by the device
as the wrong modality. The code sent is the target device's own: the employee's
row on that device, else that device's majority code, else the fleet default
(1/9/8). It is learned from the device's own uploads and cached for an hour, so
**poll a new terminal before pushing to it**.

### Key whitelisted endpoints

`store_biotemplate`, `store_device_status` (both `allow_guest`, called by
Node-RED); `poll_devices`, `request_biodata*`, `resync_scheduled_jobs`,
`get_scheduled_job_links`, `get_device_statuses`, `flip_checkins_for_date`,
`bulk_command*`, `get_device_users*`, `get_employees`,
`hydrate_users_from_templates`.

---

## 2. HR overrides

Wired via `hooks.py` (`doc_events`, `override_doctype_class`, `before_request`,
`before_job`).

- **Employee — PIN defaulting.** `set_attendance_device_id` defaults a blank
  `attendance_device_id` to the Employee ID; `sync_attendance_device_id_change`
  propagates a change through `handle_pin_change()`. The Employee form JS adds
  Add/Update/Delete-on-Device buttons and auto-syncs on new, deactivated and
  reactivated employees.
- **Employee Checkin — duplicate prevention.** See the pull flow above.
- **Leave Type — `abbreviation`.** A custom Data(6) field added on
  install/migrate and removed on uninstall. `generate_leave_abbr()` builds a
  unique abbreviation from word initials ("Maternity Leave" → "ML") with
  collision suffixing; existing values are left untouched. Consumed by the
  Monthly Attendance Sheet.
- **Monthly Attendance Sheet — enhanced report.** `apply_patch()` (idempotent,
  from `before_request`/`before_job`) replaces five functions on the stock HRMS
  report module: rows grouped by the employee's **assigned shift** (submitted
  active Shift Assignment, falling back to `default_shift`), per-leave-type
  abbreviations in day cells, a recomputed chart/legend, and server-side summary
  rows tagged `_is_summary`. The bundled JS colour-codes day cells, styles the
  summary block and pins summary rows to the bottom on client-side sort.
- **Overtime Slip — `UpandeOvertimeSlip`.** Diverges only for **bulk** slips
  (those with a `custom_bulk_overtime` link); manual slips keep native
  behaviour. `validate` skips the native duplicate-date/type/max-hours checks
  (bulk amounts are pre-computed) but keeps the date and overlap guards;
  `on_submit` sums `custom_amount` per salary component and creates + submits one
  **Additional Salary** each; `on_cancel` cancels them.
- **Workspace.** The **T&A** workspace cannot be hidden, un-published or deleted
  (except during install/migrate/uninstall).

---

## 3. Bulk Overtime

Raise overtime pay for many employees at once for one pay period.

`Bulk Overtime` (submittable, `HR-BOT-.YYYY.-`) with a `bulk_overtime_entries`
child table. `WORKING_HOURS_PER_MONTH = 199.33`; overtime types map to
multipliers ("Overtime 1.5" → 1.5, "Overtime 2.0" → 2.0), each tied to a
like-named Salary Component.

`fill_employee_details` seeds the grid from Active employees by
department/designation. `validate` checks date sanity, overlap against other
Bulk Overtime for the same company, and that Manual rows carry hours.
`on_submit` looks up each employee's Salary Structure Assignment `base`,
computes `hourly_rate = base / 199.33`, and creates a submitted Overtime Slip
with `custom_amount = round(hourly_rate × multiplier × hours, 2)` per applicable
type. `on_cancel` unwinds them.

**Chain:** `Bulk Overtime → Overtime Slip → Additional Salary` (cancel unwinds
in reverse). `ensure_overtime_setup` (on migrate) creates the custom fields and
the two Overtime Type records.

> ⚠️ The **biometric auto-calculation** path is **not functional** — see
> [Known limitations](#known-limitations). The Manual path and the
> slip/Additional-Salary generation are coherent.

---

## 4. Bulk Week Off

Reassign employees' weekly-off day in bulk by moving them onto a different
Holiday List, effective a chosen date, recorded as HRMS **Employee Transfer** +
**Holiday List Assignment** so both old and new weekly-offs render correctly per
date.

`Bulk Week Off` (submittable, `BWO-.#####`) with an `employees` child table.
`validate` rejects duplicate employees and a `from_date` outside the relevant
Holiday List periods. `on_submit` creates, per row, an Employee Transfer
changing `holiday_list` effective `from_date` plus a forward Holiday List
Assignment, and backfills a prior assignment so pre-transfer dates keep the old
weekly off. Transfers dated **today or earlier** submit immediately;
**future-dated** ones stay Draft (HRMS refuses those) until the daily
`submit_due_employee_transfers` cron submits them. `on_cancel` reverts the
assignments, transfers and `Employee.holiday_list`.

---

## 5. TA Dashboard

A custom **HTML block** in the T&A workspace, rendered by
`blocks.render_ta_dashboard`, driven by three whitelisted endpoints in
`api/dashboard.py`:

- **`get_ta_dashboard_stats`** — IN/OUT/total/unique counts over a date range
  and for today, a per-day series, device online/total, scoped headcount.
- **`get_ta_dashboard_checkins`** — the roster for one date with
  **overnight-shift pairing** (evening IN ↔ morning OUT), worked hours, and
  non-present staff classified as Leave / Weekly Off / Absent.
- **`get_ta_dashboard_filter_options`** — cascading company → farm → department
  → designation → employee options; which filters render is controlled by
  toggles on Biometric Setting.

All endpoints scope by company / farm / department / designation / employee, and
the `farm` filter degrades gracefully when `Employee.custom_farm` is absent.

Richer reporting lives in `api/attendance_insights.py`. Lateness and earliness
are measured against the **assigned shift window** stamped on each scan
(`shift_start` / `shift_end`), and the night-shift register prefers the
normalized `log_type` when a window is a clean IN…OUT pair, falling back to
time-of-day inference otherwise — so it degrades safely on data the flip pass
has not reached.

---

## 6. upande_scp integration

`upande_ta` owns the biometric pieces the **upande_scp** store-keeper transfer
flow depends on, so `upande_scp` only needs `upande_ta`, `upande_core`, ERPNext
and Frappe: **Biometric Logs** (read read-only for the live finger-scan match),
a `require_biometric` Check on **Stock Entry Type**, and a Biometric
Verification section on **Stock Entry**.

Those Stock Entry fields (`requires_biometric`, `bio_employee` + fetched name
and department, `biometric_status`, `biometric_verified_at`,
`matched_biometric_log`) are created **programmatically** on install/migrate and
removed on uninstall — see `overrides/stock_entry.py`, **not** fixtures. The
section shows only when `requires_biometric` is fetched as set.

`public/js/stock_entry.js` adds a **Check Biometric Log** button that matches the
latest scan for `bio_employee` within a 1-minute window, flips
`biometric_status` with a styled badge, auto-submits on success, and soft-blocks
submit when unverified.

---

## 7. Receipt printing via QZ Tray

Thermal receipts print on the operator's own computer even though the site is
hosted elsewhere. **QZ Tray** listens on `wss://localhost:8181` and only accepts
calls from the browser, so the chain is **site page → QZ Tray → printer**: no
tunnel, no inbound port, no per-branch VPN.

- **Signing.** QZ Tray refuses to print unattended unless every call is signed,
  and the browser must not hold the key. `qz-tray.js` hands the SHA-256 digest
  of `{call, params, timestamp}` to the site; `api/qz.py` returns an
  **RSA-SHA512** signature over it — `SHA512` here must stay in step with
  `qz.security.setSignatureAlgorithm("SHA512")` in the bridge. Configure
  `qz_certificate` / `qz_private_key` per site as inline PEM or a path.
- **Trust.** A signed request from an unknown certificate still raises the
  allow/deny dialog. Install the same certificate as **`override.crt`** in QZ
  Tray's install directory (`/opt/qz-tray` on Linux) and restart it; printing
  then goes silent. Or click **Allow** once with **Remember**, per computer.
- **Terminals.** A computer becomes a print terminal by saving a printer on
  **`/printer-settings`**; the choice lives in that browser's `localStorage`
  (`meal_receipt_printer`), never on the server, so one site serves branches
  with different printers. Client API is `upande_ta.qz.*` in
  `public/js/qz_bridge.js`.
- **Server-raised jobs.** `qz.send_to_terminal()` publishes a realtime job that
  only armed terminals act on. It defaults to `after_commit=True`, so a
  rolled-back transaction cannot print paper.

Meal receipts take this path when a printer is saved and fall back to the browser
print dialog otherwise. The receipt QR comes from `api.qrserver.com`, so the
*printing computer* needs internet for it to render.

---

## Lifecycle & scheduled jobs

**`after_install`** — desktop icon, TA Dashboard block, Leave Type abbreviation
field, Biometric Verification Stock Entry fields.

**`after_migrate`** (in order) — sanitize link filters, resync scheduled jobs,
desktop icon, dashboard block, abbreviation field, Stock Entry fields,
`cleanup.remove_orphans`, `ensure_overtime_setup`.

**`before_uninstall`** — remove the abbreviation field and the Stock Entry
fields.

`cleanup.remove_orphans` deletes standard records (Reports, Pages, Print
Formats, Notifications, Dashboards/Charts/Number Cards, Custom HTML Blocks,
public Workspaces) attributed to module "Upande TA" that are no longer shipped
in source. Orphan **DocTypes are only logged**, never auto-dropped.

| Cadence | Job | Purpose |
|---|---|---|
| every minute | `mark_stale_devices_offline_scheduled` | flip devices offline after >1 min silence |
| daily midnight | `submit_due_employee_transfers` | auto-submit due Bulk Week Off transfers |
| configurable | `run_checkin` | poll ATTLOG from devices |
| configurable | `run_biodata_sync` | request biometric templates |
| configurable (default Hourly) | `run_flip_last_in` | normalize check-in directions |

The configurable jobs are per-setting **Scheduled Job Type** rows built from
Biometric Setting frequency/cron fields, re-synced on migrate via
`resync_scheduled_jobs`.

---

## Known limitations

**Bulk Overtime biometric auto-calc is non-functional as written.**
`get_overtime_hours()` is internally inconsistent (undefined locals,
uninitialized result dict, no return), so the `{employee: {normal, holiday}}`
map its callers expect is never produced; `revalidate_biometric_hours` and
`sync_attendance_data` depend on it. `get_overtime_type` is referenced but not
defined in the file. `on_submit`'s revalidation is gated on
`auto_validate_worked_hours`, which is not a field on the doctype, so that
branch is dormant. The Manual path works.

**The Node-RED parser owns three contracts this app cannot enforce:**

- `bio_type` must be derived from the row's own `Type=` code, never from which
  query was issued — a `DATA QUERY` reply carries no command id, so attributing
  it to the most recent outstanding query silently files one modality's template
  under another. An unmapped code must fail loudly rather than defaulting.
- **BIOPHOTO rows have no home.** Bio Template stores fingerprint/face/palm
  templates only, so posting an enrolment photo to `store_biotemplate` (no
  `bio_type`, no `template`) returns `Unsupported bio_type: ''`. Visible-light
  terminals answer with these, which makes the error look device-specific.
- `log_type` accepts only `""`, `IN` or `OUT`. ATTLOG punch state **255** means
  the terminal is not recording direction at all and must map to **blank** — not
  `"Unknown"`, which fails Select validation and drops the punch. Note that a
  blank log_type yields no attendance for employees on a Shift Type set to
  "Strictly based on Log Type in Employee Checkin"; the real fix is to re-enable
  punch state on the terminal.

---

## Patches

`patches/v1`, run on migrate (see `patches.txt`):

| Patch | Does |
|---|---|
| `sanitize_link_filters` | null invalid `link_filters`, drop the `json_valid` CHECK legacy `''` values trip |
| `migrate_biometric_user_to_child` | flat Biometric User docs → parent + `users` child rows (with backup table) |
| `restore_biometric_setting` | optional snapshot restore onto an empty site (off by default, env-gated) |
| `dedupe_and_lock_bio_template` | dedupe Bio Template by (parent, employee) + UNIQUE index |
| `rename_biometric_setting_device_fields` | rename mislabelled `device_location` singles fields to `*_device_sn` |
| `backfill_device_location_fields` | rename/backfill device location & serial fields after the picker rework |
| `rename_biometric_setting_company_field` | `company` Check → `scope_company` (collides with an ERPNext validate hook) |
| `migrate_device_farm_to_farms` | single-farm `farm` Link → comma-separated `farms` |
| `backfill_bio_type_codes` | recover each device's reported BIODATA `Type=` from the stored raw logs |

---

## Releases & CI

Versioning is automated with **semantic-release** driven by **Conventional
Commits**; `__version__` in `upande_ta/__init__.py` is the source of truth. On
every push to `main` the release workflow computes the next version, commits the
bump, and cuts the tag and GitHub Release.

`feat:` bumps MINOR; `fix:` / `perf:` / `revert:` bump PATCH; housekeeping types
release nothing. **MAJOR is never bumped automatically** — `releaseRules` sets
`{"breaking": true, "release": false}` because the major tracks the Frappe
version, so majors are tagged by hand. The version comes from what lands on
`main`, so **squash & merge** with a Conventional-Commit **PR title** (that is
what the `Semantic Commits` workflow lints); merge commits usually produce no
bump. If `main` is protected, `GITHUB_TOKEN` cannot push the bump — add a
`RELEASE_TOKEN` PAT secret and switch the two token references.

`ci.yml` runs a **deploy simulation** on every PR and push to `main`: init a
Frappe `version-16` bench, `get-app` ERPNext + HRMS + this app, `new-site`,
`install-app`, `bench migrate` (exercising `after_migrate` and the patches), and
`bench run-tests --app upande_ta`. Install/migrate breakage therefore fails CI
before it reaches Frappe Cloud.

---

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app upande_ta
```

Requires a **Frappe v16** bench with ERPNext and HRMS. The app configures itself
on install (desktop icon, dashboard block, Leave Type abbreviation field,
Overtime setup); configure devices and the Node-RED bridge under **Biometric
Setting**.

Contributions use `pre-commit` (ruff, eslint, prettier, pyupgrade):

```bash
cd apps/upande_ta && pre-commit install
```

## License

mit
