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

  function renderPage() {
    const tbody = root_element.querySelector("[data-ta-table-body]");
    if (!tbody) return;
    const pagerEl = root_element.querySelector("[data-ta-pager]");
    const rows = pager.rows;

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="ta-empty">No check-in data for this date.</td></tr>';
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
      <tr>
        <td class="ta-col-empno">${escapeHtml(r.employee_number)}</td>
        <td>${escapeHtml(r.employee_name)}</td>
        <td>${escapeHtml(r.shift) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.designation) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.check_in) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.check_out) || '<span class="ta-muted">—</span>'}</td>
        <td>${escapeHtml(r.worked_hours) || '<span class="ta-muted">—</span>'}</td>
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

  function renderTable(rows) {
    pager.rows = Array.isArray(rows) ? rows : [];
    pager.page = 1;
    renderPage();
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

  function mountDateControl() {
    const slot = root_element.querySelector('[data-ta-date="date"]');
    if (!slot) return null;
    slot.innerHTML = "";
    const input = document.createElement("input");
    input.type = "date";
    input.className = "form-control form-control-sm ta-date-input";
    input.value = state.date;
    input.max = today;
    const openPicker = () => {
      if (typeof input.showPicker === "function") {
        try { input.showPicker(); } catch (e) { /* gesture-restricted, ignore */ }
      }
    };
    input.addEventListener("mousedown", (e) => { e.preventDefault(); input.focus(); openPicker(); });
    input.addEventListener("focus", openPicker);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPicker(); }
    });
    input.addEventListener("change", () => {
      const v = input.value || today;
      if (v === state.date) return;
      state.date = v;
      load();
    });
    slot.appendChild(input);
    return {
      set_value: (v) => { input.value = v || ""; },
      get_value: () => input.value,
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
        if (!d) return;
        setText("unique_in", fmt(d.window.unique_in));
        setText("unique_out", fmt(d.window.unique_out));
        setText("total_employees", fmt(d.total_employees));
        setText("devices_online", fmt(d.devices.online));
        setText("devices_total", fmt(d.devices.total));
        setText("updated_at", frappe.datetime.now_datetime());
      },
    });
    frappe.call({
      method: checkins_api,
      args: { date: state.date || null, limit: 5000, ...scopedArgs },
      callback: (r) => renderTable((r && r.message && r.message.rows) || []),
    });
  }

  date_ctrl = mountDateControl();

  const refresh = $(".ta-refresh");
  const clearBtn = $(".ta-clear");

  if (refresh) refresh.addEventListener("click", () => { loadFilterOptions().then(load); });

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
