# Upande TA (T&A)

**Upande Time & Attendance** — a Frappe/ERPNext app that turns ZKTeco biometric
devices into ERPNext Employee Checkins, and layers HR tooling on top of HRMS:
bulk overtime payroll, bulk weekly-off reassignment, a live attendance
dashboard, and an enhanced Monthly Attendance Sheet.

> **Compatibility:** Frappe/HRMS **v16 only** (`>=16.0.0,<17.0.0`). The app
> overrides HRMS **Overtime Slip**, which does not exist in HRMS v15. See
> [Compatibility](#compatibility).

---

## Table of contents

- [Architecture overview](#architecture-overview)
- [1. Biometric device integration](#1-biometric-device-integration)
- [2. HR overrides](#2-hr-overrides)
- [3. Bulk Overtime](#3-bulk-overtime)
- [4. Bulk Week Off](#4-bulk-week-off)
- [5. TA Dashboard](#5-ta-dashboard)
- [App lifecycle & scheduled jobs](#app-lifecycle--scheduled-jobs)
- [Compatibility](#compatibility)
- [Known limitations](#known-limitations)
- [Data-migration patches](#data-migration-patches)
- [Installation](#installation)
- [Contributing](#contributing)
- [License](#license)

---

## Architecture overview

The app does **not** talk to biometric hardware directly (no `pyzk`/socket
code). It uses a **push/relay model** built around **Node-RED** and ZKTeco
"PUSH SDK" text commands:

```
                    HTTP POST (JSON command strings)
   ┌───────────┐   ───────────────────────────────►   ┌──────────┐   ┌─────────────┐
   │  Frappe   │        "C:<id>:DATA QUERY ATTLOG…"     │ Node-RED │◄─►│ ZKTeco      │
   │ (upande_ta│                                        │  relay   │   │ devices     │
   │   app)    │   ◄───────────────────────────────     │          │   │ (by serial) │
   └───────────┘    allow_guest endpoints + Employee    └──────────┘   └─────────────┘
                    Checkin REST API (device → Frappe)
```

- **Outbound** (Frappe → devices): the app builds ZKTeco command strings and
  POSTs them as JSON to a Node-RED endpoint. The single bridge is
  `_post_to_nodered()` in `biometric_user.py` — it reads `server_ip`,
  `server_port`, `end_point` from the **Biometric Setting** single and POSTs
  with a 10 s timeout; failures are logged and swallowed, never raised.
- **Inbound** (devices → Frappe): Node-RED holds the real device connections and
  pushes data back by calling this app's `allow_guest` endpoints
  (`store_biotemplate`, `store_device_status`) and — for punches — Frappe's
  **standard Employee Checkin REST API**.
- **Device identity** is the **serial number** (`device_sn`), not an IP.
- **Employee mapping** is `Employee.attendance_device_id` = the device PIN
  (`user_id`), Frappe's built-in checkin linkage field.

Everything the app configures lives on the **Biometric Setting** single
(Server Settings, device registry, schedules, dashboard filter toggles).

---

## 1. Biometric device integration

### Doctypes

| Doctype | Kind | Role |
|---|---|---|
| **Biometric Setting** | Single | Control panel & orchestrator: server config, `devices` registry, `poll_devices` table, schedules, dashboard filter toggles. |
| **Biometric Device** | Child (`devices` table) | Device registry row: `device_sn`, `device_location`, `farms` (comma-sep Farm names), `status` (Online/Offline), `last_seen`. |
| **Biometric Checkin** | Child (`poll_devices` table) | Manual poll request row: `device`, `device_sn`, `command_id`, `status`. |
| **Biometric User** | Parent (1 per device, `{device_sn}-{device_location}`) | Enrollment **roster** — which PINs/employees are on a device. |
| **Bio User** | Child (`users` table of Biometric User) | One row per enrolled PIN: `user_id`, `employee`, `privilege`, `status`. |
| **Biometric Template** | Parent (1 per device) | The biometric **payloads** per employee. |
| **Bio Template** | Child (`bio_templates` table) | Fingerprint/face/palm templates + card/password/verify settings. |
| **Biometric Logs** | Standalone | Legacy/manual attendance log; not written by current code (superseded by Employee Checkin). |

`Biometric User` / `Biometric Template` are the **parents**; `Bio User` /
`Bio Template` are their **child rows** (parent-vs-child, both current — not
legacy-vs-new). Parent docs cannot be created manually — they are
auto-provisioned per device on Biometric Setting save, guarded by a
`before_insert` flag.

### Configuration & device status

- Devices are registered in **Biometric Setting → Server Settings → `devices`**.
  Each row requires `device_sn`, `device_location`, `farms`.
- On save, `BiometricSetting.on_update` runs `_sync_scheduled_jobs()`,
  `_ensure_biometric_user_parents()` (auto-create the two parent docs per
  device), and `_normalize_poll_device_values()` (keep location↔serial pickers
  in sync).
- **Removing a device is guarded**: `_block_removing_devices_with_links()`
  refuses if the device still has Bio User / Bio Template rows, and shows links
  to clean up first.
- **Online status**: Node-RED calls `store_device_status()` (heartbeat) which
  sets `status="Online"` + `last_seen`. A device is considered **offline** if
  it hasn't sent a heartbeat in **> 1 minute** (`OFFLINE_THRESHOLD_MINUTES`).
  `mark_stale_devices_offline_scheduled()` runs **every minute** (cron in
  `hooks.py`) and publishes a realtime event so the form badges update live.

### Check-in pull flow

1. **Request punches** — Frappe → Node-RED → device:
   - **Manual:** `poll_devices()` builds
     `C:<id>:DATA QUERY ATTLOG\tStartTime=…\tEndTime=…` per `poll_devices` row
     and POSTs to Node-RED.
   - **Scheduled:** `run_checkin()` computes a rolling window from
     `checkin_event_frequency` and POSTs one ATTLOG query per device.
2. **Return punches** — Node-RED creates **Employee Checkin** records via
   Frappe's standard API (device→employee via `attendance_device_id`).
3. **Deduplicate** — `Employee Checkin.validate` → `prevent_duplicate()` rejects
   any checkin with the same `employee` + `time` (+ matching `log_type`,
   treating blank/None as equivalent), making repeated ATTLOG pulls idempotent.
4. **IN/OUT normalization ("flip")** — ZKTeco punches often all arrive as IN.
   `auto_close_open_ins()` groups scans by (employee, working-day) and flips a
   trailing still-open IN to OUT. **Overnight shifts** (Shift Type with
   `start_time > end_time`) are paired across midnight so night staff aren't
   split by calendar day. Triggered scheduled (`run_flip_last_in`) or manually
   (`flip_checkins_for_date`, HR/System Manager only).

### Enrollment sync

- **Templates in** (device → Frappe): `store_biotemplate()` (`allow_guest`)
  upserts a Bio Template row keyed by (parent, employee), honoring an optional
  per-device PIN filter.
- **Request templates** (Frappe → device): `run_biodata_sync()` /
  `request_biodata*` POST five ZKTeco queries per device (FINGERTMP, FACE,
  BIOPHOTO, USERINFO, palm).
- **Roster management** (Frappe → device): `bulk_command()` is the core engine —
  Add/Update/Delete users, with **farm scoping** (Add/Update only for employees
  whose `custom_farm` ∈ the device's `farms`), pushing USERINFO + BIODATA
  commands. `bulk_command_per_device()` adds MariaDB deadlock retry/backoff.
- **PIN changes**: `Employee.on_update` → `handle_pin_change()` re-keys all Bio
  User / Bio Template rows and **enqueues** a background device resync (so a dead
  device can't block the Employee save).

### Key whitelisted endpoints

`store_biotemplate`, `store_device_status` (both `allow_guest`, called by
Node-RED); `poll_devices`, `request_biodata*`, `resync_scheduled_jobs`,
`get_scheduled_job_links`, `get_device_statuses`, `flip_checkins_for_date`,
`bulk_command*`, `get_device_users*`, `get_employees`, `hydrate_users_from_templates`.

---

## 2. HR overrides

Wired via `hooks.py` (`doc_events`, `override_doctype_class`, `before_request`,
`before_job`).

### Employee — device PIN defaulting

- `set_attendance_device_id` (before_save / after_insert): if
  `attendance_device_id` is blank, default it to the Employee ID (`name`).
- `sync_attendance_device_id_change` (on_update): when the PIN changes,
  propagate via `handle_pin_change()` (re-key enrollments + resync devices).
- The Employee form JS (`public/js/employee.js`) adds "Add/Update/Delete on
  Device" buttons and auto-syncs on new/deactivated/reactivated employees.

### Employee Checkin — duplicate prevention

`prevent_duplicate` blocks a second checkin with identical employee + timestamp
+ log type — protects against biometric double-punches at the same second.

### Leave Type — abbreviation field

- Adds a custom **`abbreviation`** Data field (len 6) to Leave Type
  (`ensure_abbreviation_field`, on install/migrate; removed on uninstall).
- `generate_leave_abbr()` auto-builds a unique abbreviation from the leave
  name's word initials (e.g. "Maternity Leave" → "ML"), with collision
  suffixing. Existing non-blank values are left untouched.
- Consumed by the Monthly Attendance Sheet to label leave-day cells and the
  color legend.

### Monthly Attendance Sheet — enhanced report (monkey-patch)

`apply_patch()` (run from `before_request` / `before_job`, idempotent) replaces
five functions on the stock HRMS report module. Changes:

- **Row grouping by *assigned* shift** — resolves each (employee, date) to the
  employee's submitted, active **Shift Assignment** (falling back to
  `default_shift`), collapsing the split rows that different attendance sources
  otherwise produce.
- **Per-leave-type abbreviations** in day cells (using the Leave Type
  `abbreviation` field) instead of a generic "L".
- **Chart & legend** recomputed for the new "On Leave|<type>" encoding and
  half-day split; one legend chip per leave type.
- **Server-side summary rows** appended per view: Present / Absent / Half Day /
  On Leave / Holiday / Weekly Off counts + Total Headcount, each tagged
  `_is_summary`.

The bundled JS (`monthly_attendance_sheet_colors.bundle.js`, loaded globally,
acts only on this report) **color-codes** day cells (P/WFH green, A red, HD/P
purple, HD/A orange, H/WO grey, any leave abbreviation blue), bolds and styles
the summary block, and **pins summary rows to the bottom** on client-side sort.

### Overtime Slip — `UpandeOvertimeSlip`

Only diverges for **bulk** slips (those with a `custom_bulk_overtime` link);
standard manual slips keep 100% native behavior.

- `validate`: skips native duplicate-date / overtime-type / max-hours checks
  (bulk amounts are pre-computed), keeps date + overlap guards, recomputes total.
- `on_submit` → `_create_bulk_additional_salary`: sums pre-computed
  `custom_amount` per salary component and creates + submits one **Additional
  Salary** per component.
- `on_cancel`: cancels those Additional Salary records.

### Workspace — protect the "T&A" workspace

`validate` forbids hiding / un-publishing the **T&A** workspace; `on_trash`
blocks deletion (except during install/migrate/uninstall).

---

## 3. Bulk Overtime

Raise overtime pay for many employees at once for one pay period.

**Doctype:** `Bulk Overtime` (submittable, `HR-BOT-.YYYY.-`) with a
`bulk_overtime_entries` child table (`Bulk Overtime Entry`: employee,
`overtime_date`, `overtime_type`, `hours_requested`, `hours_done`, `row_status`,
`verification_type` Biometric/Manual, `normal_hours`, `holiday_hours`).

**Constants:** `WORKING_HOURS_PER_MONTH = 199.33`; overtime types map to
multipliers — "Overtime 1.5" → 1.5, "Overtime 2.0" → 2.0 — each tied to a
like-named Salary Component.

**Flow:**

- `fill_employee_details` populates the grid from Active employees by
  department/designation.
- `validate`: date sanity, overlap guard against other Bulk Overtime for the
  same company, and Manual rows must carry hours.
- `on_submit` → `create_overtime_slips`: per row, looks up the employee's
  Salary Structure Assignment `base`, computes `hourly_rate = base / 199.33`,
  creates an **Overtime Slip** linked back via `custom_bulk_overtime`, appends a
  detail row per applicable type with
  `custom_amount = round(hourly_rate × multiplier × hours, 2)`, then submits it.
- The Overtime Slip override then pays each out as **Additional Salary**.
- `on_cancel` → `cancel_overtime_slips` unwinds the submitted slips (and legacy
  Additional Salary references).

**Chain:** `Bulk Overtime → Overtime Slip → Additional Salary` (cancel unwinds
in reverse).

**Setup** (`ensure_overtime_setup`, on migrate): idempotently creates the custom
fields (`custom_salary_component`, `custom_amount` on Overtime Details;
`custom_bulk_overtime` on Overtime Slip) and the two Overtime Type records.

> ⚠️ The **biometric auto-calculation** path (`get_overtime_hours` and its
> callers) is currently **under construction / not functional** — see
> [Known limitations](#known-limitations). The **Manual** entry path and the
> slip/Additional-Salary generation are coherent.

---

## 4. Bulk Week Off

Reassign employees' weekly-off day in bulk by moving them onto a different
Holiday List, effective a chosen date, recorded as HRMS **Employee Transfer** +
**Holiday List Assignment** so both old and new weekly-offs render correctly
per-date.

**Doctype:** `Bulk Week Off` (submittable, `BWO-.#####`) with an `employees`
child table (`Bulk Week Off Detail`: employee, `current_holiday_list`,
`assigned_off_day`, output links `employee_transfer`, `holiday_list_assignment`).

**Flow:**

- `get_employees` seeds the grid; `validate` checks no duplicate employees and
  that `from_date` falls inside the relevant Holiday List periods.
- `on_submit` → `create_employee_transfers`: per row, creates an Employee
  Transfer changing `holiday_list` effective `from_date`, plus a forward Holiday
  List Assignment (and backfills a prior assignment so pre-transfer dates keep
  the old weekly off). Transfers dated **today or earlier** are submitted
  immediately; **future-dated** transfers stay Draft (HRMS won't submit a
  future-dated transfer) to be auto-submitted later.
- `on_cancel` → `cancel_employee_transfers` reverts the HLAs and transfers and
  restores `Employee.holiday_list`.

**Daily cron** `submit_due_employee_transfers` (`0 0 * * *`) submits Draft
transfers whose date has arrived, and links any Holiday List Assignment a prior
transient failure skipped. Each row commits independently.

---

## 5. TA Dashboard

A custom **HTML block** embedded in the T&A workspace, rendered by
`blocks.render_ta_dashboard` (returns server-rendered `html` + compiled `css`),
driven by three whitelisted endpoints in `api/dashboard.py`:

- **`get_ta_dashboard_stats`** — KPI counts from Employee Checkin over a date
  range: IN/OUT/total/unique for the window and for today, a per-day series,
  device online/total, and scoped total employees.
- **`get_ta_dashboard_checkins`** — the attendance roster for a single date with
  **overnight-shift pairing** (evening IN ↔ morning OUT), worked-hours, and an
  attendance breakdown classifying non-present staff as **Leave** / **Weekly
  Off** / **Absent**.
- **`get_ta_dashboard_filter_options`** — cascading dropdown options
  (company → farm → department → designation → employee); which filters render
  is controlled by toggle checkboxes on Biometric Setting.

All endpoints scope by company / farm / department / designation / employee. The
`farm` filter degrades gracefully when `Employee.custom_farm` is absent.

---

## App lifecycle & scheduled jobs

**`after_install`** — desktop icon, TA Dashboard block, Leave Type abbreviation
field, SCP Stock Entry custom fields.

**`after_migrate`** (in order) — sanitize link filters, resync scheduled jobs,
desktop icon, dashboard block, abbreviation field, SCP Stock Entry custom
fields, `cleanup.remove_orphans`, `ensure_overtime_setup`.

**`before_uninstall`** — remove the Leave Type abbreviation field and the SCP
Stock Entry custom fields.

**`cleanup.remove_orphans`** deletes standard records (Reports, Pages, Print
Formats, Notifications, Dashboards/Charts/Number Cards, Custom HTML Blocks,
public Workspaces) attributed to module "Upande TA" that are no longer shipped
in source. Orphan **DocTypes are only logged**, never auto-dropped.

**Scheduled jobs:**

| Cadence | Job | Purpose |
|---|---|---|
| every minute (`* * * * *`) | `mark_stale_devices_offline_scheduled` | flip devices offline after >1 min silence |
| daily midnight (`0 0 * * *`) | `submit_due_employee_transfers` | auto-submit due Bulk Week Off transfers |
| configurable | `run_checkin` | poll ATTLOG from devices |
| configurable | `run_biodata_sync` | request biometric templates |
| configurable | `run_flip_last_in` | flip trailing IN → OUT |

The three configurable jobs are per-setting **Scheduled Job Type** rows created
from Biometric Setting frequency/cron fields, re-synced on migrate via
`resync_scheduled_jobs`.

---

## Compatibility

- **Frappe / HRMS: v16 only** — declared in `pyproject.toml`:

  ```toml
  [tool.bench.frappe-dependencies]
  frappe = ">=16.0.0,<17.0.0"
  ```

  Frappe Cloud requires this bounded, comma-separated declaration; without it
  the app is rejected with *"invalid version format … use NPM-style semver
  ranges"*.
- **Not v15-compatible**: the app overrides HRMS **Overtime Slip**, which does
  not exist in HRMS v15. Supporting v15 would require making the Overtime Slip
  override conditional on the doctype existing.
- Python `>=3.10`.

---

## Known limitations

- **Bulk Overtime biometric auto-calc is non-functional as written.**
  `get_overtime_hours()` (in `bulk_overtime.py`) is internally inconsistent
  (references to undefined locals, uninitialized result dict, no return), so the
  `{employee: {normal, holiday}}` map its callers expect is never produced.
  `revalidate_biometric_hours` and `sync_attendance_data` depend on it. The
  **Manual** hours path and slip/Additional-Salary generation work; the
  biometric auto-calc path needs a fix before use.
- `get_overtime_type` is referenced in `bulk_overtime.py` but not defined in the
  file — the affected code paths will raise `NameError` unless it is supplied
  elsewhere.
- `on_submit`'s biometric revalidation is gated on `auto_validate_worked_hours`,
  which is not a field on the doctype, so that branch is dormant unless added as
  a custom field.

---

## Data-migration patches

`patches/v1` (run on migrate; see `patches.txt`):

1. **sanitize_link_filters** — null invalid `link_filters`, drop the
   `json_valid` CHECK that legacy `''` values trip on.
2. **migrate_biometric_user_to_child** — flat Biometric User docs → parent +
   `users` child rows (with backup table).
3. **restore_biometric_setting** — optional snapshot restore onto an empty site
   (OFF by default; env-gated).
4. **dedupe_and_lock_bio_template** — dedupe Bio Template rows by
   (parent, employee) + add a UNIQUE index.
5. **rename_biometric_setting_device_fields** — rename mislabelled
   `device_location`/`biodata_device_location` singles fields to `*_device_sn`.
6. **backfill_device_location_fields** — rename/backfill device location &
   serial fields after the picker rework.
7. **rename_biometric_setting_company_field** — rename the `company` Check field
   to `scope_company` (avoids a collision with an ERPNext global validate hook).
8. **migrate_device_farm_to_farms** — single-farm `farm` Link → multi-farm
   `farms` comma-separated field.

---

## Versioning & releases

Versioning is automated with **[semantic-release](https://semantic-release.gitbook.io/)**
driven by **[Conventional Commits](https://www.conventionalcommits.org/)** —
the same model ERPNext/Frappe use. The single source of truth for the version
is `__version__` in `upande_ta/__init__.py`.

### How a release happens

On every push to **`main`**, the `Generate Semantic Release` workflow
(`.github/workflows/release.yml`) runs and, based on the commit messages since
the last release, it:

1. Computes the next version, then `sed`s it into `upande_ta/__init__.py`.
2. Commits `chore(release): Bumped to Version X` back to `main`.
3. Creates the git tag `vX` and a **GitHub Release** with auto-generated notes.

The `Release Notes` workflow (`.github/workflows/release_notes.yml`) then
regenerates the release body and strips housekeeping commits
(`chore/ci/test/docs/style`). PRs labelled `skip-release-notes` are excluded
from notes (`.github/release.yml`).

### Commit message → version bump

| Commit prefix | Example | Bump |
|---|---|---|
| `fix:` | `fix: prevent duplicate checkin race` | patch (`x.y.Z`) |
| `feat:` | `feat: bulk week-off scheduler` | minor (`x.Y.0`) |
| `chore:` / `ci:` / `docs:` / `style:` / `test:` / `refactor:` / `perf:` / `build:` | `docs: update README` | no release |

Breaking changes are **not** auto-bumped to a major (`releaseRules` in
`.releaserc.json`) — majors are managed manually, mirroring ERPNext (whose major
tracks the Frappe version). PR commit titles are enforced by the
`Semantic Commits` workflow (`.github/workflows/semantic-commits.yml` +
`commitlint.config.js`).

### Setup

Everything runs on GitHub Actions — **no local tooling and no manual secret are
required**. The workflows use the auto-provided `GITHUB_TOKEN`, so releases
happen automatically once these files are on `main` and you merge Conventional
Commits into it.

Optional:

- **Protected `main`** — if `main` is a protected branch, `GITHUB_TOKEN` cannot
  push the version-bump commit. Create a PAT secret `RELEASE_TOKEN` (repo +
  workflow scopes) and switch the two `GITHUB_TOKEN` lines in
  `release.yml`/`release_notes.yml` to `secrets.RELEASE_TOKEN`.
- **Baseline tag** — with no existing tag, the first release is **`v1.0.0`**. To
  keep the current `0.x` line instead, create a baseline tag once:
  `git tag v0.0.1 && git push origin v0.0.1`.

## upande_scp integration

`upande_ta` owns the biometric/employee-assignment DocTypes the **upande_scp**
store-keeper transfer flow depends on, so `upande_scp` only needs `upande_ta`,
`upande_core`, ERPNext, and Frappe.

- **Biometric Logs** (already present) — SCP reads it read-only for live
  finger-scan verification (`employee`, `employee_name`, `biometric_id`, `time`,
  `log_type`).
- **Employee Request** (child table) — used as `Stock Entry.employee_data`
  (transfer employee assignment): `employee`, `employee_name`.
- **Biometric Data** (child table) — used as `Stock Entry.biometric_data`
  (written on biometric-authorized submit): `employee`, `employee_name`,
  `biometric_id`.

The three Stock Entry custom fields — `employee_data`
(Table → Employee Request), `biometric_data` (Table → Biometric Data),
and `biometric_verified` (Check) — are **created programmatically** on
install/migrate and removed on uninstall (see
`overrides/stock_entry.py`), **not** shipped as fixtures.

## Continuous integration

`.github/workflows/ci.yml` runs a **deploy simulation** on every PR and every
push to `main`, driven by `.github/helper/install.sh`. It reproduces what
Frappe Cloud does on a new bench:

1. Init a **Frappe `version-16`** bench.
2. `get-app` **ERPNext** + **HRMS** (`version-16`) and this app.
3. `new-site`, then `install-app erpnext hrms upande_ta`.
4. `bench migrate` — exercises the `after_migrate` hooks and patches.
5. `bench run-tests --app upande_ta`.

If the app can't install/migrate on v16 (bad `frappe-dependencies`, a broken
`after_migrate` hook, an import that doesn't exist on the target version, etc.)
CI fails **before** it ever reaches Frappe Cloud — the same class of failures
this repo hit manually.

## Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app upande_ta
```

Requires a **Frappe v16** bench with **ERPNext** and **HRMS** installed.

After install, the app configures itself (desktop icon, TA Dashboard block,
Leave Type abbreviation field, Overtime setup). Configure devices and the
Node-RED bridge under **Biometric Setting**.

## Contributing

This app uses `pre-commit` for code formatting and linting. Please
[install pre-commit](https://pre-commit.com/#installation) and enable it for
this repository:

```bash
cd apps/upande_ta
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting
your code:

- ruff
- eslint
- prettier
- pyupgrade

## License

mit
