frappe.provide("upande_ta.blocks.ta_dashboard");

upande_ta.blocks.ta_dashboard.mount = function (root_element) {
  const stats_api    = "upande_ta.upande_ta.api.dashboard.get_ta_dashboard_stats";
  const options_api  = "upande_ta.upande_ta.api.dashboard.get_ta_dashboard_filter_options";
  const checkins_api = "upande_ta.upande_ta.api.dashboard.get_ta_dashboard_checkins";
  const $  = (sel) => root_element.querySelector(sel);
  const setText = (key, value) => {
    const el = root_element.querySelector(`[data-ta="${key}"]`);
    if (el) el.textContent = value;
  };

  const FILTERS = ["company", "farm", "department", "designation", "employee"];
  const today = frappe.datetime.get_today();

  const state = {
    date: today,
    company: "", farm: "", department: "", designation: "", employee: ""
  };
  const enabled = { company: false, farm: false, department: false, designation: false, employee: false };

  function applyEnabledVisibility() {
    FILTERS.forEach(key => {
      const wrap = root_element.querySelector(`[data-ta-filter-wrap="${key}"]`);
      if (wrap) wrap.style.display = enabled[key] ? "" : "none";
      if (!enabled[key]) state[key] = "";
    });
  }

  function selectEl(key) { return root_element.querySelector(`[data-ta-filter="${key}"]`); }

  function rangeLabel() {
    return state.date ? `(${frappe.datetime.global_date_format(state.date)})` : "";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  const pager = { rows: [], page: 1, size: 50 };

  // Tile → table filtering. `category` is the active tile; catRows holds the row
  // list per category returned by the check-ins API.
  let category = "present";
  let catRows = { present: [], absent: [], leave: [], weekly_off: [] };
  const CAT_TITLES = {
    present: "Check-Ins / Check-Outs",
    absent: "Absent Employees",
    leave: "Employees On Leave",
    weekly_off: "Employees On Weekly Off",
  };
  const CAT_EMPTY = {
    present: "No check-in data for this date.",
    absent: "No absent employees for this date.",
    leave: "No employees on leave for this date.",
    weekly_off: "No employees on weekly off for this date.",
  };

  let sortCol = null, sortDir = 1;
  const STATUS_CLASS = { Present: "ta-st-present", Absent: "ta-st-absent", WO: "ta-st-wo" };
  function statusBadge(status) {
    if (!status) return '<span class="ta-muted">—</span>';
    const cls = STATUS_CLASS[status] || "ta-st-leave";
    return `<span class="ta-status ${cls}">${escapeHtml(status)}</span>`;
  }

  function renderPage() {
    const tbody = root_element.querySelector("[data-ta-table-body]");
    if (!tbody) return;
    const pagerEl = root_element.querySelector("[data-ta-pager]");
    const rows = pager.rows;

    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="ta-empty">${CAT_EMPTY[category] || CAT_EMPTY.present}</td></tr>`;
      if (pagerEl) pagerEl.hidden = true;
      return;
    }

    const total = rows.length;
    const totalPages = Math.max(1, Math.ceil(total / pager.size));
    if (pager.page > totalPages) pager.page = totalPages;
    if (pager.page < 1) pager.page = 1;

    const start = (pager.page - 1) * pager.size;
    const end   = Math.min(start + pager.size, total);
    const slice = rows.slice(start, end);

    tbody.innerHTML = slice.map(r => `
      <tr class="ta-row" data-emp="${escapeHtml(r.employee || "")}">
        <td class="ta-col-empno">${escapeHtml(r.employee_number)}</td>
        <td>${escapeHtml(r.employee_name)}</td>
        <td>${escapeHtml(r.shift) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.designation) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.check_in) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.check_out) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.worked_hours) || '<span class="ta-muted">—</span>'}</td>
        <td>${statusBadge(r.status)}</td>
      </tr>
    `).join("");

    if (pagerEl) {
      pagerEl.hidden = false;
      const info = pagerEl.querySelector("[data-ta-page-info]");
      if (info) info.textContent = `${fmt(start + 1)}–${fmt(end)} of ${fmt(total)} (page ${pager.page} / ${totalPages})`;
      const setDisabled = (sel, disabled) => {
        const b = pagerEl.querySelector(sel);
        if (b) b.disabled = disabled;
      };
      setDisabled("[data-ta-page-first]", pager.page <= 1);
      setDisabled("[data-ta-page-prev]",  pager.page <= 1);
      setDisabled("[data-ta-page-next]",  pager.page >= totalPages);
      setDisabled("[data-ta-page-last]",  pager.page >= totalPages);
    }
  }

  function sortRows(rows) {
    if (!sortCol) return rows;
    const numeric = sortCol === "worked_hours";
    return rows.sort((a, b) => {
      let x = a[sortCol] == null ? "" : a[sortCol];
      let y = b[sortCol] == null ? "" : b[sortCol];
      if (numeric) return sortDir * ((parseFloat(x) || 0) - (parseFloat(y) || 0));
      return sortDir * String(x).toLowerCase().localeCompare(String(y).toLowerCase());
    });
  }

  function updateSortArrows() {
    root_element.querySelectorAll("[data-sort]").forEach(th => {
      const a = th.querySelector(".ta-sort-arrow");
      if (!a) return;
      if (th.getAttribute("data-sort") === sortCol) {
        a.textContent = sortDir === 1 ? "↑" : "↓";
        th.classList.add("is-sorted");
      } else {
        a.textContent = "↕";
        th.classList.remove("is-sorted");
      }
    });
  }

  function renderTable(rows) {
    pager.rows = sortRows(Array.isArray(rows) ? rows.slice() : []);
    pager.page = 1;
    renderPage();
  }

  function skeletonRows(n) {
    const row = '<tr class="ta-skel-row">' + '<td><span class="ta-skel"></span></td>'.repeat(8) + "</tr>";
    return row.repeat(n);
  }

  // KPI keys fed by the (fast) stats call vs the check-ins call, so each group's
  // skeleton clears when its own request returns.
  // Check-Ins / Check-Outs move to the ATT group: they now come from the same
  // (overnight-aware) check-ins call as the table, so the date filter, the tiles
  // and the rows all reflect one consistent computation.
  const STATS_KEYS = ["total_employees", "devices_online"];
  const ATT_KEYS = ["unique_in", "unique_out", "absent", "leave", "weekly_off"];

  function setLoading(keys, on) {
    keys.forEach(k => {
      const el = root_element.querySelector(`[data-ta="${k}"]`);
      const kpi = el && el.closest(".ta-kpi");
      if (kpi) kpi.classList.toggle("is-loading", on);
    });
  }

  function showLoading() {
    setLoading(STATS_KEYS.concat(ATT_KEYS), true);
    const tbody = root_element.querySelector("[data-ta-table-body]");
    if (tbody) tbody.innerHTML = skeletonRows(8);
    const pagerEl = root_element.querySelector("[data-ta-pager]");
    if (pagerEl) pagerEl.hidden = true;
  }

  function renderCategory() {
    renderTable(catRows[category] || []);
    const heading = root_element.querySelector("[data-ta-table-heading]");
    if (heading) heading.textContent = CAT_TITLES[category] || CAT_TITLES.present;
    root_element.querySelectorAll("[data-ta-cat]").forEach(t => {
      t.classList.toggle("is-active", t.getAttribute("data-ta-cat") === category);
    });
  }

  // ── Per-employee attendance modal (defaults to the payroll window) ──
  const grid_api = "upande_ta.upande_ta.api.dashboard.get_ta_dashboard_employee_grid";
  let modalEmp = null;
  let mFromCtrl = null, mToCtrl = null;
  function tam(key) { return root_element.querySelector(`[data-tam="${key}"]`); }

  function fetchGrid(from, to) {
    if (!modalEmp) return;
    frappe.call({
      method: grid_api,
      args: { employee: modalEmp, from_date: from || null, to_date: to || null },
      callback: (r) => { if (r && r.message) renderGrid(r.message); },
    });
  }

  function renderGrid(d) {
    tam("name").textContent = `${d.employee_name}  |  ${d.employee_number || d.employee}`;
    tam("sub").textContent = [d.shift, d.designation].filter(Boolean).join("   ·   ");
    if (mFromCtrl) mFromCtrl.set_value(d.start || "");
    if (mToCtrl) mToCtrl.set_value(d.end || "");

    const days = d.days || [], full = d.daysfull || [], codes = d.codes || [];
    let head = "<thead><tr>", body = "<tbody><tr>";
    for (let i = 0; i < days.length; i++) {
      head += `<th title="${escapeHtml(full[i] || "")}">${escapeHtml(days[i])}</th>`;
      const c = codes[i] || "";
      const cls = c === "P" ? "ta-st-present" : c === "A" ? "ta-st-absent"
        : c === "WO" ? "ta-st-wo" : (c ? "ta-st-leave" : "");
      body += `<td>${c ? `<span class="ta-status ${cls}">${escapeHtml(c)}</span>` : '<span style="color:#ccc">·</span>'}</td>`;
    }
    tam("grid").innerHTML = head + "</tr></thead>" + body + "</tr></tbody>";

    const c = d.counts || {};
    const tiles = [
      ["Present", c.present, "c-green"], ["Absent", c.absent, "c-red"],
      ["Weekly Off", c.weekly_off, "c-purple"], ["Leaves", c.leave, "c-blue"],
      ["WFH", c.wfh, ""],
    ];
    tam("tiles").innerHTML = tiles.map(x =>
      `<div class="ta-mtile"><div class="ta-mtile__l">${x[0]}</div><div class="ta-mtile__v ${x[2]}">${fmt(x[1])}</div></div>`
    ).join("");
  }

  function openModal(employee) {
    modalEmp = employee;
    const modal = root_element.querySelector("[data-ta-modal]");
    if (!modal) return;
    modal.hidden = false;
    tam("name").textContent = "Loading…";
    tam("sub").textContent = "";
    tam("tiles").innerHTML = "";
    tam("grid").innerHTML = '<tbody><tr><td style="padding:14px;color:#888">Loading…</td></tr></tbody>';
    fetchGrid(null, null);
  }

  function closeModal() {
    const m = root_element.querySelector("[data-ta-modal]");
    if (m) m.hidden = true;
    modalEmp = null;
  }

  function fmt(n) { return (n ?? 0).toLocaleString(); }

  function fillOptions(sel, values, current) {
    if (!sel) return;
    const opts = ['<option value="">All</option>'];
    values.forEach(v => {
      const label = typeof v === "object" ? (v.label || v.value) : v;
      const value = typeof v === "object" ? v.value : v;
      const safeVal = String(value).replace(/"/g, "&quot;");
      const safeLabel = String(label).replace(/</g, "&lt;");
      opts.push(`<option value="${safeVal}">${safeLabel}</option>`);
    });
    sel.innerHTML = opts.join("");
    if (current && Array.from(sel.options).some(o => o.value === current)) {
      sel.value = current;
    } else {
      sel.value = "";
      if (current) state[sel.dataset.taFilter] = "";
    }
  }

  // Build a Frappe Date control (text field + popup calendar) — avoids the native
  // <input type="date"> segment-highlight-on-click and matches Desk styling.
  function makeDateControl(parent, onChange) {
    const control = frappe.ui.form.make_control({
      df: { fieldtype: "Date", fieldname: "ta_date", placeholder: "Select date" },
      parent: parent,
      render_input: true,
      only_input: true,
    });
    control.refresh();
    if (control.$input && onChange) control.$input.on("change", onChange);
    return control;
  }

  function mountDateControl() {
    const slot = root_element.querySelector('[data-ta-date="date"]');
    if (!slot) return null;
    slot.innerHTML = "";
    const control = makeDateControl(slot, () => {
      const v = control.get_value() || today;
      if (v === state.date) return;
      state.date = v;
      load();
    });
    control.set_value(state.date);
    if (control.$input) control.$input.attr("max", today);
    return {
      set_value: (v) => control.set_value(v || ""),
      get_value: () => control.get_value(),
    };
  }

  let date_ctrl;

  function loadFilterOptions() {
    return new Promise((resolve) => {
      frappe.call({
        method: options_api,
        args: {
          company:     state.company     || null,
          farm:        state.farm        || null,
          department:  state.department  || null,
          designation: state.designation || null,
        },
        callback: (r) => {
          const d = (r && r.message) || {};
          const ef = d.enabled_filters || {};
          FILTERS.forEach(k => { enabled[k] = !!ef[k]; });
          applyEnabledVisibility();
          fillOptions(selectEl("company"),     d.companies     || [], state.company);
          fillOptions(selectEl("farm"),        d.farms         || [], state.farm);
          fillOptions(selectEl("department"),  d.departments   || [], state.department);
          fillOptions(selectEl("designation"), d.designations  || [], state.designation);
          fillOptions(selectEl("employee"),    d.employees     || [], state.employee);
          resolve();
        },
        error: () => {
          // Fail closed: if settings can't be read, keep every toggleable filter hidden.
          FILTERS.forEach(k => { enabled[k] = false; });
          applyEnabledVisibility();
          resolve();
        },
      });
    });
  }

  function load() {
    setText("range_label", rangeLabel());
    showLoading();
    const scopedArgs = {
      company:     (enabled.company     && state.company)     || null,
      farm:        (enabled.farm        && state.farm)        || null,
      department:  (enabled.department  && state.department)  || null,
      designation: (enabled.designation && state.designation) || null,
      employee:    (enabled.employee    && state.employee)    || null,
    };
    frappe.call({
      method: stats_api,
      args: { from_date: state.date || null, to_date: state.date || null, ...scopedArgs },
      callback: (r) => {
        const d = r && r.message;
        if (d) {
          setText("total_employees", fmt(d.total_employees));
          setText("devices_online", fmt(d.devices.online));
          setText("devices_total", fmt(d.devices.total));
          setText("updated_at", frappe.datetime.now_datetime());
        }
        setLoading(STATS_KEYS, false);
      },
      error: () => setLoading(STATS_KEYS, false),
    });
    frappe.call({
      method: checkins_api,
      args: { date: state.date || null, limit: 5000, ...scopedArgs },
      callback: (r) => {
        const m = (r && r.message) || {};
        const cats = m.categories || { present: m.rows || [] };
        catRows = {
          present: cats.present || [],
          absent: cats.absent || [],
          leave: cats.leave || [],
          weekly_off: cats.weekly_off || [],
        };
        const att = m.attendance || {};
        setText("unique_in", fmt(att.checked_in));
        setText("unique_out", fmt(att.checked_out));
        setText("absent", fmt(att.absent));
        setText("leave", fmt(att.leave));
        setText("weekly_off", fmt(att.weekly_off));
        setLoading(ATT_KEYS, false);
        renderCategory();
      },
      error: () => {
        catRows = { present: [], absent: [], leave: [], weekly_off: [] };
        setLoading(ATT_KEYS, false);
        renderCategory();
      },
    });
  }

  date_ctrl = mountDateControl();

  const refresh = $(".ta-refresh");
  const clearBtn = $(".ta-clear");

  if (refresh) refresh.addEventListener("click", () => { loadFilterOptions().then(load); });

  // Tile → table filter. Clicking a tile shows that category; clicking the active
  // one again returns to the default (checked-in) view.
  root_element.querySelectorAll("[data-ta-cat]").forEach(tile => {
    tile.addEventListener("click", () => {
      const cat = tile.getAttribute("data-ta-cat");
      category = (category === cat && cat !== "present") ? "present" : cat;
      renderCategory();
    });
  });

  // Sortable column headers (toggle asc / desc).
  root_element.querySelectorAll("[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.getAttribute("data-sort");
      if (sortCol === col) sortDir = -sortDir;
      else { sortCol = col; sortDir = 1; }
      updateSortArrows();
      renderTable(catRows[category] || []);
    });
  });

  // Row click → per-employee attendance modal.
  const tbodyEl = root_element.querySelector("[data-ta-table-body]");
  if (tbodyEl) {
    tbodyEl.addEventListener("click", (e) => {
      const tr = e.target.closest("tr.ta-row");
      const emp = tr && tr.getAttribute("data-emp");
      if (emp) openModal(emp);
    });
  }

  const fromWrap = tam("from-wrap"), toWrap = tam("to-wrap"), mClose = tam("close");
  const onModalDateChange = () => {
    fetchGrid(mFromCtrl && mFromCtrl.get_value(), mToCtrl && mToCtrl.get_value());
  };
  if (fromWrap) mFromCtrl = makeDateControl(fromWrap, onModalDateChange);
  if (toWrap) mToCtrl = makeDateControl(toWrap, onModalDateChange);
  if (mClose) mClose.addEventListener("click", closeModal);
  const modalEl = root_element.querySelector("[data-ta-modal]");
  if (modalEl) modalEl.addEventListener("click", (e) => { if (e.target === modalEl) closeModal(); });

  FILTERS.forEach(key => {
    const sel = selectEl(key);
    if (!sel) return;
    sel.addEventListener("change", () => {
      state[key] = sel.value || "";
      const cascades = ["company", "farm", "department", "designation"];
      if (cascades.includes(key)) {
        loadFilterOptions().then(load);
      } else {
        load();
      }
    });
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      FILTERS.forEach(k => { state[k] = ""; const s = selectEl(k); if (s) s.value = ""; });
      state.date = today;
      date_ctrl && date_ctrl.set_value(state.date);
      loadFilterOptions().then(load);
    });
  }

  const pagerEl = root_element.querySelector("[data-ta-pager]");
  if (pagerEl) {
    const sizeSel = pagerEl.querySelector("[data-ta-page-size]");
    if (sizeSel) {
      pager.size = parseInt(sizeSel.value, 10) || 50;
      sizeSel.addEventListener("change", () => {
        pager.size = parseInt(sizeSel.value, 10) || 50;
        pager.page = 1;
        renderPage();
      });
    }
    const bind = (sel, fn) => {
      const b = pagerEl.querySelector(sel);
      if (b) b.addEventListener("click", () => { fn(); renderPage(); });
    };
    bind("[data-ta-page-first]", () => { pager.page = 1; });
    bind("[data-ta-page-prev]",  () => { pager.page = Math.max(1, pager.page - 1); });
    bind("[data-ta-page-next]",  () => { pager.page = pager.page + 1; });
    bind("[data-ta-page-last]",  () => { pager.page = Math.ceil((pager.rows.length || 1) / pager.size); });
  }

  loadFilterOptions().then(load);
};
