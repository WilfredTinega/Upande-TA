// Copyright (c) 2026, Upande LTD and contributors

/**
 * Styled dropdown for the desk dialogs' "Templates from" picker. A native
 * <select>'s open list is drawn by the browser and takes no CSS, so the select
 * is kept hidden (it still holds the value and fires "change") and a trigger +
 * body-level menu stand in for it. An option's data-sub is shown muted on the
 * right (the device serial). Desk theme variables keep it right in dark mode.
 *
 *   upande_ta.device_select.enhance(selectEl)
 */
frappe.provide("upande_ta.device_select");

(function () {
	const CHEVRON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
	const CHECK = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
	const SEARCH_FROM = 8;
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	let open_menu = null;

	function inject_styles() {
		if (document.getElementById("upande-ta-device-select-styles")) return;
		const style = document.createElement("style");
		style.id = "upande-ta-device-select-styles";
		style.textContent = `
			select.uds-native { display: none !important; }
			.uds {
				display: inline-flex; align-items: center; justify-content: space-between; gap: 8px;
				box-sizing: border-box; height: var(--input-height, 28px); min-width: 170px; max-width: min(380px, calc(100vw - 16px));
				padding: 0 8px 0 10px; margin: 0; line-height: 1;
				font-size: var(--text-base, 14px); font-weight: inherit; color: var(--text-color); text-align: left;
				background: var(--control-bg, var(--fg-color)); border: 1px solid transparent;
				border-radius: 8px; cursor: pointer;
				transition: border-color .15s, box-shadow .15s;
			}
			.uds:hover { border-color: var(--border-color); }
			.uds:focus-visible, .uds.open {
				outline: none; border-color: var(--primary, #2490ef);
				box-shadow: 0 0 0 2px var(--focus-default, rgba(36,144,239,.18));
			}
			.uds-text { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
			.uds.uds-empty .uds-text { color: var(--text-muted); }
			.uds-chev { flex: none; display: flex; color: var(--text-muted); transition: transform .15s; }
			.uds.open .uds-chev { transform: rotate(180deg); color: var(--text-color); }

			body > .uds-menu {
				position: fixed !important; z-index: 1100 !important; margin: 0 !important;
				box-sizing: border-box; max-width: min(380px, calc(100vw - 16px)); max-height: 300px !important; overflow: hidden !important;
				display: flex; flex-direction: column; outline: none;
				background: var(--fg-color); border: 1px solid var(--border-color); border-radius: 10px;
				box-shadow: var(--shadow-lg, 0 12px 32px -8px rgba(0,0,0,.25));
				animation: uds-in .12s ease-out;
			}
			@keyframes uds-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
			.uds-search { flex: none; padding: 6px; border-bottom: 1px solid var(--border-color); }
			.uds-search input {
				box-sizing: border-box; width: 100%; height: var(--input-height, 28px); padding: 4px 8px; font-size: var(--text-base, 14px); outline: none;
				color: var(--text-color); background: var(--control-bg, var(--fg-color));
				border: 1px solid transparent; border-radius: 8px;
			}
			.uds-search input:focus { border-color: var(--primary, #2490ef); }
			.uds-list { flex: 1 1 auto; overflow-y: auto; padding: 4px; }
			.uds-opt {
				display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 6px;
				cursor: pointer; font-size: var(--text-base, 14px); font-weight: inherit; color: var(--text-color); white-space: nowrap;
			}
			.uds-opt-text { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
			.uds-opt-sub { flex: none; font-family: var(--font-mono, monospace); font-size: var(--text-xs, 12px); color: var(--text-muted); }
			.uds-opt.active { background: var(--fg-hover-color, var(--gray-100)); }
			.uds-opt.selected { font-weight: 500; }
			.uds-opt.placeholder .uds-opt-text { color: var(--text-muted); }
			.uds-check { flex: none; display: flex; width: 14px; color: var(--primary, #2490ef); }
			.uds-none { padding: 8px; font-size: var(--text-base, 14px); color: var(--text-muted); }

			/* the dialog toolbars around the picker: one 28px row, desk type */
			.uds-toolbar {
				display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px;
				font-size: var(--text-base, 14px);
			}
			.uds-toolbar > .btn {
				box-sizing: border-box; height: var(--input-height, 28px); margin: 0;
				display: inline-flex; align-items: center; font-size: var(--text-base, 14px);
			}
			.uds-check-label, .uds-field, .uds-count {
				flex: none; display: inline-flex; align-items: center; height: var(--input-height, 28px);
				margin: 0; white-space: nowrap; font-size: var(--text-base, 14px); line-height: 1;
			}
			.uds-check-label { gap: 6px; padding: 0 4px; color: var(--text-color); cursor: pointer; }
			.uds-check-label input { margin: 0; }
			.uds-field { gap: 8px; padding-left: 4px; color: var(--text-muted); }
			.uds-count { margin-left: auto; color: var(--text-muted); font-variant-numeric: tabular-nums; }
		`;
		document.head.appendChild(style);
	}

	function close_menu() {
		if (!open_menu) return;
		open_menu.menu.remove();
		open_menu.trigger.classList.remove("open");
		open_menu.trigger.setAttribute("aria-expanded", "false");
		open_menu = null;
	}

	function option_html(o, i, on) {
		const sub = o.dataset.sub || "";
		return `<div class="uds-opt${on ? " selected" : ""}${o.value === "" ? " placeholder" : ""}" role="option" data-i="${i}" aria-selected="${on}">`
			+ `<span class="uds-check">${on ? CHECK : ""}</span>`
			+ `<span class="uds-opt-text">${esc(o.text)}</span>`
			+ (sub ? `<span class="uds-opt-sub">${esc(sub)}</span>` : "")
			+ `</div>`;
	}

	// The closed picker is as wide as its widest option, so it and the open
	// list line up exactly.
	function fit_width(sel, trigger) {
		const probe = document.createElement("div");
		probe.className = "uds-menu";
		probe.style.cssText = "visibility:hidden;left:-9999px;top:0;animation:none";
		probe.innerHTML = `<div class="uds-list">${
			Array.from(sel.options).map((o, i) => option_html(o, i, true)).join("")
		}</div>`;
		document.body.appendChild(probe);
		const w = Math.ceil(probe.getBoundingClientRect().width);
		probe.remove();
		if (w) trigger.style.width = w + "px";
	}

	function enhance(sel) {
		if (!sel || sel.__uds) return;
		inject_styles();
		sel.__uds = true;

		const trigger = document.createElement("button");
		trigger.type = "button";
		trigger.className = "uds";
		trigger.setAttribute("aria-haspopup", "listbox");
		trigger.setAttribute("aria-expanded", "false");
		trigger.innerHTML = `<span class="uds-text"></span><span class="uds-chev">${CHEVRON}</span>`;
		sel.parentNode.insertBefore(trigger, sel.nextSibling);
		sel.classList.add("uds-native");

		const sync = () => {
			const o = sel.options[sel.selectedIndex];
			trigger.querySelector(".uds-text").textContent = o ? o.text : "";
			trigger.title = o && o.dataset.sub ? `${o.text} (${o.dataset.sub})` : (o ? o.text : "");
			trigger.classList.toggle("uds-empty", !o || o.value === "");
			trigger.disabled = !!sel.disabled;
		};
		sel.addEventListener("change", sync);
		fit_width(sel, trigger);

		trigger.addEventListener("click", (e) => {
			e.stopPropagation();
			if (open_menu && open_menu.sel === sel) return close_menu();
			open(sel, trigger, sync);
		});
		trigger.addEventListener("keydown", (e) => {
			if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
				e.preventDefault();
				if (!open_menu) open(sel, trigger, sync);
			}
		});
		sync();
	}

	function open(sel, trigger, sync) {
		close_menu();
		const menu = document.createElement("div");
		menu.className = "uds-menu";
		menu.setAttribute("role", "listbox");
		const many = sel.options.length >= SEARCH_FROM;
		menu.innerHTML = (many ? `<div class="uds-search"><input type="text" placeholder="${__("Search")}…" autocomplete="off"></div>` : "")
			+ `<div class="uds-list"></div>`;
		document.body.appendChild(menu);
		const list = menu.querySelector(".uds-list");
		let active = -1;
		let shown = [];

		function render(q) {
			q = (q || "").toLowerCase().trim();
			shown = [];
			let html = "";
			for (let i = 0; i < sel.options.length; i++) {
				const o = sel.options[i];
				const sub = o.dataset.sub || "";
				if (q && !o.text.toLowerCase().includes(q) && !sub.toLowerCase().includes(q)) continue;
				shown.push(i);
				html += option_html(o, i, i === sel.selectedIndex);
			}
			list.innerHTML = html || `<div class="uds-none">${__("No matches")}</div>`;
			active = shown.indexOf(sel.selectedIndex);
			highlight();
		}
		function highlight() {
			const opts = list.querySelectorAll(".uds-opt");
			opts.forEach((el, k) => el.classList.toggle("active", k === active));
			// scroll the list only: a page/dialog scroll closes the menu
			const el = opts[active];
			if (!el) return;
			if (el.offsetTop < list.scrollTop) list.scrollTop = el.offsetTop - 4;
			else if (el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight) {
				list.scrollTop = el.offsetTop + el.offsetHeight - list.clientHeight + 4;
			}
		}
		function choose(i) {
			if (i == null || i < 0) return;
			const changed = sel.selectedIndex !== i;
			sel.selectedIndex = i;
			close_menu();
			trigger.focus({ preventScroll: true });
			sync();
			if (changed) sel.dispatchEvent(new Event("change", { bubbles: true }));
		}
		function place() {
			const r = trigger.getBoundingClientRect();
			menu.style.width = r.width + "px";
			const h = Math.min(menu.scrollHeight, 300);
			const below = window.innerHeight - r.bottom;
			menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + "px";
			menu.style.top = (below < h + 12 && r.top > h + 12 ? r.top - h - 6 : r.bottom + 6) + "px";
		}

		list.addEventListener("mousedown", (e) => e.preventDefault());
		list.addEventListener("click", (e) => {
			const el = e.target.closest(".uds-opt");
			if (el) choose(+el.dataset.i);
		});
		list.addEventListener("mousemove", (e) => {
			const el = e.target.closest(".uds-opt");
			if (!el) return;
			const k = shown.indexOf(+el.dataset.i);
			if (k !== active) { active = k; highlight(); }
		});
		menu.addEventListener("keydown", (e) => {
			if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); highlight(); }
			else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); highlight(); }
			else if (e.key === "Enter") { e.preventDefault(); choose(shown[active]); }
			else if (e.key === "Escape" || e.key === "Tab") {
				// Escape must not also close the dialog underneath
				e.preventDefault(); e.stopPropagation();
				close_menu(); trigger.focus({ preventScroll: true });
			}
		});

		render("");
		const search = menu.querySelector(".uds-search input");
		if (search) search.addEventListener("input", () => { render(search.value); place(); });
		trigger.classList.add("open");
		trigger.setAttribute("aria-expanded", "true");
		open_menu = { sel, trigger, menu };
		place();
		if (search) search.focus({ preventScroll: true });
		else { menu.tabIndex = -1; menu.focus({ preventScroll: true }); }
	}

	document.addEventListener("click", (e) => {
		if (open_menu && !open_menu.menu.contains(e.target)) close_menu();
	});
	// the menu is fixed to the viewport, so anything scrolling under it closes it
	window.addEventListener("scroll", (e) => {
		if (open_menu && !open_menu.menu.contains(e.target)) close_menu();
	}, true);
	window.addEventListener("resize", close_menu);
	$(document).on("hide.bs.modal", close_menu);

	inject_styles();
	upande_ta.device_select.enhance = enhance;
})();
