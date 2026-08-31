
(function(){
  var $=function(id){return document.getElementById(id)};
  var charts={}, drCharts={};
  var allRows=[], filteredPivoted=[], registerData={present:[],absent:[],on_leave:[],off:[],total:0};
  var leaveData={total:0,by_type:[],rows:[]};
  var sortKey='date', sortDir='desc';
  var optsLoaded=false;
  var currentEmpId=null;
  var lastCards=null; // remember last KPI payload so partial refresh stays consistent
  var lastDaily=[];   // remember last daily payload so bars re-render when headcounts arrive
  // Company-wide active headcount, fetched once. grandActive = all active across
  // all farms; farmActive[farm] = active in that farm. Used by the "Company
  // Active" KPI card AND as the "Expected" figure in the Daily Attendance bar
  // chart (constant across the range — the daily payload has no per-day
  // expected, so leave/off are not netted out per day).
  var grandActive=0, farmActive={}, activeLoaded=false;

  // ── Date helpers ──
  function isoDate(d){return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())}
  function pad(n){return String(n).padStart(2,'0')}
  function todayISO(){return isoDate(new Date())}
  function daysAgoISO(n){var d=new Date();d.setDate(d.getDate()-n);return isoDate(d)}
  function fdShort(s){if(!s)return '—';var d=new Date(String(s).replace(' ','T'));if(isNaN(d))return s;var mo=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];return d.getDate()+' '+mo[d.getMonth()]}
  function fdHM(s){if(!s)return '—';var d=new Date(String(s).replace(' ','T'));if(isNaN(d))return s;return d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}
  function esc(s){if(s==null)return '';return String(s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function fmt(n){return (n||0).toLocaleString()}
  function fmtH(h){if(h==null)return '—';var hr=Math.floor(h),mn=Math.round((h-hr)*60);if(mn===60){hr++;mn=0}return hr+'h '+pad(mn)+'m'}
  function fmtHp(h){if(h==null)return '';var hr=Math.floor(h),mn=Math.round((h-hr)*60);if(mn===60){hr++;mn=0}return hr+':'+pad(mn)}

  // ── Non-destructive chart empty-state helpers ──
  function showChartEmpty(canvas, msg){
    if(!canvas) return;
    var wrap = canvas.parentNode;
    if(!wrap) return;
    clearChartEmpty(canvas);
    var div = document.createElement('div');
    div.className = 'chart-empty';
    div.textContent = msg || 'No data';
    wrap.appendChild(div);
  }
  function clearChartEmpty(canvas){
    if(!canvas) return;
    var wrap = canvas.parentNode;
    if(!wrap) return;
    var prior = wrap.querySelector('.chart-empty');
    if(prior) prior.remove();
  }
  function destroyChart(key){
    if(charts[key]){ try{ charts[key].destroy(); }catch(e){} charts[key]=null; }
  }

  // ── Defaults ──
  var _selDate=todayISO();
  (function initDateCtl(){
    function build(){
      try{
        var wrap=$('r-date'); if(!wrap) return;
        var ctl=frappe.ui.form.make_control({df:{fieldtype:'Date',fieldname:'r_date',label:'',placeholder:'Select date'},parent:wrap,render_input:true});
        ctl.set_value(_selDate);
        try{ if(ctl.datepicker && ctl.datepicker.update) ctl.datepicker.update({minDate: new Date(2000,0,1), maxDate: new Date(2999,11,31), todayButton: true}); }catch(e){}
        // highlight the active date inside the calendar (set_value fills the input only)
        // selectDate() fires the picker's select event, and with autoClose the calendar
        // shuts itself the moment it opens. So: selectDate ONLY on first build, and on
        // open just move the visible month (setViewDate), never re-select.
        function _syncPicker(initial){
          try{
            var v=ctl.get_value()||_selDate; if(!v||!ctl.datepicker) return;
            var p=String(v).split('-');
            var dt=new Date(parseInt(p[0],10), parseInt(p[1],10)-1, parseInt(p[2],10));
            if(initial && ctl.datepicker.selectDate) ctl.datepicker.selectDate(dt);
            if(ctl.datepicker.setViewDate) ctl.datepicker.setViewDate(dt);
          }catch(e){}
        }
        _syncPicker(true);
        // on open, only move the view so the calendar stays open for the click
        ctl.$input.on('focus click', function(){ setTimeout(function(){ _syncPicker(false); }, 0); });
        wrap._syncPicker=_syncPicker;
        // Hide the label/help chrome — but NEVER remove a node that contains the input.
        // Frappe's control markup nests the input inside .control-value in newer builds,
        // so the old blanket removeChild() deleted the date field itself.
        try{
          var kill=wrap.querySelectorAll('.control-label,label,.help-box,.clearfix,.control-value');
          for(var i=0;i<kill.length;i++){
            var k=kill[i];
            if(k.querySelector && k.querySelector('input')) continue;   // keep the input's container
            if(k.tagName==='INPUT') continue;
            k.style.display='none';
          }
        }catch(e){}
        // Fallback: if no input ended up in the wrapper, use a native date field so the
        // filter always works regardless of desk-asset changes.
        try{
          if(!wrap.querySelector('input')){
            var ni=document.createElement('input');
            ni.type='date'; ni.id='r-date-native';
            ni.value=_selDate;
            ni.style.cssText='height:32px;font-size:13px;padding:5px 9px';
            ni.addEventListener('change',function(){ if(ni.value){ _selDate=ni.value; window._selDate=ni.value; load(); scheduleAutoRefresh(); } });
            wrap.appendChild(ni);
          }
        }catch(e){}
        ctl.$input.on('change', function(){ var v=ctl.get_value(); if(v){ _selDate=v; window._selDate=v; load(); scheduleAutoRefresh(); } });
        wrap._ctl=ctl;
      }catch(e){ if(window.console) console.error('date picker init failed', e); }
    }
    if(window.frappe && frappe.ui && frappe.ui.form && frappe.ui.form.make_control){ build(); }
    else if(window.frappe && frappe.require){ frappe.require('controls.bundle.js', build); }
  })();
  $('dr-from').value=daysAgoISO(29);
  $('dr-to').value=todayISO();

  // ── Quick range buttons ──
  document.querySelectorAll('.btn.q[data-q]').forEach(function(b){
    b.addEventListener('click',function(){
      var q=b.getAttribute('data-q');
      if(q==='1'){$('r-from').value=todayISO();$('r-to').value=todayISO()}
      else if(q==='7'){$('r-from').value=daysAgoISO(6);$('r-to').value=todayISO()}
      else if(q==='30'){$('r-from').value=daysAgoISO(29);$('r-to').value=todayISO()}
      else if(q==='mtd'){var d=new Date();$('r-from').value=isoDate(new Date(d.getFullYear(),d.getMonth(),1));$('r-to').value=todayISO()}
      load();
      scheduleAutoRefresh();
    });
  });

  // Drawer quick range
  document.querySelectorAll('.btn.q[data-dq]').forEach(function(b){
    b.addEventListener('click',function(){
      var q=b.getAttribute('data-dq');
      if(q==='30'){$('dr-from').value=daysAgoISO(29);$('dr-to').value=todayISO()}
      else if(q==='90'){$('dr-from').value=daysAgoISO(89);$('dr-to').value=todayISO()}
      else if(q==='365'){$('dr-from').value=daysAgoISO(364);$('dr-to').value=todayISO()}
      else if(q==='all'){$('dr-from').value='2000-01-01';$('dr-to').value=todayISO()}
      if(currentEmpId) loadEmpHistory(currentEmpId);
    });
  });


  // ── Reusable Frappe Date control ───────────────────────────────────────────
  // Renders the desk datepicker into `wrapId` and mirrors the ISO value into the
  // hidden input `hiddenId`, so existing $('<id>').value reads keep working.
  function makeFrappeDate(wrapId, hiddenId, initial){
    function build(){
      try{
        var wrap=$(wrapId), hid=$(hiddenId);
        if(!wrap || !hid || wrap._built) return;
        var ctl=frappe.ui.form.make_control({
          df:{fieldtype:'Date', fieldname:String(hiddenId).replace(/-/g,'_'), label:'', placeholder:'Select date'},
          parent:wrap, render_input:true});
        wrap._built=true;
        var start=initial || hid.value || todayISO();
        ctl.set_value(start); hid.value=start;
        try{ if(ctl.datepicker && ctl.datepicker.update)
          ctl.datepicker.update({minDate:new Date(2000,0,1), maxDate:new Date(2999,11,31), todayButton:true}); }catch(e){}
        try{
          var p=String(start).split('-');
          var dt=new Date(parseInt(p[0],10), parseInt(p[1],10)-1, parseInt(p[2],10));
          if(ctl.datepicker && ctl.datepicker.selectDate) ctl.datepicker.selectDate(dt);
        }catch(e){}
        try{
          var kill=wrap.querySelectorAll('.control-label,label,.help-box,.clearfix,.control-value');
          for(var i=0;i<kill.length;i++){
            var k=kill[i];
            if(k.querySelector && k.querySelector('input')) continue;
            if(k.tagName==='INPUT') continue;
            k.style.display='none';
          }
        }catch(e){}
        ctl.$input.on('change', function(){ var v=ctl.get_value(); if(v) hid.value=v; });
        hid._setDate=function(v){ try{ ctl.set_value(v); hid.value=v; }catch(e){} };
        wrap._ctl=ctl;
      }catch(e){ if(window.console) console.error('date control failed', wrapId, e); }
    }
    if(window.frappe && frappe.ui && frappe.ui.form && frappe.ui.form.make_control) build();
    else if(window.frappe && frappe.require) frappe.require('controls.bundle.js', build);
    else setTimeout(build, 800);
  }

  // ── Searchable select ─────────────────────────────────────────────────────
  // Turns a filter input into a combobox: typing lists matching options; picking
  // one runs onPick. Keyboard: ArrowUp/Down, Enter, Escape.
  function attachSearchSelect(inputId, getOptions, onPick, onType){
    var inp=$(inputId), menu=$(inputId+'-menu');
    if(!inp || !menu) return;
    var idx=-1, shown=[];
    function close(){ menu.classList.remove('open'); menu.innerHTML=''; idx=-1; shown=[]; }
    function render(q){
      var opts=[];
      try{ opts=getOptions(q)||[]; }catch(e){ opts=[]; }
      shown=opts.slice(0,60);
      if(!shown.length){
        menu.innerHTML='<div class="ss-empty">'+(q?'No match':'Type to search')+'</div>';
        menu.classList.add('open'); return;
      }
      menu.innerHTML=shown.map(function(o,i){
        return '<div class="ss-opt'+(i===idx?' active':'')+'" data-i="'+i+'">'+
               '<span class="ss-main">'+esc(o.label)+'</span>'+
               (o.meta?'<span class="ss-meta">'+esc(o.meta)+'</span>':'')+'</div>';
      }).join('');
      menu.classList.add('open');
      var els=menu.querySelectorAll('.ss-opt');
      for(var i=0;i<els.length;i++){
        els[i].addEventListener('mousedown', function(ev){
          ev.preventDefault();
          var o=shown[parseInt(this.getAttribute('data-i'),10)];
          if(o && onPick) onPick(o);
          close();
        });
      }
    }
    inp.addEventListener('input', function(){ if(onType){try{onType(inp.value);}catch(e){}} render(inp.value.trim()); });
    inp.addEventListener('focus', function(){ render(inp.value.trim()); });
    inp.addEventListener('blur',  function(){ setTimeout(close, 150); });
    inp.addEventListener('keydown', function(ev){
      if(!menu.classList.contains('open')) return;
      if(ev.key==='ArrowDown'||ev.key==='ArrowUp'){
        ev.preventDefault();
        idx += (ev.key==='ArrowDown'?1:-1);
        if(idx<0) idx=shown.length-1;
        if(idx>=shown.length) idx=0;
        render(inp.value.trim());
        var a=menu.querySelector('.ss-opt.active'); if(a&&a.scrollIntoView) a.scrollIntoView({block:'nearest'});
      } else if(ev.key==='Enter'){
        if(idx>=0 && shown[idx]){ ev.preventDefault(); if(onPick) onPick(shown[idx]); close(); }
      } else if(ev.key==='Escape'){ close(); }
    });
  }

  // ── TEXT SIZE (accessibility) ──
  // Sets --fs on <html>; .body/.emp-drawer zoom to it. Charts re-measure after
  // the zoom so canvases stay crisp at the new scale. Persists per browser.
  function applyTextScale(scale, save){
    document.documentElement.style.setProperty('--fs', scale);
    document.querySelectorAll('.ts-btn').forEach(function(b){
      b.classList.toggle('active', b.getAttribute('data-scale')===String(scale));
    });
    if(save){ try{ localStorage.setItem('att_text_scale', String(scale)); }catch(e){} }
    // let layout settle, then resize any live charts to the new pixel box
    setTimeout(function(){
      Object.keys(charts).forEach(function(k){ if(charts[k]){ try{ charts[k].resize(); }catch(e){} } });
      Object.keys(drCharts).forEach(function(k){ if(drCharts[k]){ try{ drCharts[k].resize(); }catch(e){} } });
    }, 60);
  }
  document.querySelectorAll('.ts-btn').forEach(function(b){
    b.addEventListener('click', function(){ applyTextScale(b.getAttribute('data-scale'), true); });
  });
  // Text-size control was removed from the UI, but browsers that ever clicked 1.15×/1.3×
  // still carry localStorage.att_text_scale — and zoom on the fixed .body scales its own
  // left/right offsets, clipping the page's right side with no way back. Force 1× and
  // purge the stale key. (If scaling returns later, zoom .tab-panel, never the fixed .body.)
  (function(){
    try{ localStorage.removeItem('att_text_scale'); }catch(e){}
    applyTextScale('1', false);
  })();

  // ── TAB SWITCHING ──
  // Panels are display:none when inactive; Chart.js canvases inside a hidden
  // panel measure as 0×0, so we nudge a resize whenever a panel becomes visible.
  function activateTab(name){
    document.querySelectorAll('.tab-btn').forEach(function(b){
      b.classList.toggle('active', b.getAttribute('data-tab')===name);
    });
    document.querySelectorAll('.tab-panel').forEach(function(p){
      p.classList.toggle('active', p.id==='panel-'+name);
    });
    try{ _fitTopbar(); }catch(e){}
    // lazy-load the heavy checkin table the first time Register is opened
    if(name==='register') loadRows();
    // let the panel paint, then resize any charts it contains so they fill width
    setTimeout(function(){
      Object.keys(charts).forEach(function(k){ if(charts[k]){ try{ charts[k].resize(); }catch(e){} } });
    }, 30);
  }
  document.querySelectorAll('.tab-btn').forEach(function(b){
    b.addEventListener('click',function(){
      var t=b.getAttribute('data-tab');
      var cur=document.querySelector('.tab-panel.active');
      // toggle: clicking the active view returns to Overview (the default)
      if(cur && cur.id==='panel-'+t){ activateTab('overview'); } else { activateTab(t); }
    });
  });
  var KPI_TAB={'Company Active':'overview','Total Expected':'overview','Used Biometric':'register','Manually Marked':'register','Expected Present':'register','Absent':'register','On Leave / Off':'register','Night Shift':'exceptions'};
  (function(){ var kp=$('r-kpi'); if(kp) kp.addEventListener('click',function(e){ var t=(e.target&&e.target.closest)?e.target.closest('.kpi'):null; if(t&&t.getAttribute('data-list')) showTileInline(t.getAttribute('data-list')); });
    var icl=$('tile-inline-close'); if(icl) icl.addEventListener('click',hideTileInline);
    var rrf=$('r-refresh'); if(rrf) rrf.addEventListener('click',function(){ load(); scheduleAutoRefresh(); });
    // ── FARM TREND modal: present headcount per farm per day ──
    var TREND_COLORS=['#38a160','#318ad8','#ea580c','#7c3aed','#cb2929','#d9a514','#0a0a0a','#db2777','#3730a3','#64748b','#0e7490','#84cc16'];
    var _trSeq=0;
    function loadTrend(td){
      var _seq=++_trSeq;
      var pay=(td==='pay'&&window._payroll&&window._payroll.from&&window._payroll.to)?window._payroll:null;
      if(td==='pay'&&!pay) td='30';
      var _tcard=$('r-trend-inline'); if(_tcard) _tcard.classList.add('trend-loading');
      function _trDone(){ if(_tcard) _tcard.classList.remove('trend-loading'); }
      var st=$('trend-status'); if(st) st.textContent=pay?('Loading payroll period '+pay.from+' \u2192 '+pay.to+'\u2026'):('Loading '+td+' days\u2026');
      var qs=new URLSearchParams({date:(window._selDate||''),trend:'1',
        days:String(pay?'':td),trend_from:pay?pay.from:'',trend_to:pay?pay.to:'',
        farm:(($('r-farm')||{}).value||''),
        company:(window._trendCo!=null?window._trendCo:(($('r-company')||{}).value||'')),
        employment_type:($('r-emptype')||{}).value||''}).toString();
      fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_register?'+qs).then(function(r){return r.json()}).then(function(res){
        if(_seq!==_trSeq) return;   // a newer request superseded this one
        var m=res.message||{}; var rows=m.trend||[];
        if(st) st.textContent='';
        destroyChart('trend');
        if(!rows.length){ _trDone(); if(st) st.innerHTML='<div class="empty" style="padding:30px">No check-in data for this selection.</div>'; return; }
        var days_=[], farms={}, seen={};
        rows.forEach(function(r){ if(!seen[r.d]){seen[r.d]=1;days_.push(r.d);} (farms[r.f]=farms[r.f]||{})[r.d]=r.n; });
        var dsets=Object.keys(farms).sort().map(function(f,i){
          return {label:f, data:days_.map(function(d){return farms[f][d]||0}),
            borderColor:TREND_COLORS[i%TREND_COLORS.length], backgroundColor:TREND_COLORS[i%TREND_COLORS.length],
            tension:.3, pointRadius:1.5, borderWidth:2, fill:false};
        });
        var c=$('ch-trend'); if(!c) return;
        // Size the canvas in REAL PIXELS and disable responsive sizing: relying on CSS
        // flex/percentage heights gave Chart.js a 0px box and the chart drew blank.
        var wrap=c.parentNode, wr=wrap.getBoundingClientRect();
        var card=$('r-trend-inline'), cr=card?card.getBoundingClientRect():null;
        // width/height from the wrap, falling back to the CARD (always laid out), then viewport
        var W=Math.round(wr.width||wrap.clientWidth||0);
        if(W<320 && cr) W=Math.round(cr.width-24);
        if(W<320) W=Math.max(320, Math.round(window.innerWidth-(cr?cr.left:300)-40));
        var H=Math.round(wr.height||wrap.clientHeight||0);
        if(H<120 && cr) H=Math.round(cr.bottom-wr.top-16);
        if(H<120) H=Math.max(320, Math.round(window.innerHeight-wr.top-40));
        if(H<120) H=420;
        c.width=W; c.height=H; c.style.width=W+'px'; c.style.height=H+'px';
        window._trendRedraw=function(){ try{ scheduleTrend(td); }catch(e){} };
        charts.trend=new Chart(c.getContext('2d'),{type:'line',
          data:{labels:days_.map(function(d){return d.slice(5)}),datasets:dsets},
          options:{responsive:false,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
            plugins:{legend:{position:'bottom',labels:{boxWidth:10,font:{size:10}}},
              tooltip:{mode:'index',intersect:false}},
            scales:{x:{grid:{display:false},ticks:{font:{size:9},maxTicksLimit:16}},
              y:{beginAtZero:true,grid:{color:'rgba(0,0,0,.05)'},ticks:{precision:0}}}}});
        _trDone();
        // self-heal: if the canvas still came out unsized, re-render once with the box it has now
        if((c.height||0)<120 && !window._trRetried){
          window._trRetried=1;
          setTimeout(function(){ window._trRetried=0; scheduleTrend(td); }, 220);
        }

      }).catch(function(e){ _trDone(); if(st) st.innerHTML='<div class="alert">Trend error: '+esc(e.message)+'</div>'; });
    }
    function buildTrendCos(){
      var box=$('trend-cos'); if(!box) return;
      var cos=window._companies||[];
      var cur=(window._trendCo!=null)?window._trendCo:'';
      var html='';
      cos.forEach(function(co){ html+='<button type="button" class="ti-tab'+(cur===co?' active':'')+'" data-co="'+esc(co)+'">'+esc(co).toUpperCase()+'</button>'; });
      box.innerHTML=html;
    }
    function showTrendInline(on){
      var tc=$('r-trend-inline'), tt=$('r-tile-inline');
      if(!tc) return;
      tc.style.display=on?'':'none'; if(tt) tt.style.display=on?'none':'';
      var tb2=$('r-trend'); if(tb2) tb2.classList.toggle('active', !!on);
      // full-focus: hide tiles + sidebar so the chart takes the whole screen
      document.body.classList.toggle('trend-mode', !!on);
      try{ _fitTopbar(); }catch(e){}
      if(on){
        if(window._trendCo==null || window._trendCo===''){
          var cos=window._companies||[];
          var kai=cos.filter(function(c){return /kaitet/i.test(c)})[0];
          window._trendCo=kai||cos[0]||'';
        }
        buildTrendCos();
        var act=document.querySelector('#trend-ranges .ti-tab.active');
        scheduleTrend(act?act.getAttribute('data-td')||'pay':'pay'); }
      else { window._trendCo=null; destroyChart('trend'); }
    }
    var trCos=$('trend-cos');
    if(trCos) trCos.addEventListener('click',function(e){
      var b=(e.target&&e.target.closest)?e.target.closest('.ti-tab'):null; if(!b) return;
      var pick=b.getAttribute('data-co')||'';
      if(pick!==window._trendCo){ var rf=$('r-farm'); if(rf) rf.value=''; }
      window._trendCo=pick;
      buildTrendCos();
      var act=document.querySelector('#trend-ranges .ti-tab.active');
      scheduleTrend(act?act.getAttribute('data-td')||'pay':'pay');
    });
    // filters (employment type, date) reload an open trend via this hook
    window._reloadTrend=function(){
      if(!document.body.classList.contains('trend-mode')) return;
      // sidebar selection drives the trend too: sync company tab to the sidebar's company
      var rc=(($('r-company')||{}).value||'');
      if(rc && rc!==window._trendCo){ window._trendCo=rc; buildTrendCos(); }
      var act=document.querySelector('#trend-ranges .ti-tab.active');
      scheduleTrend(act?(act.getAttribute('data-td')||'pay'):'pay');
    };
    // Coalesce duplicate triggers (open + filter hook) into ONE fetch, and wait two
    // frames so the just-shown card has laid out before the canvas is measured.
    var _trTimer=null;
    function scheduleTrend(td){
      var pre=$('r-trend-inline'); if(pre && document.body.classList.contains('trend-mode')) pre.classList.add('trend-loading');
      if(_trTimer){ clearTimeout(_trTimer); }
      _trTimer=setTimeout(function(){
        _trTimer=null;
        requestAnimationFrame(function(){ requestAnimationFrame(function(){ loadTrend(td); }); });
      }, 60);
    }
    var trBtn=$('r-trend');
    if(trBtn) trBtn.addEventListener('click',function(){ showTrendInline($('r-trend-inline').style.display==='none'); });
    var trCl=$('trend-close');
    if(trCl) trCl.addEventListener('click',function(){ showTrendInline(false); });
    var trR=$('trend-ranges');
    if(trR) trR.addEventListener('click',function(e){
      var b=(e.target&&e.target.closest)?e.target.closest('.ti-tab'):null; if(!b) return;
      var a=trR.querySelectorAll('.ti-tab'); for(var i=0;i<a.length;i++)a[i].classList.remove('active');
      b.classList.add('active'); scheduleTrend(b.getAttribute('data-td')||'pay');
    });

    var xbt=$('tile-inline-xls'); if(xbt) xbt.addEventListener('click',exportTileExcel);
    // clicking an employee row opens the attendance modal for the payroll period
    var tib=$('tile-inline-tbody');
    if(tib) tib.addEventListener('click',function(e){
      var tr=(e.target&&e.target.closest)?e.target.closest('tr'):null;
      if(!tr) return; var id=tr.getAttribute('data-emp');
      if(id) openDrawer(id, tr.getAttribute('data-name')||id);
    });
    var abk=$('act-back'); if(abk) abk.addEventListener('click',function(){ activateTab('overview'); });
    // Actions page: show one action at a time (Mark Attendance / Bulk Shift Change)
    (function(){
      var bar=document.querySelector('.act-switch'); if(!bar) return;
      function showAct(which){
        var m=$('mark-attendance-card'), s=$('shift-change-card');
        // class-driven: the CSS flex rules use !important, which would beat an inline
        // display:none and leave BOTH cards visible (squeezed and clipped).
        if(m){ m.classList.toggle('act-on', which==='mark');  m.style.display=(which==='mark')?'':'none'; }
        if(s){ s.classList.toggle('act-on', which==='shift'); s.style.display=(which==='shift')?'':'none'; }
        if(which==='shift' && !window._shiftEmps){ try{ loadShiftEmployees(); }catch(e){} }
        var bs=bar.querySelectorAll('.act-btn');
        for(var i=0;i<bs.length;i++) bs[i].classList.toggle('active', bs[i].getAttribute('data-act')===which);
      }
      var bs=bar.querySelectorAll('.act-btn');
      for(var i=0;i<bs.length;i++) bs[i].addEventListener('click',function(){ showAct(this.getAttribute('data-act')); });
      showAct('mark');
    })();
    var ov=$('tile-modal'), cl=$('tile-modal-close'); if(cl&&ov) cl.addEventListener('click',function(){ov.classList.remove('open')}); if(ov) ov.addEventListener('click',function(e){if(e.target===ov)ov.classList.remove('open')}); })();

  // Keep the tab badges (Register present count, Exceptions late count) current.
  function updateTabBadges(reg, d){
    var rb=$('tab-badge-register'), eb=$('tab-badge-exceptions');
    if(rb){
      var pres=(reg&&reg.present)?reg.present.length:0;
      var tot=(reg&&reg.total)||0;
      rb.textContent=tot?pres+'/'+tot:'—';
    }
    if(eb){
      var late=(d&&d.top_late)?d.top_late.length:0;
      var early=(d&&d.top_early)?d.top_early.length:0;
      eb.textContent=(late+early)>0?(late+early):'0';
    }
  }


  Chart.defaults.font.size=11;
  Chart.defaults.font.family="'Poppins',system-ui,sans-serif";
  Chart.defaults.color='#7c7a72';
  Chart.defaults.borderColor='rgba(10,10,10,.08)';
  Chart.defaults.plugins.legend.labels.boxWidth=8;
  Chart.defaults.plugins.legend.labels.boxHeight=8;
  Chart.defaults.plugins.legend.labels.padding=12;
  Chart.defaults.plugins.tooltip.padding=8;
  Chart.defaults.plugins.tooltip.boxPadding=4;

  function destroyCharts(){Object.keys(charts).forEach(function(k){destroyChart(k)})}
  function destroyDrCharts(){Object.keys(drCharts).forEach(function(k){if(drCharts[k]){try{drCharts[k].destroy()}catch(e){}drCharts[k]=null}})}

  // ── SKELETON: instant visual feedback before data arrives ──
  function showSkeleton(){
    // KPI strip: 8 shimmer tiles
    var k='';
    for(var i=0;i<8;i++){
      k+='<div class="kpi"><div class="sk sk-lbl"></div><div class="sk sk-val"></div><div class="sk sk-sub"></div></div>';
    }
    $('r-kpi').innerHTML=k;
    // register count strip: keep labels, show shimmer values
    ['rs-present','rs-leave','rs-off','rs-absent','rs-total'].forEach(function(id){
      var el=$(id); if(el) el.innerHTML='<span class="sk sk-num"></span>';
    });
  }

  // ── MAIN LOAD — progressive render (no Promise.all blocking) ──
  // Each endpoint renders as soon as it returns. The register (fast) paints
  // first; the dashboard (heavier) fills charts/exceptions when ready. KPIs need
  // BOTH register buckets and dashboard cards, so they render once both are in.
  // Transient network failures (browser "Failed to fetch" from an aborted/reset
  // connection when several requests fire at once) used to surface as a red banner
  // and leave the tiles stuck in skeleton. Retry twice with backoff before failing.
  function fetchJSON(url, tries){
    tries = tries || 3;
    function attempt(n){
      return fetch(url).then(function(r){
        if(!r.ok) throw new Error('HTTP '+r.status);
        return r.json();
      }).catch(function(e){
        if(n<=1) throw e;
        return new Promise(function(res){ setTimeout(res, 500*(tries-n+1)); })
          .then(function(){ return attempt(n-1); });
      });
    }
    return attempt(tries);
  }
  function load(){
    try{ if(window._reloadTrend) window._reloadTrend(); }catch(e){}
    try{ if(window._reloadLost) window._reloadLost(); }catch(e){}
    var farm=$('r-farm').value, company=$('r-company').value, emptype=$('r-emptype').value;
    var _rd=_selDate; var from=_rd, to=_rd;
    var qs=new URLSearchParams({from_date:from,to_date:to,farm:farm,company:company,employment_type:emptype}).toString();
    var regQs=new URLSearchParams({date:to,farm:farm,company:company,employment_type:emptype}).toString();
    var leaveQs=new URLSearchParams({date:to,farm:farm,company:company,employment_type:emptype}).toString();

    document.body.classList.add('att-loading');
    $('r-status').innerHTML='';
    showSkeleton();

    // shared state for this load pass so the two renders can coordinate the KPIs
    var pass={reg:null, d:null, leaveDone:false};
    var loadToken=(load._t=(load._t||0)+1);
    function stale(){ return loadToken!==load._t }

    function maybeKPI(){
      if(pass.reg && pass.d && !stale()){
        renderKPI(pass.d, pass.reg);
        updateTabBadges(pass.reg, pass.d);
      }
    }

    // 1) REGISTER — fast; render the register + its strip the moment it lands
    fetchJSON('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_register?'+regQs).then(function(res){
      if(stale()) return;
      var reg=res.message||{};
      pass.reg=reg;
      if(reg.company_farms){buildSidebar(reg.company_farms, reg.company_farm_counts||{});} if(reg.employment_types&&reg.employment_types.length){populateEmpTypes(reg.employment_types);} renderDevices(reg.devices, reg.devices_online, reg.devices_total); if(reg.payroll_from&&reg.payroll_to){window._payroll={from:reg.payroll_from,to:reg.payroll_to};} window._companies=Object.keys(reg.company_farms||{});
      renderRegisterFromData(reg, to);
      renderKPI(pass.d||{}, reg);
      maybeKPI();
    }).catch(function(){ /* register failure is non-fatal to the rest */ });

    // 2) ON-LEAVE — small; feeds the leave card + KPI leave-type chips
    fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_on_leave?'+leaveQs).then(function(r){return r.json()}).catch(function(){return {message:{total:0,by_type:[],rows:[]}}}).then(function(res){
      if(stale()) return;
      leaveData=(res&&res.message)||{total:0,by_type:[],rows:[]};
      renderLeaveTable(to);
      // if the register already rendered, refresh KPI chips now that types are in
      if(pass.reg && pass.d) renderKPI(pass.d, pass.reg);
    });

    // 3) DASHBOARD — charts + exceptions (NO checkin rows anymore; see att_rows)
    fetchJSON('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_dashboard_data?'+qs).then(function(res){
      if(stale()) return;
      var d=res.message;
      if(!d){ document.body.classList.remove('att-loading'); $('r-status').innerHTML='<div class="alert">Empty dashboard response</div>'; return; }
      pass.d=d; lastCards=d;
      document.body.classList.remove('att-loading');
      $('r-status').innerHTML='';
      if(!optsLoaded){populateOptions(d.farms,d.companies);populateEmpTypes(d.employment_types||[]);if(d.company_farms&&Object.keys(d.company_farms).length){buildSidebar(d.company_farms, d.company_farm_counts||{});}optsLoaded=true}
      var _rm=$('rpt-meta'); if(_rm) _rm.textContent=d.from_date+' → '+d.to_date+' · '+fmt(d.cards.unique)+' scanned · '+fmt(d.cards.total)+' logs';
      maybeKPI();
      destroyCharts();
      renderDailyBars(d.daily||[]);
      renderAvgHours(d.avg_hours||[]);
      renderHourly(d.hourly||[]);
      renderLateTable(d.top_late||[], d.from_date, d.to_date);
      renderEarlyTable(d.top_early||[], d.from_date, d.to_date);
      // The checkin table (up to 6000 rows) is no longer part of this payload.
      // It is fetched lazily by loadRows() the first time the Register tab opens.
      // Reset its loaded-state for the new filter set; if the Register tab is
      // already the active one, fetch now.
      rowsLoaded=false; rowsLoading=false; allRows=[];
      markCheckinTablePending();
      if(document.querySelector('.tab-btn.active') && document.querySelector('.tab-btn.active').getAttribute('data-tab')==='register'){
        loadRows();
      }
    }).catch(function(e){
      document.body.classList.remove('att-loading');
      // the dashboard call only supplies late/early extras — still draw the tiles
      // and table from the register so the page is never left as skeletons
      try{ if(registerData) renderKPI(null, registerData); }catch(_e){}
      $('r-status').innerHTML='<div class="alert">Late/early data unavailable ('+esc(e.message)+'). Counts below are from the register.</div>';
    });
  }

  // ── LAZY CHECKIN ROWS (att_rows) ──
  // Fetched only when the Register tab is first opened for the current filter
  // set. rowsLoaded guards against refetching on repeat tab clicks; load() resets
  // it when filters change.
  var rowsLoaded=false, rowsLoading=false;
  function markCheckinTablePending(){
    var tb=$('r-tbody');
    if(tb) tb.innerHTML='<tr><td colspan="9" class="empty">Open to load checkin records…</td></tr>';
    var rc=$('r-rowcount'); if(rc) rc.textContent='—';
  }
  function loadRows(){
    if(rowsLoaded || rowsLoading) return;
    rowsLoading=true;
    var farm=$('r-farm').value, company=$('r-company').value, emptype=$('r-emptype').value;
    var _rd=_selDate; var from=_rd, to=_rd;
    var qs=new URLSearchParams({from_date:from,to_date:to,farm:farm,company:company,employment_type:emptype}).toString();
    var tb=$('r-tbody');
    if(tb) tb.innerHTML='<tr><td colspan="9" class="empty">Loading checkin records…</td></tr>';
    var token=load._t; // tie to current filter pass; ignore if filters changed
    fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.att_rows?'+qs).then(function(r){return r.json()}).then(function(res){
      if(token!==load._t) return; // stale — filters changed while loading
      var d=res.message||{};
      allRows=d.rows||[];
      rowsLoaded=true; rowsLoading=false;
      // reflect the cap note in the meta line if present
      if(d.rows_capped){
        var m=$('rpt-meta');
        if(m && m.textContent.indexOf('capped')===-1) m.textContent=m.textContent+' · table capped at '+fmt(d.rows_cap)+' newest rows';
      }
      renderTable();
    }).catch(function(e){
      rowsLoading=false;
      if(tb) tb.innerHTML='<tr><td colspan="9" class="empty">Error loading rows: '+esc(e.message)+'</td></tr>';
    });
  }

  function populateOptions(farms,companies){
    [['r-farm',farms],['r-company',companies]].forEach(function(p){
      var sel=$(p[0]);
      (p[1]||[]).forEach(function(v){if(!v)return;var o=document.createElement('option');o.value=v;o.textContent=v;sel.appendChild(o)});
    });
  }

  // ── Floating companies → unit/division sidebar ──
  // ── biometric device status (from Biometric Setting's device child table) ──
  // The sidebar keeps only the online/total badge; the devices themselves open
  // in the main area like Lost Hours and Actions do. A serial number and a
  // last-seen timestamp never fitted in a 200px rail — they were truncated to
  // the point of being unreadable.
  var _devs=[];
  function renderDevices(devs, online, total){
    _devs = devs || [];
    var cnt=$('sb-dev-count');
    if(cnt){ cnt.textContent=(online!=null?online:0)+'/'+(total!=null?total:_devs.length); cnt.className='sb-dev-count'; }
    var tag=$('dev-online-tag');
    if(tag) tag.textContent=(online!=null?online:0)+' ONLINE OF '+(total!=null?total:_devs.length);
    drawDevices();
  }
  function drawDevices(){
    var body=$('dev-tbody'); if(!body) return;
    var q=(($('dev-search')||{}).value||'').toLowerCase().trim();
    var rows=_devs.filter(function(d){
      if(!q) return true;
      return String(d.location||'').toLowerCase().indexOf(q)>-1
          || String(d.sn||'').toLowerCase().indexOf(q)>-1;
    });
    var cnt=$('dev-count'); if(cnt) cnt.textContent=rows.length+' DEVICE'+(rows.length===1?'':'S');
    if(!rows.length){
      body.innerHTML='<tr><td colspan="4"><div class="empty">'
        +(_devs.length?'No device matches that search':'No devices configured')+'</div></td></tr>';
      return;
    }
    body.innerHTML=rows.map(function(d){
      var st=(d.status||'Unknown'), on=(st==='Online');
      var seen=d.last_seen?String(d.last_seen).slice(0,16):'never';
      return '<tr class="dev-row'+(on?'':' off')+'">'
        +'<td>'+esc(d.location||'—')+'</td>'
        +'<td class="dev-sn">'+esc(d.sn||'—')+'</td>'
        +'<td>'+esc(seen)+'</td>'
        +'<td><span class="sb-dev-dot"></span> '+esc(st)+'</td></tr>';
    }).join('');
  }
  (function(){
    var srch=$('dev-search'); if(srch) srch.addEventListener('input', drawDevices);
    var xls=$('dev-xls');
    if(xls) xls.addEventListener('click', function(){
      try{
        var aoa=[['Device Name','Device ID','Last Seen','Status']].concat(_devs.map(function(d){
          return [d.location||'', d.sn||'',
                  d.last_seen?String(d.last_seen).slice(0,16):'never',
                  d.status||'Unknown'];
        }));
        var wb=XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), 'Devices');
        XLSX.writeFile(wb, 'biometric-devices.xlsx');
      }catch(e){ if(window.console) console.error('device export failed', e); }
    });
  })();
  function populateEmpTypes(types){
    var box=$("r-emptype-btns"); if(!box) return;
    var h=$("r-emptype");
    var cur=((h&&h.value)||"");
    box.innerHTML="";
    // Fixed groups, SINGLE-select. Contract + Permanent are one button (comma value → backend FIND_IN_SET).
    var GROUPS=[
      {label:'Contract + Permanent', value:'Contract,Permanent'},
      {label:'Contract', value:'Contract'},
      {label:'Permanent', value:'Permanent'},
      {label:'Task Worker', value:'Task Worker'},
      {label:'Temporary', value:'Temporary'}
    ];
    GROUPS.forEach(function(g){
      var b=document.createElement("button"); b.type="button"; b.className="et-btn"+(cur===g.value?" active":""); b.setAttribute("data-et",g.value); b.textContent=g.label;
      b.addEventListener("click",function(){
        var wasActive=b.classList.contains('active');
        var a=box.querySelectorAll('.et-btn'); for(var i=0;i<a.length;i++)a[i].classList.remove('active');
        if(wasActive){ if(h)h.value=''; }               // clicking the active one clears it (= all)
        else { b.classList.add('active'); if(h)h.value=g.value; }
        load(); scheduleAutoRefresh();
      });
      box.appendChild(b);
    });
  }
  // sidebar clicks change r-farm/r-company then call load(); make sure an open trend
  // picks up the NEW values (load() fires the hook before the DOM values settle).
  function _sbTrendSync(){ setTimeout(function(){ try{ if(window._reloadTrend) window._reloadTrend(); }catch(e){} try{ if(window._reloadLost) window._reloadLost(); }catch(e){} }, 0); }
  function _setActive(el){var a=document.querySelectorAll('.att-sb-farm.active,.att-sb-co-head.active');for(var i=0;i<a.length;i++)a[i].classList.remove('active');if(el)el.classList.add('active');}
  function _sbSetSelect(id,val){var s=$(id);if(!s)return;var f=false;for(var i=0;i<s.options.length;i++){if(s.options[i].value===val){f=true;break;}}if(!f){var o=document.createElement('option');o.value=val;o.textContent=val;s.appendChild(o);}s.value=val;}
  function closeSidebar(){var sb=$('att-sidebar');if(sb)sb.classList.remove('open');}
  function sidebarSkeleton(){
    var body=$('att-sb-body'); if(!body) return;
    var h='';
    for(var g=0;g<2;g++){
      h+='<div class="att-sb-company"><div class="att-sb-co-head"><span class="sk" style="display:inline-block;width:55%;height:12px;border-radius:5px"></span></div><div class="att-sb-farms">';
      for(var r=0;r<4;r++){ h+='<div class="att-sb-farm"><span class="sk" style="display:inline-block;width:'+(52+r*8)+'%;height:11px;border-radius:5px"></span></div>'; }
      h+='</div></div>';
    }
    body.innerHTML=h;
  }
  var SB_COUNTS={};
  function buildSidebar(cf, counts){
    if(counts) SB_COUNTS=counts; counts=SB_COUNTS||{};
    var body=$('att-sb-body'); if(!body) return;
    body.innerHTML='';
    var cos=Object.keys(cf||{});
    if(!cos.length){body.innerHTML='<div class="att-sb-empty">No data</div>';return;}
    cos.forEach(function(co){
      var farms=cf[co]||[];
      var grp=document.createElement('div'); grp.className='att-sb-company';
      var h=document.createElement('div'); h.className='att-sb-co-head';
      h.innerHTML='<span class="att-sb-caret">&#9662;</span><span class="att-sb-co-name"></span><span class="att-sb-co-cnt"></span>';
      h.querySelector('.att-sb-co-name').textContent=co;
      h.querySelector('.att-sb-co-cnt').textContent=farms.length;
      h.setAttribute('data-co',co);
      h.addEventListener('click',function(){ grp.classList.toggle('collapsed'); _sbSetSelect('r-company',co); _sbSetSelect('r-farm',''); _setActive(h); load(); scheduleAutoRefresh(); _sbTrendSync(); });
      grp.appendChild(h);
      var box=document.createElement('div'); box.className='att-sb-farms';
      farms.forEach(function(f){
        var it=document.createElement('div'); it.className='att-sb-farm'; it.setAttribute('data-co',co); it.setAttribute('data-farm',f);
        var c=(counts[co]||{})[f]||null;
        var cnt = c ? '<span class="att-sb-counts"><span class="c-tw" title="Task Workers">'+c.tw+'</span><span class="c-rest" title="Others">'+c.rest+'</span></span>' : '';
        it.innerHTML='<span class="att-sb-fname"></span>'+cnt;
        it.querySelector('.att-sb-fname').textContent=f;
        it.addEventListener('click',function(){ _sbSetSelect('r-company',co); _sbSetSelect('r-farm',f); _setActive(it); load(); scheduleAutoRefresh(); _sbTrendSync(); closeSidebar(); });
        box.appendChild(it);
      });
      grp.appendChild(box);
      body.appendChild(grp);
    });
    try{ var cco=($('r-company')||{}).value||'', cff=($('r-farm')||{}).value||'';
      var act=body.querySelectorAll('.att-sb-farm.active,.att-sb-co-head.active'); for(var i=0;i<act.length;i++)act[i].classList.remove('active');
      if(cco){ if(cff){ var it2=body.querySelector('.att-sb-farm[data-co="'+cco+'"][data-farm="'+cff+'"]'); if(it2)it2.classList.add('active'); } else { var ch=body.querySelector('.att-sb-co-head[data-co="'+cco+'"]'); if(ch)ch.classList.add('active'); } }
    }catch(e){}
  }
  (function(){var t=$('att-sb-toggle'),c=$('att-sb-close'),sb=$('att-sidebar');
    if(t&&sb)t.addEventListener('click',function(){sb.classList.toggle('open')});
    if(c&&sb)c.addEventListener('click',function(){sb.classList.remove('open')});
    try{ sidebarSkeleton(); }catch(e){}
  })();

  // ── LEAVE-TYPE BREAKDOWN for the "On Leave / Off" KPI card ──
  // Returns { chipsHtml, titleText } where chipsHtml is a compact set of chips
  // (one per leave type + one for rest-day/off) and titleText is the full list
  // for the tile's hover tooltip so a narrow column never hides information.
  // Prefers leaveData.by_type (from attendance_on_leave); if that's empty it
  // counts leave_type values on the register's on_leave rows. Falls back to the
  // plain "X leave · Y rest-day" line when no type information exists.
  function leaveTypeBreakdown(onLeave, off, maxChips){
    onLeave = onLeave || [];
    off = off || [];
    if(maxChips == null) maxChips = 4;

    // 1) build an ordered {type -> count} list, biggest first.
    // PREFER the register's own on_leave rows: they are already scoped to the
    // selected farm/company and each carries leave_type (set by the register
    // Server Script). Only fall back to leaveData.by_type (from the separate
    // attendance_on_leave endpoint) if the register rows carry no type at all —
    // this is what prevents the bare "N leave · M rest-day" fallback showing on
    // a farm-filtered view where the two endpoints disagree.
    var counts = {};
    var typedFromRows = 0;
    onLeave.forEach(function(r){
      if(r && r.leave_type){
        counts[r.leave_type] = (counts[r.leave_type]||0) + 1;
        typedFromRows++;
      }
    });
    if(typedFromRows === 0 && leaveData && leaveData.by_type && leaveData.by_type.length){
      leaveData.by_type.forEach(function(t){
        var k = t.leave_type || 'Unspecified';
        counts[k] = (counts[k]||0) + (t.count||0);
      });
    }
    // last-ditch: we know how many are on leave but have no type labels — show a
    // single "Leave N" chip so the card still visibly reflects leave, not a bare
    // count line.
    if(Object.keys(counts).length === 0 && onLeave.length){
      counts['Leave'] = onLeave.length;
    }
    var pairs = Object.keys(counts).map(function(k){return {type:k, n:counts[k]}});
    pairs.sort(function(a,b){return b.n - a.n});

    // 2) full text (all types + rest-day) for the tooltip and the fallback line
    var fullParts = pairs.map(function(p){return p.n+' '+p.type});
    if(off.length) fullParts.push(off.length+' rest-day');
    var titleText = fullParts.length
      ? 'On leave / off breakdown — '+fullParts.join(' · ')
      : (onLeave.length+' on leave · '+off.length+' rest-day');

    // 3) no type info at all -> plain sub-line, no chips
    if(!pairs.length){
      return {
        chipsHtml: '',
        subText: onLeave.length+' leave · '+off.length+' rest-day',
        titleText: titleText
      };
    }

    // 4) compact chips: show up to maxChips leave types, collapse the rest into
    //    a "+N more" chip; always append the rest-day chip when present.
    var shown = pairs.slice(0, maxChips);
    var hidden = pairs.slice(maxChips);
    var chips = shown.map(function(p){
      return '<span class="kpi-lt-chip" title="'+esc(p.type)+': '+p.n+'">'+esc(p.type)+' '+p.n+'</span>';
    });
    if(hidden.length){
      var hiddenTotal = hidden.reduce(function(s,p){return s+p.n},0);
      var hiddenLabel = hidden.map(function(p){return p.type+': '+p.n}).join(' · ');
      chips.push('<span class="kpi-lt-chip more" title="'+esc(hiddenLabel)+'">+'+hidden.length+' more ('+hiddenTotal+')</span>');
    }
    if(off.length){
      chips.push('<span class="kpi-lt-chip off" title="Off / rest day: '+off.length+'">Rest-day '+off.length+'</span>');
    }

    return {
      chipsHtml: '<div class="kpi-leavetypes">'+chips.join('')+'</div>',
      subText: '',
      titleText: titleText
    };
  }

  // ── KPI STRIP ──
  // Snapshot buckets (present / leave / off / absent / total) come from the
  // REGISTER, which buckets every employee exactly once (biometric → manual →
  // leave → off → absent) and always reconciles to total. The dashboard payload
  // (d.cards) is used ONLY for range-based checkin metrics (late, in_count),
  // never for the snapshot. reg is the attendance_register response.
  //
  // "Manually Marked" is its OWN card and is deliberately kept separate from
  // "Arrived Today": Arrived = biometric + manual, Used Biometric = device scans
  // only, Manually Marked = source==manual submitted via this dashboard, broken
  // down by marking reason. Manual never counts toward the biometric figure.
  //
  // "On Leave / Off" now shows a per-leave-type breakdown (chips) instead of a
  // bare leave/rest-day split. See leaveTypeBreakdown().
  function _fmtDT(t){ if(!t) return '-'; var s=String(t); return s.length>=16 ? s.slice(0,16) : s; }
  // shared column + title config for both the (legacy) modal and the inline panel
  // minutes -> "45m" / "3h 22m" (readable once past an hour)
  function fmtMins(n){
    n=Math.round(Number(n)||0);
    if(n < 60) return n+'m';
    var h=Math.floor(n/60), m=n%60;
    return m ? (h+'h '+m+'m') : (h+'h');
  }
  function tileColsFor(key){
    var titles={total:'All Employees',expected:'Expected to Work',present:'Present',absent:'Absent',off:'Week Off / Rest Day',nocheckout:'No Checkout (checked in, not out)',leaves:'On Leave',night:'Night Shift',late:'Late Check-ins (after shift start)',early:'Early Checkouts (before shift end)'};
    // shared column defs. v = renderer, s = sort key extractor
    function _isOvernight(r){ var inD=(r.in_time||'').slice(0,10), refD=window._selDate||''; return !!(r.is_night || (inD && refD && inD<refD)); }
    function _statusText(r){
      if(r.pending_night) return r.night_note || 'Shift starts in the evening';
      if(key==='present'||key==='nocheckout'||key==='night'){
        if(_isOvernight(r)) return 'Overnight';
        if(r.source==='manual') return 'Manual';
        return r.att_status||'Present';
      }
      if(key==='total'||key==='expected'){
        if(r.bucket==='Present'){
          if(_isOvernight(r)) return 'Overnight';
          if(r.source==='manual') return 'Manual';
          return r.att_status||'Present';
        }
        return r.bucket||r.att_status||r.status||'';
      }
      if(key==='late')  return 'Late In';
      if(key==='early') return 'Early Out';
      if(key==='absent') return r.att_status||'Absent';
      if(key==='off') return r.off_type||r.type||'Week Off';
      if(key==='leaves') return r.leave_type||'On Leave';
      return r.att_status||r.status||'';
    }
    var idCol   ={h:'Employee ID',v:function(r){return '<span class="t-code">'+esc(r.name||r.employee||'')+'</span>'},s:function(r){return (r.name||r.employee||'')}};
    var nameCol ={h:'Employee Name',v:function(r){return '<span class="t-name">'+esc(r.employee_name||'')+'</span>'},s:function(r){return (r.employee_name||'').toLowerCase()}};
    var shiftCol={h:'Shift',v:function(r){return esc(r.shift||'')},s:function(r){return (r.shift||'').toLowerCase()}};
    var farmCol ={h:'Farm',v:function(r){return esc(r.custom_farm||r.farm||'')},s:function(r){return (r.custom_farm||r.farm||'').toLowerCase()}};
    var inCol   ={h:'Check-In',v:function(r){return '<span class="t-time">'+esc(_fmtDT(r.in_time))+'</span>'},s:function(r){return r.in_time||''}};
    var outCol  ={h:'Check-Out',v:function(r){return '<span class="t-time">'+esc(_fmtDT(r.out_time))+'</span>'},s:function(r){return r.out_time||''}};
    // worked hours = out - in (handles overnight rollover past midnight)
    function _hrsNum(r){
      if(!r.in_time || !r.out_time) return null;
      var a=new Date(String(r.in_time).replace(' ','T')), b=new Date(String(r.out_time).replace(' ','T'));
      if(isNaN(a.getTime())||isNaN(b.getTime())) return null;
      var h=(b-a)/3600000; if(h<0) h+=24; return h;
    }
    var workedCol={h:'Worked Hours',v:function(r){var h=_hrsNum(r); return h==null?'<span class="t-time">\u2013</span>':'<span class="t-time">'+h.toFixed(2)+'h</span>'},s:function(r){var h=_hrsNum(r); return h==null?-1:h}};
    // status pill uses the SAME colour as its tile (st-<key> classes mirror --tc)
    var statCol ={h:'Status',v:function(r){var t=_statusText(r); if(!t) return '';
      var sk=key; var tl=t.toLowerCase();
      if(tl==='overnight'||tl==='night') sk='night';
      else if(tl==='manual') sk='nocheckout';
      else if(tl.indexOf('leave')>=0) sk='leaves';
      else if(tl.indexOf('present')>=0||tl.indexOf('half')>=0||tl.indexOf('home')>=0) sk='present';
      else if(tl.indexOf('absent')>=0) sk='absent';
      else if(tl.indexOf('off')>=0||tl.indexOf('holiday')>=0) sk='off';
      return '<span class="pill st-'+sk+'">'+esc(t)+'</span>'},
      s:function(r){return (_statusText(r)||'').toLowerCase()}};
    var timeCol={h:'Late / Early',v:function(r){
      var out=[];
      if(r.mins_late!=null && Number(r.mins_late)>0) out.push('<span class="pill st-late" title="Checked IN '+Math.round(r.mins_late)+' minutes after shift start">LATE IN &middot; '+fmtMins(r.mins_late)+'</span>');
      if(r.mins_early!=null && Number(r.mins_early)>0) out.push('<span class="pill st-early" title="Checked OUT '+Math.round(r.mins_early)+' minutes before shift end">EARLY OUT &middot; '+fmtMins(r.mins_early)+'</span>');
      return out.join(' ');
    },s:function(r){ return (Number(r.mins_late)||0)+(Number(r.mins_early)||0); }};
    var colMap={
      present:   [idCol,nameCol,shiftCol,farmCol,inCol,outCol,workedCol,timeCol,statCol],
      nocheckout:[idCol,nameCol,shiftCol,farmCol,inCol,workedCol,statCol],
      night:     [idCol,nameCol,shiftCol,farmCol,inCol,outCol,workedCol,statCol],
      absent:    [idCol,nameCol,shiftCol,farmCol,{h:'Designation',v:function(r){return esc(r.designation||'')},s:function(r){return (r.designation||'').toLowerCase()}},{h:'Pending Leave / Week Off',v:function(r){
        if(r.flag) return '<span class="pill pill-reason">'+esc(r.flag)+'</span>';
        if(r.night_note) return '<span class="pill st-night">'+esc(r.night_note)+'</span>';
        return ''},s:function(r){return (r.flag||'').toLowerCase()}},statCol],
      off:       [idCol,nameCol,shiftCol,farmCol,statCol],
      leaves:    [idCol,nameCol,farmCol,{h:'Leave Type',v:function(r){return esc(r.leave_type||'')},s:function(r){return (r.leave_type||'').toLowerCase()}},statCol],
      total:     [idCol,nameCol,shiftCol,farmCol,inCol,outCol,workedCol,timeCol,statCol],
      expected:  [idCol,nameCol,shiftCol,farmCol,inCol,outCol,workedCol,statCol],
      late:      [idCol,nameCol,farmCol,{h:'Late By',v:function(r){return '<span class="t-time">'+esc(r.avg_minutes_late!=null?fmtMins(r.avg_minutes_late):'')+'</span>'},s:function(r){return Number(r.avg_minutes_late)||0}},{h:'Max Late',v:function(r){return '<span class="t-time">'+esc(r.max_minutes_late!=null?fmtMins(r.max_minutes_late):'')+'</span>'},s:function(r){return Number(r.max_minutes_late)||0}},statCol],
      early:     [idCol,nameCol,farmCol,{h:'Early By',v:function(r){return '<span class="t-time">'+esc(r.avg_minutes_early!=null?fmtMins(r.avg_minutes_early):'')+'</span>'},s:function(r){return Number(r.avg_minutes_early)||0}},{h:'Max Early',v:function(r){return '<span class="t-time">'+esc(r.max_minutes_early!=null?fmtMins(r.max_minutes_early):'')+'</span>'},s:function(r){return Number(r.max_minutes_early)||0}},statCol]
    };
    return {cols:colMap[key]||[idCol,nameCol,farmCol,statCol], title:titles[key]||key};
  }
  // export the drill-down table exactly as displayed (active tab + search + sort)
  function _plain(h){ var d=document.createElement('div'); d.innerHTML=h==null?'':String(h); return (d.textContent||'').trim(); }
  function exportTileExcel(){
    var v=window._tileView; if(!v||!v.rows||!v.rows.length){ alert('No data to export'); return; }
    var data=v.rows.map(function(r,i){
      var o={'#':i+1};
      v.cols.forEach(function(c){ o[c.h]=_plain(c.v(r)); });
      return o;
    });
    var sheet=(v.title||'Data').replace(/[\\\/\?\*\[\]:]/g,'').slice(0,31);
    var d=(window._selDate||'').replace(/-/g,'');
    var fname=(v.title||'table').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'')+'_'+d+'.xlsx';
    dlXLSX(data, sheet||'Data', fname);
  }
  function tileRowMatch(r,q){ var hay=((r.employee_name||'')+' '+(r.name||r.employee||'')+' '+(r.custom_farm||r.farm||'')).toLowerCase(); return hay.indexOf(q)>=0; }
  // inline drill-down: hide the charts row, render the clicked tile's employees in its place
  function hideTileInline(){
    var cr=$('r-charts-row'), pn=$('r-tile-inline'); if(pn)pn.style.display='none'; if(cr)cr.style.display='';
    window._tileOpen=null;
    var kp=$('r-kpi'); if(kp){ var ts=kp.querySelectorAll('.kpi'); for(var i=0;i<ts.length;i++) ts[i].classList.remove('active'); }
  }
  function showTileInline(key, explicit){
    var pn=$('r-tile-inline'); if(!pn) return;
    if(explicit===undefined) explicit=true;
    // clicking the ACTIVE tile toggles the filter off -> show everything again
    if(explicit && window._tileOpen===key && pn.style.display!=='none'){
      window._tileOpen=null; showTileInline('total', false); return;
    }
    window._tileExplicit=explicit;
    var lists=window._tileLists||{};
    // combined Late In / Early Out tile → two sub-tabs sharing one panel
    var isLE=(key==='lateearly');
    if(isLE && (!window._leTab || (window._leTab!=='late' && window._leTab!=='early'))) window._leTab='late';
    var effKey=isLE?window._leTab:key;
    var rows=lists[effKey]||[];
    var cfg=tileColsFor(effKey); var cols=cfg.cols;
    pn.style.display='';
    window._tileOpen=key;
    $('tile-inline-title').textContent=cfg.title;
    $('tile-inline-count').textContent=rows.length+' employees';
    var tabsEl=$('tile-inline-tabs');
    if(tabsEl){
      if(isLE){
        tabsEl.style.display='';
        var defs=[{k:'late',l:'Late Ins',n:(lists.late||[]).length},{k:'early',l:'Early Outs',n:(lists.early||[]).length}];
        tabsEl.innerHTML=defs.map(function(d){
          return '<button type="button" class="ti-tab'+(window._leTab===d.k?' active':'')+'" data-le="'+d.k+'">'+d.l+' <span class="ti-tab-n">'+d.n+'</span></button>';
        }).join('');
        var tb=tabsEl.querySelectorAll('.ti-tab');
        for(var ti=0;ti<tb.length;ti++){ tb[ti].onclick=function(){
          window._leTab=this.getAttribute('data-le');
          window._tileSort=null;              // reset sort when switching tab
          window._tileOpen=null; showTileInline('lateearly');
        }; }
      } else { tabsEl.style.display='none'; tabsEl.innerHTML=''; }
    }
    // sortable headers: click to toggle asc/desc (state resets when the tile changes)
    if(!window._tileSort || window._tileSort.key!==effKey) window._tileSort={key:effKey, ci:-1, dir:1};
    function head(){
      var st=window._tileSort;
      $('tile-inline-thead').innerHTML='<tr><th>#</th>'+cols.map(function(c,ci){
        var ar = (st.ci===ci) ? (st.dir>0?' \u2191':' \u2193') : '';
        return '<th class="sortable" data-ci="'+ci+'">'+c.h+ar+'</th>';
      }).join('')+'</tr>';
      var ths=$('tile-inline-thead').querySelectorAll('th.sortable');
      for(var i=0;i<ths.length;i++){ ths[i].onclick=function(){
        var ci=parseInt(this.getAttribute('data-ci'),10);
        var s=window._tileSort;
        s.dir = (s.ci===ci) ? -s.dir : 1; s.ci=ci;
        head(); draw((($('tile-inline-search')||{}).value)||'');
      }; }
    }
    function draw(q){
      q=(q||'').toLowerCase(); var html='', n=0;
      var list=rows.filter(function(r){ return !q || tileRowMatch(r,q); });
      var st=window._tileSort;
      // ABSENT default order: pending-leave rows first, then missing week-off, then the rest
      if(effKey==='absent' && st.ci<0){
        list=list.slice().sort(function(a,b){
          function rank(r){ var f=r.flag||''; if(f.indexOf('Pending')===0) return 0; if(f) return 1; return 2; }
          var d=rank(a)-rank(b); if(d) return d;
          return (a.employee_name||'').toLowerCase()<(b.employee_name||'').toLowerCase()?-1:1;
        });
      }
      // default order elsewhere: complete records (both check-in AND check-out) first
      if(effKey!=='absent' && st.ci<0){
        list=list.slice().sort(function(a,b){
          var ca=(a.in_time&&a.out_time)?0:1, cb=(b.in_time&&b.out_time)?0:1;
          if(ca!==cb) return ca-cb;
          return (a.employee_name||'').toLowerCase()<(b.employee_name||'').toLowerCase()?-1:1;
        });
      }
      if(st.ci>=0 && cols[st.ci] && cols[st.ci].s){
        var kf=cols[st.ci].s;
        list=list.slice().sort(function(a,b){
          var x=kf(a), y=kf(b);
          if(typeof x==='number' && typeof y==='number') return (x-y)*st.dir;
          x=(x==null?'':String(x)); y=(y==null?'':String(y));
          return (x<y?-1:x>y?1:0)*st.dir;
        });
      }
      window._tileView={rows:list, cols:cols, title:cfg.title};
      list.forEach(function(r){ n++;
        var eid=esc(String(r.name||r.employee||'')), enm=esc(r.employee_name||'');
        html+='<tr data-emp="'+eid+'" data-name="'+enm+'"><td>'+n+'</td>'+cols.map(function(c){return '<td>'+c.v(r)+'</td>'}).join('')+'</tr>'; });
      $('tile-inline-tbody').innerHTML=html||'<tr><td colspan="'+(cols.length+1)+'" class="empty">No employees</td></tr>';
      try{ _fitTables(); }catch(e){}
    }
    head();
    draw('');
    var sr=$('tile-inline-search'); if(sr){ sr.value=''; sr.oninput=function(){draw(sr.value)}; }
    var kp=$('r-kpi'); if(kp){ var ts=kp.querySelectorAll('.kpi'); for(var i=0;i<ts.length;i++){ ts[i].classList.toggle('active', explicit && ts[i].getAttribute('data-list')===key); } }
  }
  function openTileModal(key){
    var lists=window._tileLists||{}; var rows=lists[key]||[];
    var cfg=tileColsFor(key); var cols=cfg.cols;
    var ov=$('tile-modal'); if(!ov) return;
    $('tile-modal-title').textContent=cfg.title;
    $('tile-modal-count').textContent=rows.length+' employees';
    $('tile-modal-thead').innerHTML='<tr><th>#</th>'+cols.map(function(c){return '<th>'+c.h+'</th>'}).join('')+'</tr>';
    function draw(q){
      q=(q||'').toLowerCase(); var html='', n=0;
      rows.forEach(function(r){
        var hay=((r.employee_name||'')+' '+(r.name||r.employee||'')+' '+(r.custom_farm||r.farm||'')).toLowerCase();
        if(q && hay.indexOf(q)<0) return;
        n++;
        html+='<tr><td>'+n+'</td>'+cols.map(function(c){return '<td>'+c.v(r)+'</td>'}).join('')+'</tr>';
      });
      $('tile-modal-tbody').innerHTML=html||'<tr><td colspan="'+(cols.length+1)+'" class="empty">No employees</td></tr>';
    }
    draw('');
    var sr=$('tile-modal-search'); if(sr){ sr.value=''; sr.oninput=function(){draw(sr.value)}; }
    ov.classList.add('open');
  }
  function renderKPI(d, reg){
    var c=(d&&d.cards)||{};
    reg=reg||registerData||{};
    var present = reg.present || [];
    var onLeave = reg.on_leave || [];
    var off     = reg.off || [];
    var absent  = reg.absent || [];
    var total   = reg.total || (present.length+onLeave.length+off.length+absent.length);
    // shift-aware lateness comes from the dashboard_data endpoint (top_late/top_early).
    // Those lists are computed from raw scans, so they can include people the register
    // buckets as Week Off / On Leave (e.g. a guard who worked on his rest day). A late-in
    // or early-out only makes sense for someone counted PRESENT, so intersect the two and
    // annotate the present rows with their timing.
    var _rawLate  = (reg && reg.top_late) || (d && d.top_late)  || [];
    var _rawEarly = (reg && reg.top_early) || (d && d.top_early) || [];
    var _presRows=(reg.present && reg.present.length) ? reg.present
                  : ((window._tileLists && window._tileLists.present) || []);
    var _presIds={}, _nPres=0;
    _presRows.forEach(function(r){ _presIds[String(r.name||r.employee||'')]=r; _nPres++; });
    // only intersect when we actually have a present list; otherwise keep the raw lists
    // (this handler also runs from the dashboard fetch, which has no register payload)
    var topLate  = _nPres ? _rawLate.filter(function(x){ return _presIds[String(x.employee||x.name||'')]; })  : _rawLate;
    var topEarly = _nPres ? _rawEarly.filter(function(x){ return _presIds[String(x.employee||x.name||'')]; }) : _rawEarly;
    topLate.forEach(function(x){ var p=_presIds[String(x.employee||x.name||'')]; if(p) p.mins_late=x.avg_minutes_late!=null?x.avg_minutes_late:x.max_minutes_late; });
    topEarly.forEach(function(x){ var p=_presIds[String(x.employee||x.name||'')]; if(p) p.mins_early=x.avg_minutes_early!=null?x.avg_minutes_early:x.max_minutes_early; });

    // present split: device scans vs manually-marked attendance
    var manualRows = present.filter(function(r){return r.source==='manual'});
    var bioCount = present.length - manualRows.length;
    var manualCount = manualRows.length;

    // reason breakdown for the Manually Marked card sub-line
    var reasonCounts = {};
    manualRows.forEach(function(r){
      var k = r.marking_reason || r.att_status || 'Manual';
      reasonCounts[k] = (reasonCounts[k]||0) + 1;
    });
    var reasonBreakdown = Object.keys(reasonCounts).map(function(k){
      return reasonCounts[k]+' '+k;
    }).join(' · ') || 'manual present';

    // arrived = everyone present (biometric + manual). This always equals the
    // register's Present count, so it can never be less than biometric.
    var arrived = present.length;

    // attendance rate is measured against people who were EXPECTED to work —
    // i.e. exclude leave and off from the denominator.
    var expected = Math.max(0, total - onLeave.length - off.length);
    var pct = expected>0 ? Math.round(arrived/expected*100) : 0;

    var latePct = c.in_count ? Math.round((c.late||0)*100/c.in_count) : 0;

    // ── night shift figures come from the register (single source) ──
    var nightTotal = reg.night_total || 0;
    var nightIn = reg.night_checked_in || 0;
    var nightPct = nightTotal ? Math.round(nightIn/nightTotal*100) : 0;

    // ── Company Active card ──
    var selFarm = $('r-farm').value;
    var caVal, caSub, caPct;
    if(selFarm){
      var thisFarm = (farmActive[selFarm]!=null) ? farmActive[selFarm] : total;
      caVal = thisFarm;
      caSub = grandActive ? ('of '+fmt(grandActive)+' company-wide') : (selFarm);
      caPct = grandActive ? Math.round(thisFarm/grandActive*100) : 100;
    } else {
      caVal = grandActive || total;
      caSub = 'all farms';
      caPct = 100;
    }

    // ── On Leave / Off breakdown (chips by leave type) ──
    var lb = leaveTypeBreakdown(onLeave, off, 4);

    var noCheckout = present.filter(function(r){ return !r.out_time; });
    var night = present.filter(function(r){ return r.is_night; });
    // night staff whose EVENING shift hasn't started yet: not absent — listed here with
    // their start time so it is obvious they are simply not due on shift yet.
    var nightPending=(reg.night_pending||[]).map(function(r){ r.pending_night=true; return r; });
    night = night.concat(nightPending);
    // tag each row with its bucket so the combined (all) view can show a Status
    present.forEach(function(r){ r.bucket='Present' });
    absent.forEach(function(r){ r.bucket='Absent' });
    onLeave.forEach(function(r){ r.bucket='On Leave' });
    off.forEach(function(r){ r.bucket='Week Off' });
    var allEmp = present.concat(absent, onLeave, off);
    var expectedList = present.concat(absent);
    window._tileLists = {total:allEmp, expected:expectedList, present:present, absent:absent, off:off, nocheckout:noCheckout, leaves:onLeave, night:night, late:topLate, early:topEarly, lateearly:topLate};
    var items=[
      {key:'total',      lbl:'Total Employees', val:fmt(total)},
      {key:'expected',   lbl:'Expected',        val:fmt(expectedList.length)},
      {key:'present',    lbl:'Present',         val:fmt(present.length)},
      {key:'nocheckout', lbl:'No Checkout',     val:fmt(noCheckout.length)},
      {key:'absent',     lbl:'Absent',          val:fmt(absent.length)},
      {key:'off',        lbl:'Week Off',        val:fmt(off.length)},
      {key:'leaves',     lbl:'Leaves',          val:fmt(onLeave.length)},
      {key:'night',      lbl:'Night Shift',     val:fmt(night.length)},
      {key:'lateearly',  lbl:'Late In / Early Out', val:fmt(topLate.length+topEarly.length)}
    ];
    $('r-kpi').innerHTML=items.map(function(x){
      return '<div class="kpi kpi--'+x.key+'" data-list="'+x.key+'"><div class="kpi-lbl">'+x.lbl+'</div><div class="kpi-val">'+x.val+'</div></div>';
    }).join('');
    // charts removed: the employee table is the overview body. Keep the current tile open,
    // or default to Present, so a table always shows below the tiles.
    var _tk=window._tileOpen||'total'; var _te=(window._tileOpen? (window._tileExplicit!==false) : false); window._tileOpen=null; showTileInline(_tk,_te);
    // tiles are a fixed header — re-measure so the body reserves the right space
    try{ _fitTopbar(); }catch(e){}
  }

  // ── EXPECTED HEADCOUNT for the daily bars ──
  // Uses the company-wide/farm-level active counts (fetched once at startup).
  // If a farm is selected, expected = that farm's active headcount; otherwise
  // the grand total. Falls back to the register total until counts arrive.
  // NOTE: this is a flat headcount per day — per-day leave/off are NOT netted
  // out because the daily payload doesn't carry them.
  function expectedHeadcount(){
    var farm=$('r-farm').value;
    if(farm && farmActive[farm]!=null) return farmActive[farm];
    if(!farm && grandActive) return grandActive;
    return registerData.total||0;
  }

  // ── CHARTS ──
  // Grouped vertical bars per day: Expected (blue) / Present (green) / Absent (red).
  // Prefers the server-computed daily series (expected = active − on-leave −
  // holiday PER DAY; present = checkin ∪ manual attendance). Falls back to the
  // flat-headcount method if the endpoint is the pre-rewrite version.
  function renderDailyBars(daily){
    lastDaily = daily || [];
    var c=$('ch-daily');
    if(!c) return;
    destroyChart('daily');
    if(!lastDaily.length){
      showChartEmpty(c, 'No data for selected range');
      return;
    }
    clearChartEmpty(c);

    var hasServer = lastDaily[0].expected != null;
    var labels=[], expected=[], present=[], absent=[], leaveArr=null, offArr=null;
    if(hasServer){
      labels   = lastDaily.map(function(r){return fdShort(r.day)});
      expected = lastDaily.map(function(r){return r.expected||0});
      present  = lastDaily.map(function(r){return r.present||0});
      absent   = lastDaily.map(function(r){return r.absent||0});
      leaveArr = lastDaily.map(function(r){return r.on_leave||0});
      offArr   = lastDaily.map(function(r){return r.off||0});
      $('daily-bars-tag').textContent='expected = active − leave − holiday, per day';
    } else {
      var exp = expectedHeadcount();
      var farm=$('r-farm').value;
      labels   = lastDaily.map(function(r){return fdShort(r.day)});
      expected = lastDaily.map(function(){return exp});
      present  = lastDaily.map(function(r){return r.present!=null?r.present:(r.unique_employees||0)});
      absent   = present.map(function(p){return Math.max(0, exp-p)});
      $('daily-bars-tag').textContent='expected='+fmt(exp)+(farm?' ('+farm+')':' (all farms)')+' · flat headcount';
    }

    charts.daily=new Chart(c.getContext('2d'),{type:'bar',data:{
      labels:labels,
      datasets:[
        {label:'Expected',data:expected,backgroundColor:'rgba(37,99,235,.55)',borderColor:'#2563eb',borderWidth:1,borderRadius:3,maxBarThickness:34},
        {label:'Present', data:present, backgroundColor:'rgba(10,122,67,.65)',borderColor:'#0a7a43',borderWidth:1,borderRadius:3,maxBarThickness:34},
        {label:'Absent',  data:absent,  backgroundColor:'rgba(185,28,28,.55)',borderColor:'#b91c1c',borderWidth:1,borderRadius:3,maxBarThickness:34},
      ]},options:{
        responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{
          legend:{position:'bottom'},
          tooltip:{callbacks:{footer:function(items){
            var i=items[0].dataIndex;
            var f=expected[i]? 'Turnout: '+Math.round(present[i]/expected[i]*100)+'%' : '';
            if(leaveArr) f=f+(f?' · ':'')+leaveArr[i]+' leave · '+offArr[i]+' off';
            var asrc=lastDaily[i]&&lastDaily[i].absent_src;
            if(asrc) f=f+(f?' · ':'')+(asrc==='marked'?'absent: HR-marked':'absent: derived');
            return f;
          }}}
        },
        scales:{
          x:{grid:{display:false},ticks:{maxTicksLimit:16}},
          y:{grid:{color:'rgba(0,0,0,.04)'},beginAtZero:true,ticks:{precision:0}}
        }
      }});
  }

  // Line chart: average hours of attendance per day. Prefers the server-side
  // aggregate (d.avg_hours — first IN paired with earliest OUT within 20h,
  // covers the FULL range regardless of the rows cap, night-shift safe).
  // Falls back to a client-side pivot of allRows if the endpoint is older.
    function isoMinus(iso,n){ var d=new Date(iso+'T00:00:00'); d.setDate(d.getDate()-n); var m=('0'+(d.getMonth()+1)).slice(-2), day=('0'+d.getDate()).slice(-2); return d.getFullYear()+'-'+m+'-'+day; }
  function renderHours7d(series){
    var c=$('ch-hours7d'); if(!c) return;
    destroyChart('hours7d');
    var dates=[],avgs=[];
    (series||[]).forEach(function(r){ dates.push(String(r.day).slice(0,10)); avgs.push(Number(r.avg_hours)||0); });
    if(!dates.length){ showChartEmpty(c,'No complete IN/OUT pairs in the last 7 days'); return; }
    clearChartEmpty(c);
    charts.hours7d=new Chart(c.getContext('2d'),{type:'line',data:{labels:dates.map(fdShort),datasets:[{label:'Avg hours (in → out)',data:avgs,borderColor:'#0a0a0a',backgroundColor:'rgba(10,10,10,.07)',borderWidth:2,fill:true,tension:.35,pointRadius:4,pointHoverRadius:6,pointBackgroundColor:'#0a0a0a'}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{position:'bottom'},tooltip:{callbacks:{label:function(it){return 'Avg: '+fmtH(it.parsed.y)}}}},scales:{y:{beginAtZero:true,ticks:{callback:function(v){return v+'h'}}}}}});
    setTimeout(function(){try{charts.hours7d&&charts.hours7d.resize()}catch(e){}},60);
  }
  function openHoursModal(){
    var ov=$('hours-modal'); if(!ov) return;
    ov.classList.add('open'); ov.classList.add('loading');
    var st=$('hours-modal-status'); if(st) st.innerHTML='';
    destroyChart('hours7d');
    var end=_selDate||todayISO(); var start=isoMinus(end,6);
    var farm=$('r-farm').value, company=$('r-company').value, emptype=$('r-emptype').value;
    var qs=new URLSearchParams({from_date:start,to_date:end,farm:farm,company:company,employment_type:emptype}).toString();
    fetchJSON('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_dashboard_data?'+qs).then(function(res){
      var d=res.message||{}; ov.classList.remove('loading'); if(st) st.innerHTML='';
      renderHours7d(d.avg_hours||[]);
    }).catch(function(){ ov.classList.remove('loading'); if(st) st.innerHTML='<div class="alert">Failed to load</div>'; });
  }
  (function(){ var eb=$('daily-expand'), ov=$('hours-modal'), cl=$('hours-modal-close');
    if(eb) eb.addEventListener('click', openHoursModal);
    if(cl&&ov) cl.addEventListener('click', function(){ ov.classList.remove('open'); });
    if(ov) ov.addEventListener('click', function(e){ if(e.target===ov) ov.classList.remove('open'); });
  })();
function renderAvgHours(serverSeries){
    var c=$('ch-avghours');
    if(!c) return;
    destroyChart('avghours');
    var dates=[], avgs=[], counts=[];
    if(serverSeries && serverSeries.length){
      serverSeries.forEach(function(r){
        dates.push(String(r.day).slice(0,10));
        avgs.push(Number(r.avg_hours)||0);
        counts.push(r.n||0);
      });
    } else {
      var pivoted = pivotRows(allRows);
      var byDate={};
      pivoted.forEach(function(r){
        if(r.hours_worked==null) return;
        if(!byDate[r.date]) byDate[r.date]={sum:0,n:0};
        byDate[r.date].sum = byDate[r.date].sum + r.hours_worked;
        byDate[r.date].n   = byDate[r.date].n + 1;
      });
      dates=Object.keys(byDate).sort();
      avgs=dates.map(function(d){return byDate[d].sum/byDate[d].n});
      counts=dates.map(function(d){return byDate[d].n});
    }
    if(!dates.length){
      showChartEmpty(c, 'No complete IN/OUT pairs in selected range');
      return;
    }
    clearChartEmpty(c);

    charts.avghours=new Chart(c.getContext('2d'),{type:'line',data:{
      labels:dates.map(fdShort),
      datasets:[{
        label:'Avg hours (in → out)',
        data:avgs,
        borderColor:'#7c3aed',
        backgroundColor:'rgba(124,58,237,.08)',
        borderWidth:2,fill:true,tension:.35,
        pointRadius:3,pointHoverRadius:5,
        pointBackgroundColor:'#7c3aed'
      }]},options:{
        responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{
          legend:{position:'bottom'},
          tooltip:{callbacks:{
            label:function(it){return 'Avg: '+fmtH(it.parsed.y)},
            footer:function(items){var i=items[0].dataIndex;return counts[i]+' employees with full IN/OUT'}
          }}
        },
        scales:{
          x:{grid:{display:false},ticks:{maxTicksLimit:16}},
          y:{grid:{color:'rgba(0,0,0,.04)'},beginAtZero:true,
             title:{display:true,text:'hours',font:{size:10}},
             ticks:{callback:function(v){return v+'h'}}}
        }
      }});
  }

  function renderHourly(hourly){
    var c=$('ch-hour');
    if(!c) return;
    destroyChart('hour');
    if(!hourly || !hourly.length){
      showChartEmpty(c, 'No checkin data for selected range');
      return;
    }
    clearChartEmpty(c);
    var byIn={},byOut={};for(var i=0;i<24;i++){byIn[i]=0;byOut[i]=0}
    hourly.forEach(function(r){if(r.log_type==='IN')byIn[r.hr]=(byIn[r.hr]||0)+r.n;else byOut[r.hr]=(byOut[r.hr]||0)+r.n});
    var lbls=[];for(var h=0;h<24;h++)lbls.push(pad(h));
    charts.hour=new Chart(c.getContext('2d'),{type:'bar',data:{labels:lbls,datasets:[
      {label:'IN',data:lbls.map(function(_,i){return byIn[i]}),backgroundColor:'rgba(10,122,67,.7)',borderRadius:2},
      {label:'OUT',data:lbls.map(function(_,i){return byOut[i]}),backgroundColor:'rgba(168,165,155,.6)',borderRadius:2},
    ]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{position:'bottom'},tooltip:{mode:'index',intersect:false,callbacks:{
        title:function(items){return (items&&items[0]?items[0].label:'')+':00 hrs'},
        label:function(ctx){return ' '+ctx.dataset.label+': '+(ctx.parsed.y||0)},
        footer:function(items){var t=0;items.forEach(function(it){t+=it.parsed.y||0});return 'Total: '+t}
      }}},scales:{x:{stacked:true,grid:{display:false},ticks:{font:{size:9}}},y:{stacked:true,grid:{color:'rgba(0,0,0,.04)'},beginAtZero:true,ticks:{precision:0}}}}});
  }

  // ── LATE / EARLY DETAIL TABLES ──
  function renderLateTable(top, from, to){
    $('late-range-tag').textContent=from+' → '+to+' · top '+top.length;
    var tbody=$('tbody-late');
    if(!top.length){tbody.innerHTML='<tr><td colspan="7" class="empty">No late arrivals</td></tr>';return}
    tbody.innerHTML=top.map(function(r,i){
      // Low arrival spread + chronic "lateness" = the SHIFT start time is wrong,
      // not the person: they arrive at nearly the same minute every day.
      var spread=(r.arrival_spread_min!=null)?Number(r.arrival_spread_min):null;
      var trend;
      if(r.late_days>=5 && spread!=null && spread<20)
        trend='<span style="color:var(--accent)" title="Arrives within ±'+spread+' min every day — shift start time likely misconfigured">Check shift ('+spread+'m spread)</span>';
      else if(r.late_days>3) trend='<span style="color:var(--bad)">↑ Frequent</span>';
      else if(r.late_days>1) trend='<span style="color:var(--warn)">→ Occasional</span>';
      else trend='<span style="color:var(--good)">↓ Rare</span>';
      return '<tr>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.employee)+'" data-empname="'+esc(r.employee_name)+'">'+esc(r.employee_name||r.employee)+'</span></td>'+
        '<td class="t-code">'+esc(r.employee||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.farm||'—')+'</span></td>'+
        '<td><strong>'+fmt(r.late_days)+'</strong></td>'+
        '<td class="t-time">'+(r.avg_minutes_late?Math.round(r.avg_minutes_late)+'m':'—')+'</td>'+
        '<td class="t-time">'+(r.max_minutes_late?Math.round(r.max_minutes_late)+'m':'—')+'</td>'+
        '<td>'+trend+'</td>'+
      '</tr>';
    }).join('');
    attachEmpLinks(tbody);
  }

  function renderEarlyTable(top, from, to){
    $('early-range-tag').textContent=from+' → '+to+' · top '+top.length;
    var tbody=$('tbody-early');
    if(!top.length){tbody.innerHTML='<tr><td colspan="7" class="empty">No early departures</td></tr>';return}
    tbody.innerHTML=top.map(function(r,i){
      var trend=r.early_days>3?'<span style="color:var(--bad)">↑ Frequent</span>':r.early_days>1?'<span style="color:var(--warn)">→ Occasional</span>':'<span style="color:var(--good)">↓ Rare</span>';
      return '<tr>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.employee)+'" data-empname="'+esc(r.employee_name)+'">'+esc(r.employee_name||r.employee)+'</span></td>'+
        '<td class="t-code">'+esc(r.employee||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.farm||'—')+'</span></td>'+
        '<td><strong>'+fmt(r.early_days)+'</strong></td>'+
        '<td class="t-time">'+(r.avg_minutes_early?Math.round(r.avg_minutes_early)+'m':'—')+'</td>'+
        '<td class="t-time">'+(r.max_minutes_early?Math.round(r.max_minutes_early)+'m':'—')+'</td>'+
        '<td>'+trend+'</td>'+
      '</tr>';
    }).join('');
    attachEmpLinks(tbody);
  }

  // Track employees manually marked this session
  var markedEmployeeIds = new Set();

  // ── LEAVE TABLE ──
  function renderLeaveTable(onDate){
    $('leave-date-tag').textContent='Date: '+onDate;
    var rows=leaveData.rows||[];
    $('leave-cnt').textContent=rows.length+' on leave';

    var chips=(leaveData.by_type||[]).map(function(t){
      return '<span class="pill pill-leavetype" style="margin-right:6px;padding:3px 9px">'+esc(t.leave_type)+': '+t.count+'</span>';
    }).join('');
    $('leave-type-summary').innerHTML=chips||'<span style="color:var(--text-3);font-style:italic">No approved leave covering '+esc(onDate)+'</span>';

    $('tbody-leave').innerHTML=rows.length?rows.map(function(r,i){
      return '<tr>'+
        '<td class="t-code">'+(i+1)+'</td>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.employee)+'" data-empname="'+esc(r.employee_name)+'">'+esc(r.employee_name||'—')+'</span></td>'+
        '<td class="t-code">'+esc(r.employee||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.custom_farm||'—')+'</span></td>'+
        '<td>'+esc(r.department||'—')+'</td>'+
        '<td><span class="pill pill-leavetype">'+esc(r.leave_type||'—')+'</span></td>'+
        '<td class="t-time">'+fdShort(r.from_date)+' → '+fdShort(r.to_date)+'</td>'+
        '<td class="t-time">'+(r.total_leave_days!=null?r.total_leave_days+(r.half_day?' (½)':''):'—')+'</td>'+
      '</tr>';
    }).join(''):'<tr><td colspan="8" class="empty">No employees on approved leave on '+esc(onDate)+'</td></tr>';
    attachEmpLinks($('tbody-leave'));
  }

  // ── REGISTER ──
  function renderRegisterFromData(reg, regDate){
    $('register-date-tag').textContent='Date: '+regDate;
    var present=reg.present||[], absent=reg.absent||[];
    var onLeave=reg.on_leave||[], off=reg.off||[];
    var total=reg.total||0;
    registerData.present=present;
    registerData.on_leave=onLeave;
    registerData.off=off;
    registerData.total=total;
    // carry night figures through so partial refreshes keep the KPI consistent
    registerData.night_total=reg.night_total||0;
    registerData.night_checked_in=reg.night_checked_in||0;

    // Filter out already-marked employees up front
    var filteredAbsent=absent.filter(function(r){return !markedEmployeeIds.has(r.name)});

    // ── count strip (four buckets must reconcile to total) ──
    function pct(n){return total?Math.round(n/total*100)+'%':'—'}
    $('rs-present').textContent=fmt(present.length);
    $('rs-present-pct').textContent=pct(present.length);
    $('rs-leave').textContent=fmt(onLeave.length);
    $('rs-leave-pct').textContent=pct(onLeave.length);
    $('rs-off').textContent=fmt(off.length);
    $('rs-off-pct').textContent=pct(off.length);
    $('rs-absent').textContent=fmt(filteredAbsent.length);
    $('rs-absent-pct').textContent=pct(filteredAbsent.length);
    $('rs-total').textContent=fmt(total);
    // reconciliation check — does present+leave+off+absent (raw, pre-mark) == total?
    var sum=present.length+onLeave.length+off.length+absent.length;
    var stat=$('rs-total-stat'), bal=$('rs-balance');
    stat.classList.remove('bal-ok','bal-bad');
    if(sum===total){ stat.classList.add('bal-ok'); bal.textContent='BALANCES'; }
    else { stat.classList.add('bal-bad'); bal.textContent='MISMATCH '+sum+' \u2260 '+total; }

    $('present-cnt').textContent=present.length+' / '+total+' employees';
    $('absent-cnt').textContent=filteredAbsent.length+' / '+total+' employees';

    $('tbody-present').innerHTML=present.length?present.map(function(r,i){
      var hw=null;
      if(r.in_time&&r.out_time){
        var t1=new Date(String(r.in_time).replace(' ','T')),t2=new Date(String(r.out_time).replace(' ','T'));
        if(!isNaN(t1)&&!isNaN(t2)&&t2>t1)hw=(t2-t1)/3600000;
      }
      var sourceCell;
      if(r.source==='manual'){
        var lbl = r.marking_reason || r.att_status || 'Manual';
        sourceCell = '<span class="pill pill-reason">'+esc(lbl)+'</span>';
      } else if(r.source==='night'){
        sourceCell = '<span class="pill pill-leave">Night</span>';
      } else {
        sourceCell = '<span class="pill pill-ok">Biometric</span>';
      }
      return '<tr>'+
        '<td class="t-code">'+(i+1)+'</td>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.name)+'" data-empname="'+esc(r.employee_name)+'">'+esc(r.employee_name||'—')+'</span></td>'+
        '<td class="t-code">'+esc(r.name||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.custom_farm||'—')+'</span></td>'+
        '<td class="t-time">'+(r.in_time?fdHM(r.in_time):'—')+'</td>'+
        '<td class="t-time">'+(r.out_time?fdHM(r.out_time):'—')+'</td>'+
        '<td class="t-time">'+(hw!=null?fmtH(hw):'—')+'</td>'+
        '<td>'+sourceCell+'</td>'+
      '</tr>';
    }).join(''):'<tr><td colspan="8" class="empty">No one checked in on '+regDate+'</td></tr>';
    attachEmpLinks($('tbody-present'));

    $('tbody-absent').innerHTML=filteredAbsent.length?filteredAbsent.map(function(r,i){
      return '<tr>'+
        '<td class="t-code">'+(i+1)+'</td>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.name)+'" data-empname="'+esc(r.employee_name)+'">'+esc(r.employee_name||'—')+'</span></td>'+
        '<td class="t-code">'+esc(r.name||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.custom_farm||'—')+'</span></td>'+
        '<td>'+esc(r.designation||'—')+'</td>'+
        '<td><span class="pill pill-abs">Absent</span></td>'+
      '</tr>';
    }).join(''):'<tr><td colspan="6" class="empty">All employees accounted for</td></tr>';
    attachEmpLinks($('tbody-absent'));

    // ── On Leave sub-table ──
    $('reg-leave-cnt').textContent=onLeave.length+' on leave';
    $('tbody-reg-leave').innerHTML=onLeave.length?onLeave.map(function(r,i){
      return '<tr>'+
        '<td class="t-code">'+(i+1)+'</td>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.name)+'" data-empname="'+esc(r.employee_name)+'">'+esc(r.employee_name||'—')+'</span></td>'+
        '<td class="t-code">'+esc(r.name||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.custom_farm||'—')+'</span></td>'+
        '<td>'+esc(r.designation||'—')+'</td>'+
        '<td><span class="pill pill-leavetype">'+esc(r.leave_type||'—')+'</span></td>'+
      '</tr>';
    }).join(''):'<tr><td colspan="6" class="empty">No one on leave on '+regDate+'</td></tr>';
    attachEmpLinks($('tbody-reg-leave'));

    // ── Off / Rest-Day sub-table ──
    $('reg-off-cnt').textContent=off.length+' off';
    $('tbody-reg-off').innerHTML=off.length?off.map(function(r,i){
      return '<tr>'+
        '<td class="t-code">'+(i+1)+'</td>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.name)+'" data-empname="'+esc(r.employee_name)+'">'+esc(r.employee_name||'—')+'</span></td>'+
        '<td class="t-code">'+esc(r.name||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.custom_farm||'—')+'</span></td>'+
        '<td>'+esc(r.designation||'—')+'</td>'+
        '<td><span class="pill pill-off">'+esc(r.off_type||'Off')+'</span></td>'+
      '</tr>';
    }).join(''):'<tr><td colspan="6" class="empty">No rest-day employees on '+regDate+'</td></tr>';
    attachEmpLinks($('tbody-reg-off'));
    // hide off sub-table entirely when empty to reduce clutter on working days
    $('reg-off-sub').style.display=off.length?'':'none';

    registerData.absent=filteredAbsent;
    // range mode: re-scan with the new filters instead of dropping back to the
    // dashboard-date list (keeps the picked dates)
    if(window._attRangeOn && window.attFindOpenDates){ window.attFindOpenDates(true); }
    else { renderAttChecklist(); }
  }

  // ── CHECKIN TABLE ──
  function pivotRows(raw){
    var byKey={};
    raw.forEach(function(r){
      var date=String(r.time||'').slice(0,10);if(!date)return;
      var key=(r.employee||'')+'||'+date;
      if(!byKey[key])byKey[key]={date:date,employee:r.employee,employee_name:r.employee_name,farm:r.farm,shift:r.shift,in_time:null,out_time:null,minutes_late:null,minutes_early:null,hours_worked:null};
      var row=byKey[key];
      if(r.log_type==='IN'){if(!row.in_time||r.time<row.in_time){row.in_time=r.time;row.minutes_late=r.minutes_late}}
      else if(r.log_type==='OUT'){if(!row.out_time||r.time>row.out_time){row.out_time=r.time;row.minutes_early=r.minutes_early}}
    });
    Object.keys(byKey).forEach(function(k){
      var row=byKey[k];
      if(row.in_time&&row.out_time){var t1=new Date(String(row.in_time).replace(' ','T')),t2=new Date(String(row.out_time).replace(' ','T'));row.hours_worked=(!isNaN(t1)&&!isNaN(t2)&&t2>t1)?(t2-t1)/3600000:null}
    });
    return Object.keys(byKey).map(function(k){return byKey[k]});
  }

  function renderTable(){
    var search=($('r-search').value||'').toLowerCase().trim();
    var pivoted=pivotRows(allRows);
    var filtered=pivoted.filter(function(r){
      if(!search)return true;
      return ((r.employee_name||'')+' '+(r.employee||'')+' '+(r.farm||'')+' '+(r.shift||'')).toLowerCase().indexOf(search)!==-1;
    });
    filtered.sort(function(a,b){
      var av=a[sortKey]||'',bv=b[sortKey]||'';
      return sortDir==='asc'?(av<bv?-1:av>bv?1:0):(av>bv?-1:av<bv?1:0);
    });
    filteredPivoted=filtered;
    $('r-rowcount').textContent=filtered.length+(filtered.length!==pivoted.length?' / '+pivoted.length:'')+' rows';
    if(!filtered.length){$('r-tbody').innerHTML='<tr><td colspan="9" class="empty">No records</td></tr>';return}
    $('r-tbody').innerHTML=filtered.map(function(r){
      var flags=[];
      if(r.minutes_late)flags.push('<span class="pill pill-late">'+Math.round(r.minutes_late)+'m late</span>');
      if(r.minutes_early)flags.push('<span class="pill pill-early">'+Math.round(r.minutes_early)+'m early</span>');
      if(!flags.length){
        if(r.in_time&&r.out_time)flags.push('<span class="pill pill-ok">on time</span>');
        else if(r.in_time&&!r.out_time)flags.push('<span class="pill pill-late">no OUT</span>');
        else flags.push('<span class="pill pill-abs">no IN</span>');
      }
      return '<tr>'+
        '<td class="t-time">'+esc(r.date)+'</td>'+
        '<td class="t-name"><span class="emp-link" data-empid="'+esc(r.employee)+'" data-empname="'+esc(r.employee_name||r.employee)+'">'+esc(r.employee_name||'—')+'</span></td>'+
        '<td class="t-code">'+esc(r.employee||'—')+'</td>'+
        '<td>'+(r.farm?'<span class="pill pill-farm">'+esc(r.farm)+'</span>':'—')+'</td>'+
        '<td>'+esc(r.shift||'—')+'</td>'+
        '<td>'+(r.in_time?'<span class="t-time">'+fdHM(r.in_time)+'</span>':'<span style="color:var(--text-3)">—</span>')+'</td>'+
        '<td>'+(r.out_time?'<span class="t-time">'+fdHM(r.out_time)+'</span>':'<span style="color:var(--text-3)">—</span>')+'</td>'+
        '<td>'+(r.hours_worked!=null?'<span class="t-time">'+fmtH(r.hours_worked)+'</span>':'<span style="color:var(--text-3)">—</span>')+'</td>'+
        '<td>'+flags.join(' ')+'</td>'+
      '</tr>';
    }).join('');
    document.querySelectorAll('thead th[data-sort]').forEach(function(th){
      th.classList.remove('s','asc','desc');
      if(th.getAttribute('data-sort')===sortKey)th.classList.add('s',sortDir);
    });
    attachEmpLinks($('r-tbody'));
  }

  function attachEmpLinks(container){
    if(!container) return;
    container.querySelectorAll('.emp-link').forEach(function(el){
      el.addEventListener('click',function(){openDrawer(el.getAttribute('data-empid'),el.getAttribute('data-empname'))});
    });
  }

  // ── SORT ──
  document.querySelectorAll('thead th[data-sort]').forEach(function(th){
    th.addEventListener('click',function(){
      var k=th.getAttribute('data-sort');
      if(sortKey===k)sortDir=sortDir==='asc'?'desc':'asc';
      else{sortKey=k;sortDir=(k==='date'||k==='in_time'||k==='out_time'||k==='hours_worked')?'desc':'asc'}
      renderTable();
    });
  });

  // ── EMPLOYEE DRAWER ──
  function openDrawer(empId, empName){
    currentEmpId=empId;
    $('dr-name').textContent=empName||empId;
    $('dr-sub').textContent=empId;
    // default range = payroll period from Biometric Setting (fallback: last 90 days)
    var _pf=(window._payroll&&window._payroll.from)||daysAgoISO(89);
    var _pt=(window._payroll&&window._payroll.to)||todayISO();
    if($('dr-from')._setDate) $('dr-from')._setDate(_pf); else $('dr-from').value=_pf;
    if($('dr-to')._setDate) $('dr-to')._setDate(_pt); else $('dr-to').value=_pt;
    $('emp-overlay').classList.add('open');
    // Setting the range above is not a user edit — record it as already seen so
    // the watcher does not fire a second, identical fetch.
    drSyncDateWatch();
    loadEmpHistory(empId);
  }

  function closeDrawer(){
    $('emp-overlay').classList.remove('open');
    destroyDrCharts();
    $('dr-body').innerHTML='<div class="empty">Select a date range above and click Load.</div>';
    currentEmpId=null;
  }

  $('dr-close').addEventListener('click',closeDrawer);
  $('emp-overlay').addEventListener('click',function(e){if(e.target===$('emp-overlay'))closeDrawer()});

  // The drawer reloads when either date changes — there is no LOAD button.
  // The desk date control writes its value into the hidden input, and does so
  // through its own change handler rather than a DOM event we can bind to, so
  // the value is watched instead. Debounced, because picking a range touches
  // both inputs and only the second one should trigger the fetch.
  var _drDateSeen={};
  function drSyncDateWatch(){
    ['dr-from','dr-to'].forEach(function(id){ _drDateSeen[id]=(($(id)||{}).value||''); });
  }
  (function watchDrawerDates(){
    var pending=null, last=_drDateSeen;
    drSyncDateWatch();
    setInterval(function(){
      var overlay=$('emp-overlay');
      if(!overlay || !overlay.classList.contains('open')) return;   // drawer closed
      var changed=false;
      ['dr-from','dr-to'].forEach(function(id){
        var v=(($(id)||{}).value||'');
        if(v!==last[id]){ last[id]=v; changed=true; }
      });
      if(!changed || !currentEmpId) return;
      clearTimeout(pending);
      pending=setTimeout(function(){
        if(!$('dr-from').value || !$('dr-to').value) return;
        loadEmpHistory(currentEmpId);
      }, 350);
    }, 250);
  })();

  function loadEmpHistory(empId){
    var from=$('dr-from').value;
    var to=$('dr-to').value;
    if(!from||!to){$('dr-body').innerHTML='<div class="alert">Please select a date range.</div>';return}
    $('dr-body').innerHTML='<div class="empty" style="padding:40px">Loading history…</div>';
    destroyDrCharts();
    var qs=new URLSearchParams({emp_id:empId,from_date:from,to_date:to}).toString();
    fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_employee_history?'+qs)
      .then(function(r){return r.json()})
      .then(function(res){
        var d=res.message;
        if(!d||d.error)throw new Error(d?d.error:'Empty response');
        renderDrawer(d);
      })
      .catch(function(e){$('dr-body').innerHTML='<div class="alert">Error: '+esc(e.message)+'</div>'});
  }

  function renderDrawer(d){
    var emp=d.employee||{},kpi=d.kpi||{},rows=d.rows||[],monthly=d.monthly||[];
    var parts=[];
    if(emp.designation)parts.push(emp.designation);
    if(emp.custom_farm)parts.push(emp.custom_farm);
    if(emp.company)parts.push(emp.company);
    parts.push(d.from_date+' → '+d.to_date);
    if(kpi.is_night_shift){
      parts.push('NIGHT SHIFT \u2014 starts '+((kpi.shift_start||'').slice(0,5)||'evening')+' (runs past midnight)');
    }
    $('dr-sub').textContent=parts.join(' · ');
    var pivoted=pivotDrawerRows(rows);
    var html='<div class="dr-kpis">'+
      drKpi('Days Present',kpi.days_seen||0)+
      drKpi('Absent',kpi.absent_days||0)+
      drKpi('Leaves',kpi.leave_days||0)+
      drKpi('Week Off',kpi.weekoff_days||0)+
      drKpi('Late Arrivals',kpi.late_days||0)+
      drKpi('Early Departs',kpi.early_days||0)+
      drKpi('Days in Range',pivoted.length)+
    '</div>';
    html+='<div class="dr-actions"><div style="display:flex;gap:6px">'+
      '<button class="btn export-csv" id="dr-csv">CSV</button>'+
      '<button class="btn export-xls" id="dr-xls">EXCEL</button>'+
    '</div><span style="font-family:var(--f-ui);font-size:12px;color:var(--text-3)">'+pivoted.length+' day records</span></div>';
    html+='<div class="dr-charts">'+
      '<div class="dr-chart-card"><h4>Monthly Attendance</h4><div class="dr-ch"><canvas id="dr-ch-monthly"></canvas></div></div>'+
      '<div class="dr-chart-card"><h4>Exception Breakdown</h4><div class="dr-ch"><canvas id="dr-ch-exc"></canvas></div></div>'+
    '</div>';
    html+='<div class="dr-tbl-wrap"><table class="dr-tbl"><thead><tr>'+
      '<th>Date</th><th>Shift</th><th>In</th><th>Out</th><th>Hours</th><th>Status</th>'+
    '</tr></thead><tbody>'+pivoted.map(function(r){
      var flags=[];
      if(r.minutes_late)flags.push('<span class="pill pill-late">'+Math.round(r.minutes_late)+'m late</span>');
      if(r.minutes_early)flags.push('<span class="pill pill-early">'+Math.round(r.minutes_early)+'m early</span>');
      if(!flags.length){
        if(r.in_time&&r.out_time)flags.push('<span class="pill pill-ok">on time</span>');
        else if(r.in_time)flags.push('<span class="pill" style="background:#fef3c7;color:#92400e">no OUT</span>');
        else flags.push('<span class="pill pill-late">absent</span>');
      }
      return '<tr>'+
        '<td class="t-time">'+esc(r.date)+'</td>'+
        '<td style="color:var(--text-3);font-size:12px">'+esc(r.shift||'—')+'</td>'+
        '<td class="t-time">'+(r.in_time?fdHM(r.in_time):'<span style="color:var(--text-3)">—</span>')+'</td>'+
        '<td class="t-time">'+(r.out_time?fdHM(r.out_time):'<span style="color:var(--text-3)">—</span>')+'</td>'+
        '<td class="t-time">'+(r.hours_worked!=null?fmtH(r.hours_worked):'<span style="color:var(--text-3)">—</span>')+'</td>'+
        '<td>'+flags.join(' ')+'</td>'+
      '</tr>';
    }).join('')+'</tbody></table></div>';
    $('dr-body').innerHTML=html;
    $('dr-csv').addEventListener('click',function(){exportDrawerCSV(d,pivoted)});
    $('dr-xls').addEventListener('click',function(){exportDrawerXLS(d,pivoted)});
    // Charts
    if(monthly.length){
      var mc=$('dr-ch-monthly');
      if(mc) drCharts.monthly=new Chart(mc.getContext('2d'),{type:'bar',data:{
        labels:monthly.map(function(m){return m.month}),
        datasets:[
          {label:'Days present',data:monthly.map(function(m){return m.days_present||0}),backgroundColor:'rgba(37,99,235,.7)',borderRadius:2},
          {label:'Late',data:monthly.map(function(m){return m.late_days||0}),backgroundColor:'rgba(185,28,28,.7)',borderRadius:2},
        ]
      },options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'}},scales:{x:{grid:{display:false}},y:{grid:{color:'rgba(0,0,0,.04)'},beginAtZero:true,ticks:{precision:0}}}}});
    }
    var onTime=Math.max(0,(kpi.in_count||0)-(kpi.late_days||0)),normalOut=Math.max(0,(kpi.out_count||0)-(kpi.early_days||0));
    if(onTime+(kpi.late_days||0)+normalOut+(kpi.early_days||0)>0){
      var ec=$('dr-ch-exc');
      if(ec) drCharts.exc=new Chart(ec.getContext('2d'),{type:'doughnut',data:{
        labels:['On-time IN','Late IN','Normal OUT','Early Out'],
        datasets:[{data:[onTime,kpi.late_days||0,normalOut,kpi.early_days||0],backgroundColor:['#0a7a43','#b91c1c','#a8a59b','#d9a514'],borderColor:'#fff',borderWidth:2}]
      },options:{responsive:true,maintainAspectRatio:false,cutout:'58%',plugins:{legend:{position:'bottom'}}}});
    }
  }

  function drKpi(lbl,val){return '<div class="dr-kpi"><div class="lbl">'+lbl+'</div><div class="val">'+fmt(val)+'</div></div>'}

  function pivotDrawerRows(raw){
    var byKey={};
    raw.forEach(function(r){
      var date=String(r.time||'').slice(0,10);if(!date)return;
      if(!byKey[date])byKey[date]={date:date,shift:r.shift,in_time:null,out_time:null,minutes_late:null,minutes_early:null,hours_worked:null};
      var row=byKey[date];
      if(r.log_type==='IN'){if(!row.in_time||r.time<row.in_time){row.in_time=r.time;row.minutes_late=r.minutes_late}}
      else{if(!row.out_time||r.time>row.out_time){row.out_time=r.time;row.minutes_early=r.minutes_early}}
    });
    Object.keys(byKey).forEach(function(k){
      var row=byKey[k];
      if(row.in_time&&row.out_time){var t1=new Date(String(row.in_time).replace(' ','T')),t2=new Date(String(row.out_time).replace(' ','T'));row.hours_worked=(!isNaN(t1)&&!isNaN(t2)&&t2>t1)?(t2-t1)/3600000:null}
    });
    return Object.keys(byKey).sort(function(a,b){return a<b?1:-1}).map(function(k){return byKey[k]});
  }

  // ── EXPORT ──
  function buildExport(pivoted){
    return pivoted.map(function(r){
      var s=[];
      if(r.minutes_late)s.push(Math.round(r.minutes_late)+'m late');
      if(r.minutes_early)s.push(Math.round(r.minutes_early)+'m early');
      if(!s.length){if(r.in_time&&r.out_time)s.push('on time');else if(r.in_time)s.push('no OUT');else s.push('no IN')}
      return {'Date':r.date||'','Employee ID':r.employee||'','Employee Name':r.employee_name||'','Farm':r.farm||'','Shift':r.shift||'','Time IN':r.in_time?fdHM(r.in_time):'','Time OUT':r.out_time?fdHM(r.out_time):'','Hours Worked':r.hours_worked!=null?fmtHp(r.hours_worked):'','Minutes Late':r.minutes_late!=null?Math.round(r.minutes_late):'','Minutes Early':r.minutes_early!=null?Math.round(r.minutes_early):'','Status':s.join(', ')};
    });
  }

  function toCSV(data){
    if(!data.length)return '';
    var cols=Object.keys(data[0]);
    return [cols.join(',')].concat(data.map(function(row){return cols.map(function(c){var v=row[c]==null?'':String(row[c]);return v.indexOf(',')!==-1||v.indexOf('"')!==-1?'"'+v.replace(/"/g,'""')+'"':v}).join(',')})).join('\r\n');
  }

  function dlText(content,filename){var b=new Blob([content],{type:'text/csv'});var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=filename;a.click()}
  function dlXLSX(data,sheet,filename){if(!window.XLSX){alert('SheetJS not loaded');return}var ws=XLSX.utils.json_to_sheet(data);var wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,ws,sheet);XLSX.writeFile(wb,filename)}

  $('btn-csv').addEventListener('click',function(){var data=buildExport(filteredPivoted);if(!data.length){alert('No data');return}dlText(toCSV(data),'attendance_'+$('r-from').value+'_'+$('r-to').value+'.csv')});
  $('btn-xls').addEventListener('click',function(){var data=buildExport(filteredPivoted);if(!data.length){alert('No data');return}dlXLSX(data,'Attendance','attendance_'+$('r-from').value+'_'+$('r-to').value+'.xlsx')});

  $('btn-reg-csv').addEventListener('click',function(){
    var to=$('r-to').value;
    var pData=registerData.present.map(function(r,i){
      return {'#':i+1,'Status':'Present','Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||'','Detail':r.source==='manual'?(r.marking_reason||r.att_status||'Manual'):(r.source==='night'?'Night':'Biometric'),'In':r.in_time?fdHM(r.in_time):'','Out':r.out_time?fdHM(r.out_time):''};
    });
    var lData=(registerData.on_leave||[]).map(function(r,i){
      return {'#':i+1,'Status':'On Leave','Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||'','Detail':r.leave_type||'','In':'','Out':''};
    });
    var oData=(registerData.off||[]).map(function(r,i){
      return {'#':i+1,'Status':'Off','Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||'','Detail':r.off_type||'Off','In':'','Out':''};
    });
    var aData=registerData.absent.map(function(r,i){
      return {'#':i+1,'Status':'Absent','Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||'','Detail':'','In':'','Out':''};
    });
    dlText(toCSV(pData.concat(lData).concat(oData).concat(aData)),'attendance_register_'+to+'.csv');
  });
  $('btn-reg-xls').addEventListener('click',function(){
    var to=$('r-to').value;
    if(!window.XLSX){alert('SheetJS not loaded');return}
    var wb=XLSX.utils.book_new();
    var pData=registerData.present.map(function(r,i){
      return {'#':i+1,'Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||'','Source':r.source==='manual'?(r.marking_reason||r.att_status||'Manual'):(r.source==='night'?'Night':'Biometric'),'In':r.in_time?fdHM(r.in_time):'','Out':r.out_time?fdHM(r.out_time):''};
    });
    var lData=(registerData.on_leave||[]).map(function(r,i){
      return {'#':i+1,'Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||'','Leave Type':r.leave_type||''};
    });
    var oData=(registerData.off||[]).map(function(r,i){
      return {'#':i+1,'Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||'','Type':r.off_type||'Off'};
    });
    var aData=registerData.absent.map(function(r,i){
      return {'#':i+1,'Employee ID':r.name||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Designation':r.designation||''};
    });
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(pData.length?pData:[{'Note':'No present data'}]), 'Present');
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(lData.length?lData:[{'Note':'No leave data'}]), 'On Leave');
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(oData.length?oData:[{'Note':'No off data'}]), 'Off');
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(aData.length?aData:[{'Note':'No absent data'}]), 'Absent');
    XLSX.writeFile(wb,'attendance_register_'+to+'.xlsx');
  });

  // ── LEAVE EXPORT ──
  function buildLeaveExport(){
    return (leaveData.rows||[]).map(function(r,i){
      return {'#':i+1,'Employee ID':r.employee||'','Name':r.employee_name||'','Farm':r.custom_farm||'','Department':r.department||'','Leave Type':r.leave_type||'','From':r.from_date||'','To':r.to_date||'','Days':r.total_leave_days!=null?r.total_leave_days:'','Half Day':r.half_day?'Yes':''};
    });
  }
  $('btn-leave-csv').addEventListener('click',function(){var data=buildLeaveExport();if(!data.length){alert('No leave data');return}dlText(toCSV(data),'on_leave_'+$('r-to').value+'.csv')});
  $('btn-leave-xls').addEventListener('click',function(){var data=buildLeaveExport();if(!data.length){alert('No leave data');return}dlXLSX(data,'On Leave','on_leave_'+$('r-to').value+'.xlsx')});

  function exportDrawerCSV(d,pivoted){
    var emp=d.employee||{};
    var data=pivoted.map(function(r){var s=[];if(r.minutes_late)s.push(Math.round(r.minutes_late)+'m late');if(r.minutes_early)s.push(Math.round(r.minutes_early)+'m early');if(!s.length){if(r.in_time&&r.out_time)s.push('on time');else if(r.in_time)s.push('no OUT');else s.push('absent')}return {'Date':r.date,'Employee ID':emp.name||d.emp_id,'Name':emp.employee_name,'Farm':emp.custom_farm,'Shift':r.shift||'','Time IN':r.in_time?fdHM(r.in_time):'','Time OUT':r.out_time?fdHM(r.out_time):'','Hours':r.hours_worked!=null?fmtHp(r.hours_worked):'','Status':s.join(', ')}});
    dlText(toCSV(data),'history_'+(emp.name||d.emp_id)+'_'+d.from_date+'_'+d.to_date+'.csv');
  }
  function exportDrawerXLS(d,pivoted){
    var emp=d.employee||{};
    var data=pivoted.map(function(r){var s=[];if(r.minutes_late)s.push(Math.round(r.minutes_late)+'m late');if(r.minutes_early)s.push(Math.round(r.minutes_early)+'m early');if(!s.length){if(r.in_time&&r.out_time)s.push('on time');else if(r.in_time)s.push('no OUT');else s.push('absent')}return {'Date':r.date,'Employee ID':emp.name||d.emp_id,'Name':emp.employee_name,'Farm':emp.custom_farm,'Shift':r.shift||'','Time IN':r.in_time?fdHM(r.in_time):'','Time OUT':r.out_time?fdHM(r.out_time):'','Hours':r.hours_worked!=null?fmtHp(r.hours_worked):'','Status':s.join(', ')}});
    dlXLSX(data,'History','history_'+(emp.name||d.emp_id)+'_'+d.from_date+'_'+d.to_date+'.xlsx');
  }

  /* date-picker change handler wired in initDateCtl */
  $('r-farm').addEventListener('change',function(){load();scheduleAutoRefresh()});
  $('r-company').addEventListener('change',function(){load();scheduleAutoRefresh()});
  $('r-emptype').addEventListener('change',function(){load();scheduleAutoRefresh()});
  $('r-search').addEventListener('input',renderTable);

  // ── AUTO-REFRESH every 30 seconds when viewing today ──
  var autoRefreshTimer = null;
  function scheduleAutoRefresh(){
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(function(){
      var to=$('r-to').value, today=todayISO();
      if(to===today){
        refreshSnapshot();
      }
    }, 30 * 1000);
  }

  // ── SHIFT SUBMIT ──
  $('shift-submit').addEventListener('click', function(){
    var selected=[];
    document.querySelectorAll('.shift-chk:checked').forEach(function(c){
      selected.push({id:c.getAttribute('data-id'),name:c.getAttribute('data-name')});
    });
    var newShift=$('shift-new').value;
    var effDate=$('shift-date').value;
    if(!selected.length){$('shift-feedback').innerHTML='<div class="alert">No employees selected.</div>';return}
    if(!newShift){$('shift-feedback').innerHTML='<div class="alert">Please select a shift.</div>';return}
    if(!effDate){$('shift-feedback').innerHTML='<div class="alert">Please set an effective date.</div>';return}
    $('shift-feedback').innerHTML='<div class="info-box">Updating shift for '+selected.length+' employees…</div>';
    $('shift-submit').disabled=true;

    var empIds = selected.map(function(e){return e.id}).join(',');
    var csrf = '';
    try { csrf = frappe.csrf_token || ''; } catch(e){}
    if(!csrf){ var m=document.cookie.match(/csrftoken=([^;]+)/); csrf=m?m[1]:''; }

    fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.shift_assign', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Frappe-CSRF-Token': csrf
      },
      body: 'emp_ids='+encodeURIComponent(empIds)+
            '&shift_type='+encodeURIComponent(newShift)+
            '&start_date='+encodeURIComponent(effDate)
    })
    .then(function(r){return r.json()})
    .then(function(res){
      var msg_data = res.message || {};
      var ok_count  = msg_data.ok_count  || 0;
      var err_count = msg_data.err_count || 0;
      var results   = msg_data.results   || [];
      var msg='<div style="padding:10px 14px;border-radius:6px;background:'+(err_count&&!ok_count?'#fef2f2':'#ecfdf5')+';border:1px solid '+(err_count&&!ok_count?'#fecaca':'#a7f3d0')+';font-size:13px">';
      msg+='<strong>'+(err_count&&!ok_count?'FAILED':err_count?'PARTIAL':'DONE')+'</strong> — '+ok_count+' shift assignments created ('+esc(newShift)+', from '+esc(effDate)+').';
      if(err_count) msg+='<br>'+results.filter(function(r){return !r.ok}).map(function(r){return '• '+esc(r.employee)+': '+esc(r.error||'')}).join('<br>');
      msg+='</div>';
      $('shift-feedback').innerHTML=msg;
      $('shift-submit').disabled=false;
      if(!err_count) autoHideFeedback('shift-feedback', 1000);
      if(ok_count) loadShiftEmployees();
    })
    .catch(function(e){
      $('shift-feedback').innerHTML='<div class="alert">Request failed: '+esc(e.message)+'</div>';
      $('shift-submit').disabled=false;
    });
  });

  // ── SEARCH FILTER FUNCTIONS (global scope for oninput) ──

  // searchable selects: the two Actions checklists list matching employees as you
  // type; picking one ticks that employee (so several can be added in a row).
  (function initSearchSelects(){
    function opts(rows, q, metaFn){
      q=(q||'').toLowerCase();
      return (rows||[]).filter(function(r){
        if(!q) return true;
        return ((r.employee_name||'')+' '+(r.name||'')).toLowerCase().indexOf(q)>=0;
      }).map(function(r){
        return {id:r.name, label:(r.employee_name||r.name||''), meta:(metaFn?metaFn(r):'')};
      });
    }
    function tick(scope, cls, id){
      var boxes=document.querySelectorAll(scope+' .'+cls);
      for(var i=0;i<boxes.length;i++){
        if(boxes[i].getAttribute('data-id')===id){
          boxes[i].checked=true;
          var tr=boxes[i].closest?boxes[i].closest('tr'):null;
          if(tr){ tr.style.display=''; if(tr.scrollIntoView) tr.scrollIntoView({block:'nearest'}); }
          return true;
        }
      }
      return false;
    }
    attachSearchSelect('att-search',
      function(q){ return opts(registerData.absent, q, function(r){
        return (r.custom_farm||'')+(r.designation?' · '+r.designation:''); }); },
      function(o){ tick('#att-checklist','att-chk',o.id); if(window.filterAttChecklist) filterAttChecklist(); },
      function(){ if(window.filterAttChecklist) filterAttChecklist(); });

    attachSearchSelect('shift-search',
      function(q){ return opts(window._shiftEmps, q, function(r){
        return (r.designation||'')+' · '+(r.default_shift||'no shift'); }); },
      function(o){ tick('#shift-checklist','shift-chk',o.id); if(window.filterShiftChecklist) filterShiftChecklist(); },
      function(){ if(window.filterShiftChecklist) filterShiftChecklist(); });

    // drill-down table: picking an option filters the table to that employee
    attachSearchSelect('tile-inline-search',
      function(q){
        var rows=(window._tileLists&&window._tileOpen)?(window._tileLists[window._tileOpen]||[]):[];
        return opts(rows, q, function(r){ return (r.custom_farm||r.farm||'')+(r.shift?' · '+r.shift:''); });
      },
      function(o){ var s=$('tile-inline-search'); if(s){ s.value=o.id; if(s.oninput) s.oninput(); } });
  })();


  // ── Date-range marking ────────────────────────────────────────────────────
  // "FIND OPEN DATES" asks the backend which dates in the range the SELECTED
  // employees have no attendance record or an Absent one, then offers those dates
  // as chips. Marking then covers every ticked employee × ticked date.
  window._attDays = [];
  function attSelectedIds(){
    var out=[];
    document.querySelectorAll('.att-chk:checked').forEach(function(c){ out.push(c.getAttribute('data-id')); });
    return out;
  }
  // employees whose gap dates intersect the picked chips (all of them if none picked)
  function attVisibleRangeEmps(){
    var picked=(window._attDays||[]).filter(function(d){return d.on}).map(function(d){return d.date});
    var emps=window._attRangeEmps||[];
    if(!picked.length) return emps;
    return emps.filter(function(e){
      var g=e.gap_dates||[];
      for(var i=0;i<picked.length;i++){ if(g.indexOf(picked[i])>=0) return true; }
      return false;
    });
  }
  // re-render the checklist for the picked dates, preserving existing ticks
  function attRefreshForDays(){
    if(!(window._attRangeEmps && window._attRangeEmps.length)) return;
    var ticked={};
    document.querySelectorAll('.att-chk:checked').forEach(function(c){ ticked[c.getAttribute('data-id')]=1; });
    renderAttChecklist(attVisibleRangeEmps());
    document.querySelectorAll('.att-chk').forEach(function(c){
      if(ticked[c.getAttribute('data-id')]) c.checked=true;
    });
  }
  // Auto-dismiss a clean success message. Failures are LEFT on screen so the
  // per-employee reasons can still be read.
  function autoHideFeedback(id, ms){
    var el=$(id); if(!el) return;
    var token=(el._hideToken||0)+1; el._hideToken=token;
    setTimeout(function(){
      if(el._hideToken!==token) return;   // a newer message replaced this one
      el.classList.add('fb-fade');
      setTimeout(function(){
        if(el._hideToken!==token) return;
        el.innerHTML=''; el.classList.remove('fb-fade');
      }, 260);
    }, ms || 1000);
  }
  function attRenderDays(){
    var box=$('att-range-days'); if(!box) return;
    if(!window._attDays.length){ box.innerHTML=''; return; }
    box.innerHTML=window._attDays.map(function(d,i){
      var parts=[];
      if(d.absent)  parts.push(d.absent+' absent');
      if(d.absent_with_scan) parts.push(d.absent_with_scan+' absent+scan');
      if(d.absent_on_off) parts.push(d.absent_on_off+' absent on off');
      if(d.missing) parts.push(d.missing+' no record');
      if(d.holiday && (d.absent||d.absent_with_scan||d.absent_on_off||d.missing)) parts.push(d.holiday+' off');
      if(!parts.length){
        if(d.holiday) parts.push(d.holiday+' off');
        else if(d.leave) parts.push(d.leave+' leave');
        else if(d.present) parts.push('all present');
      }
      var dis = d.markable ? '' : ' none';
      return '<div class="att-day'+(d.on?' on':'')+dis+'" data-i="'+i+'">'+
             '<span class="ad-d">'+esc(String(d.date).slice(5))+(d.future?' ⟳':'')+'</span>'+
             '<span class="ad-m">'+esc(parts.join(' · ')||'—')+'</span></div>';
    }).join('');
    var els=box.querySelectorAll('.att-day');
    for(var i=0;i<els.length;i++){
      els[i].addEventListener('click', function(){
        var d=window._attDays[parseInt(this.getAttribute('data-i'),10)];
        if(!d || !d.markable) return;
        d.on=!d.on; attRenderDays(); attRefreshForDays(); attUpdateNote();
      });
    }
  }
  function attUpdateNote(){
    var n=$('att-range-note'); if(!n) return;
    var days=window._attDays.filter(function(d){return d.on});
    if(!window._attDays.length){ n.textContent=''; return; }
    var markable=window._attDays.filter(function(d){return d.markable}).length;
    var ticked=attSelectedIds().length;
    var onDates=attVisibleRangeEmps().length;
    n.textContent = days.length
      ? (days.length+' date'+(days.length>1?'s':'')+' · '+onDates+' employee'+(onDates===1?'':'s')+
         ' with gaps'+(ticked?(' · '+ticked+' ticked'):''))
      : (markable+' date'+(markable===1?'':'s')+' with gaps — click to pick');
  }
  (function initAttRange(){
    // Exposed so a filter change (sidebar farm/company, employment type, date) can
    // RE-RUN the scan instead of resetting the view back to the dashboard-date list.
    window.attFindOpenDates=function(keepPicks){
      var ids=attSelectedIds();
      var f=($('att-range-from')||{}).value, t=($('att-range-to')||{}).value;
      if(!f||!t){ if($('att-range-note')) $('att-range-note').textContent='Pick a date range'; return; }
      var prevOn={};
      if(keepPicks){ (window._attDays||[]).forEach(function(d){ if(d.on) prevOn[d.date]=1; }); }
      if($('att-range-note')) $('att-range-note').textContent=
        'Checking '+(ids.length?ids.length+' selected':'all filtered')+' employees\u2026';
      fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_mark', {
        method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded',
                 'X-Frappe-CSRF-Token':(function(){ try{return frappe.csrf_token||''}catch(e){
                   var m=document.cookie.match(/csrftoken=([^;]+)/); return m?m[1]:'' } })()},
        body:'mode=gaps&emp_ids='+encodeURIComponent(ids.join(','))+
             '&from_date='+encodeURIComponent(f)+'&to_date='+encodeURIComponent(t)+
             (ids.length?'':('&farm='+encodeURIComponent(($('r-farm')||{}).value||'')+
                             '&company='+encodeURIComponent(($('r-company')||{}).value||'')+
                             '&employment_type='+encodeURIComponent(($('r-emptype')||{}).value||'')))
      })
      .then(function(r){return r.json()})
      .then(function(res){
        var m=res.message||{};
        if(m.error){ $('att-range-note').textContent=m.error; return; }
        window._attRangeOn=true;
        window._attDays=(m.dates||[]).map(function(d){ d.on=!!prevOn[d.date] && !!d.markable; return d; });
        window._attRangeEmps=(m.gap_employees||[]);
        renderAttChecklist(window._attRangeEmps);
        attRenderDays(); attUpdateNote();
      })
      .catch(function(e){ if($('att-range-note')) $('att-range-note').textContent='Failed: '+e.message; });
    };
    var load=$('att-range-load');
    if(load) load.addEventListener('click', function(){ window.attFindOpenDates(false); });
    var all=$('att-range-all');
    if(all) all.addEventListener('click', function(){
      window._attDays.forEach(function(d){ if(d.markable) d.on=true; });
      attRenderDays(); attRefreshForDays(); attUpdateNote();
    });
    var none=$('att-range-none');
    if(none) none.addEventListener('click', function(){
      window._attDays.forEach(function(d){ d.on=false; });
      attRenderDays(); attRefreshForDays(); attUpdateNote();
    });
  })();

  window.filterAttChecklist = function(){
    var q=($('att-search').value||'').toLowerCase().trim();
    var rows=document.querySelectorAll('#att-checklist tbody tr');
    var visible=0;
    rows.forEach(function(tr){
      var hide=q&&tr.textContent.toLowerCase().indexOf(q)===-1;
      tr.style.display=hide?'none':'';
      if(!hide)visible++;
    });
    var c=$('att-row-count');
    if(c) c.textContent=(q?visible+' / ':'')+rows.length+' rows';
  };

  window.filterShiftChecklist = function(){
    var q=($('shift-search').value||'').toLowerCase().trim();
    var rows=document.querySelectorAll('#shift-checklist tbody tr');
    var visible=0;
    rows.forEach(function(tr){
      var hide=q&&tr.textContent.toLowerCase().indexOf(q)===-1;
      tr.style.display=hide?'none':'';
      if(!hide)visible++;
    });
    var c=$('shift-row-count');
    if(c) c.textContent=(q?visible+' / ':'')+rows.length+' rows';
  };

  window.filterPresent = function(){
    var q=($('present-search').value||'').toLowerCase().trim();
    var rows=document.querySelectorAll('#tbody-present tr');
    var visible=0;
    rows.forEach(function(tr){
      var hide=q&&tr.textContent.toLowerCase().indexOf(q)===-1;
      tr.style.display=hide?'none':'';
      if(!hide)visible++;
    });
    var c=$('present-cnt');
    if(c&&q) c.textContent=visible+' shown';
  };

  window.filterAbsent = function(){
    var q=($('absent-search').value||'').toLowerCase().trim();
    var rows=document.querySelectorAll('#tbody-absent tr');
    var visible=0;
    rows.forEach(function(tr){
      var hide=q&&tr.textContent.toLowerCase().indexOf(q)===-1;
      tr.style.display=hide?'none':'';
      if(!hide)visible++;
    });
    var c=$('absent-cnt');
    if(c&&q) c.textContent=visible+' shown';
  };

  window.filterLeave = function(){
    var q=($('leave-search').value||'').toLowerCase().trim();
    var rows=document.querySelectorAll('#tbody-leave tr');
    var visible=0;
    rows.forEach(function(tr){
      var hide=q&&tr.textContent.toLowerCase().indexOf(q)===-1;
      tr.style.display=hide?'none':'';
      if(!hide)visible++;
    });
    var c=$('leave-cnt');
    if(c) c.textContent=(q?visible+' shown':(leaveData.rows||[]).length+' on leave');
  };

  $('att-date').value = window._selDate || todayISO();
  makeFrappeDate('att-date-ctl','att-date', window._selDate || todayISO());
  makeFrappeDate('att-rf-ctl','att-range-from', daysAgoISO(6));
  makeFrappeDate('att-rt-ctl','att-range-to', window._selDate || todayISO());
  makeFrappeDate('shift-date-ctl','shift-date', todayISO());
  makeFrappeDate('dr-from-ctl','dr-from', daysAgoISO(89));
  makeFrappeDate('dr-to-ctl','dr-to', todayISO());
  $('shift-date').value = todayISO();

  // Populate absent checklist whenever registerData.absent changes
  function renderAttChecklist(rangeRows){
    // the list always shows the DASHBOARD date's absentees, so the attendance date
    // must follow it (still editable for deliberate back-dating)
    var _ad=$('att-date');
    if(_ad && window._selDate){ if(_ad._setDate) _ad._setDate(window._selDate); else _ad.value=window._selDate; }
    // rangeRows = employees returned by FIND OPEN DATES (absent OR no record within
    // the chosen range). Without it, fall back to the dashboard date's absentees.
    var isRange = !!(rangeRows && rangeRows.length);
    var absent = (isRange ? rangeRows : (registerData.absent || [])).filter(function(r){
      return isRange ? true : !markedEmployeeIds.has(r.name);
    });
    var el = $('att-checklist');
    var countEl = $('att-row-count');
    if(!el) return;
    if(!absent.length){
      el.innerHTML='<div class="empty">No absent employees to mark — all accounted for</div>';
      if(countEl) countEl.textContent='0 rows';
      return;
    }
    if(countEl) countEl.textContent=absent.length+' rows'+(isRange?' with gaps in range':'');
    el.innerHTML = '<table class="tbl"><thead><tr>'+
      '<th style="width:36px"><input type="checkbox" id="att-chk-all" title="Select all"></th>'+
      '<th>Employee</th><th>Code</th><th>Farm</th><th>Designation</th><th>Employment Type</th>'+
      (isRange?'<th>Absent / Absent+Check-in / Off day / No record</th><th>Dates</th>':'')+
    '</tr></thead><tbody>'+
    absent.map(function(r,i){
      return '<tr>'+
        '<td><input type="checkbox" class="att-chk" data-id="'+esc(r.name)+'" data-name="'+esc(r.employee_name)+'" data-company="'+esc(r.company||'')+'"></td>'+
        '<td class="t-name">'+esc(r.employee_name||'—')+'</td>'+
        '<td class="t-code">'+esc(r.name||'')+'</td>'+
        '<td><span class="pill pill-farm">'+esc(r.custom_farm||'—')+'</span></td>'+
        '<td>'+esc(r.designation||'—')+'</td>'+
        '<td><span style="font-family:var(--f-ui);font-size:12px;color:var(--text-3)">'+esc(r.employment_type||'—')+'</span></td>'+
        (isRange
          ? '<td>'+
            ((r.absent_count||0)?'<span class="pill st-absent">'+r.absent_count+' absent</span> ':'')+
            ((r.absent_scan_count||0)?'<span class="pill st-late" title="Marked Absent but a check-in exists">'+
               r.absent_scan_count+' absent + check-in</span> ':'')+
            ((r.absent_off_count||0)?'<span class="pill st-off" title="Marked Absent on a weekly off / holiday — cancel the Absent instead of marking Present">'+
               r.absent_off_count+' absent on off day</span> ':'')+
            ((r.missing_count||0)?'<span class="pill st-nocheckout">'+r.missing_count+' no record</span>':'')+
            '</td>'+
            '<td><span class="ad-m">'+esc((r.gap_dates||[]).map(function(d){return String(d).slice(5)}).join(', '))+'</span></td>'
          : '')+
      '</tr>';
    }).join('')+
    '</tbody></table>';

    var chkAll = document.getElementById('att-chk-all');
    if(chkAll) chkAll.addEventListener('change', function(){
      _attVisibleChks().forEach(function(c){c.checked=chkAll.checked});
    });
  }

  function _attVisibleChks(){
    var out=[];
    document.querySelectorAll('#att-checklist tbody tr').forEach(function(tr){
      if(tr.style.display==='none') return;
      var c=tr.querySelector('.att-chk'); if(c) out.push(c);
    });
    return out;
  }
  $('att-select-all').addEventListener('click', function(){
    _attVisibleChks().forEach(function(c){c.checked=true});
  });
  $('att-clear-sel').addEventListener('click', function(){
    document.querySelectorAll('.att-chk').forEach(function(c){c.checked=false});
  });

  $('att-submit').addEventListener('click', function(){
    var selected = [];
    document.querySelectorAll('.att-chk:checked').forEach(function(c){
      selected.push({id: c.getAttribute('data-id'), name: c.getAttribute('data-name'), company: c.getAttribute('data-company')||''});
    });
    if(!selected.length){
      $('att-feedback').innerHTML='<div class="alert">No employees selected.</div>';
      return;
    }
    var attDate  = $('att-date').value;
    if(!attDate){
      $('att-feedback').innerHTML='<div class="alert">Please select an attendance date.</div>';
      return;
    }
    var reason = $('att-reason') ? $('att-reason').value : '';
    var reasonNote = reason ? (' as <strong>'+esc(reason)+'</strong>') : '';
    // Rest days are protected by default; this is a deliberate, per-submission override.
    var allowOff = !!($('att-allow-off') && $('att-allow-off').checked);
    $('att-submit').disabled=true;

    var csrf = '';
    try { csrf = frappe.csrf_token || ''; } catch(e){}
    if(!csrf){ var m=document.cookie.match(/csrftoken=([^;]+)/); csrf=m?m[1]:''; }

    // Each employee is an insert + submit (plus cancelling any existing Absent), so a
    // several-hundred-person batch in one request can exceed the gateway timeout.
    // Send sequential chunks and aggregate; one failed chunk never loses the rest.
    var allIds  = selected.map(function(e){return e.id});
    // ticked date chips (empty = just the single ATTENDANCE DATE)
    var pickedDates=(window._attDays||[]).filter(function(d){return d.on}).map(function(d){return d.date});
    // each request now covers employees × dates, so shrink the chunk accordingly
    var CHUNK   = pickedDates.length>1 ? Math.max(10, Math.floor(75/pickedDates.length)) : 75;
    var batches = [];
    for(var bi=0; bi<allIds.length; bi+=CHUNK){ batches.push(allIds.slice(bi, bi+CHUNK)); }
    var agg = {ok:0, err:0, replaced:0, offSkipped:0, offMarked:0, okIds:[], failures:[]};

    function attFinish(){
      var msg = '<div style="padding:10px 14px;border-radius:6px;background:'+(agg.err?'#fef2f2':'#ecfdf5')+
                ';border:1px solid '+(agg.err?'#fecaca':'#a7f3d0')+';font-size:13px">';
      msg += '<strong>'+(agg.err?'PARTIAL SUCCESS':'DONE')+'</strong>'+(reason?' — '+esc(reason):'')+'<br>';
      msg += agg.ok+' attendance records submitted for '+
             esc(pickedDates.length ? (pickedDates.length+' date(s): '+pickedDates.join(', ')) : attDate)+'.';
      if(agg.replaced){ msg += ' '+agg.replaced+' existing Absent record'+(agg.replaced>1?'s':'')+' cancelled and replaced.'; }
      if(agg.offSkipped){ msg += ' '+agg.offSkipped+' skipped — weekly off / holiday (never marked on a rest day).'; }
      if(agg.offMarked){ msg += ' <strong>'+agg.offMarked+' written on a REST DAY</strong> (week-off override was on).'; }
      if(agg.err){
        msg += '<br>'+agg.err+' failed:<br>';
        msg += agg.failures.slice(0,25).map(function(r){
          return '&nbsp;• '+esc(r.employee)+': '+esc(r.error||'error');
        }).join('<br>');
        if(agg.failures.length>25){ msg += '<br>&nbsp;… '+(agg.failures.length-25)+' more'; }
      }
      msg += '</div>';
      $('att-feedback').innerHTML=msg;
      $('att-submit').disabled=false;
      if(!agg.err && !agg.failures.length) autoHideFeedback('att-feedback', 1000);
      if(agg.ok>0){
        agg.okIds.forEach(function(id){ markedEmployeeIds.add(id); });
        registerData.absent = (registerData.absent||[]).filter(function(e){
          return !markedEmployeeIds.has(e.name);
        });
        // if a date-range scan is on screen, keep that view and drop the rows we just
        // marked; otherwise fall back to the dashboard-date list
        if(window._attRangeEmps && window._attRangeEmps.length){
          window._attRangeEmps=window._attRangeEmps.filter(function(e){
            return !markedEmployeeIds.has(e.name);
          });
          renderAttChecklist(window._attRangeEmps);
        } else {
          // range mode: re-scan with the new filters instead of dropping back to the
    // dashboard-date list (keeps the picked dates)
    if(window._attRangeOn && window.attFindOpenDates){ window.attFindOpenDates(true); }
    else { renderAttChecklist(); }
        }
        load();
      }
    }

    function attSend(idx){
      if(idx >= batches.length){ attFinish(); return; }
      $('att-feedback').innerHTML='<div class="info-box">Submitting '+allIds.length+
        ' attendance records'+reasonNote+'… batch '+(idx+1)+' of '+batches.length+
        ' ('+agg.ok+' done)</div>';
      fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_mark', {
        method: 'POST',
        headers: {'Content-Type':'application/x-www-form-urlencoded','X-Frappe-CSRF-Token': csrf},
        body: 'emp_ids='+encodeURIComponent(batches[idx].join(','))+
              '&att_date='+encodeURIComponent(attDate)+
              (pickedDates.length ? '&att_dates='+encodeURIComponent(pickedDates.join(','))+'&only_gaps=1' : '')+
              '&reason='+encodeURIComponent(reason)+
              (allowOff ? '&allow_off=1' : '')+
              '&status=Present'
      })
      .then(function(r){return r.json()})
      .then(function(res){
        var d = res.message || {};
        agg.ok       += (d.ok_count || 0);
        agg.err      += Math.max(0, (d.err_count || 0) - (d.off_skipped || 0));
        agg.replaced += (d.replaced_count || 0);
        agg.offSkipped += (d.off_skipped || 0);
        agg.offMarked  += (d.off_marked || 0);
        (d.results || []).forEach(function(r){
          if(r.ok) agg.okIds.push(r.employee);
          else if(!r.skipped_off) agg.failures.push(r);
        });
        attSend(idx+1);
      })
      .catch(function(e){
        agg.err += batches[idx].length;
        agg.failures.push({employee:'batch '+(idx+1), error:e.message});
        attSend(idx+1);
      });
    }

    attSend(0);
  });


  // ── SHIFT CHANGE SECTION ──
  function populateShiftFarmDropdown(farms){
    var sel = $('shift-farm');
    farms.forEach(function(v){
      if(!v)return;
      var o=document.createElement('option');
      o.value=v;o.textContent=v;sel.appendChild(o);
    });
  }

  function loadShiftTypes(){
    fetch('/api/resource/Shift Type?fields=["name"]&limit=50&order_by=name asc')
      .then(function(r){return r.json()})
      .then(function(res){
        var shifts=(res.data||[]);
        var sel=$('shift-new');
        sel.innerHTML='<option value="">— select shift —</option>';
        shifts.forEach(function(s){var o=document.createElement('option');o.value=s.name;o.textContent=s.name;sel.appendChild(o)});
      }).catch(function(){$('shift-new').innerHTML='<option value="">Failed to load shifts</option>'});
  }
  // shift types are only needed in the Actions tab — defer so the first three
  // critical fetches (register/leave/dashboard) get the connection pool first.
  setTimeout(loadShiftTypes, 1200);

  // ── Company-wide active headcount, grouped by farm (fetched once) ──
  function loadActiveCounts(){
    var url='/api/method/frappe.desk.reportview.get?'+
      'doctype=Employee'+
      '&fields='+encodeURIComponent('["count(name) as cnt","custom_farm"]')+
      '&filters='+encodeURIComponent('[["Employee","status","=","Active"]]')+
      '&group_by='+encodeURIComponent('custom_farm')+
      '&order_by='+encodeURIComponent('custom_farm asc')+
      '&page_length=0';
    fetch(url, {headers:{'X-Frappe-CSRF-Token': (function(){try{return frappe.csrf_token||''}catch(e){return ''}})()}})
      .then(function(r){return r.json()})
      .then(function(res){
        var msg=res.message||{};
        var keys=msg.keys||[];
        var vals=msg.values||msg.result||[];
        var ci=keys.indexOf('cnt'), fi=keys.indexOf('custom_farm');
        grandActive=0; farmActive={};
        vals.forEach(function(row){
          var cnt, farmName;
          if(ci!==-1 && fi!==-1){ cnt=Number(row[ci])||0; farmName=row[fi]; }
          else if(Array.isArray(row)){ cnt=Number(row[0])||0; farmName=row[1]; }
          else { cnt=Number(row.cnt)||0; farmName=row.custom_farm; }
          farmName=(farmName==null||farmName==='')?'(Unassigned)':farmName;
          farmActive[farmName]=cnt;
          grandActive+=cnt;
        });
        activeLoaded=true;
        if(registerData && registerData.total!=null) renderKPI(lastCards||{cards:{}}, registerData);
        // re-render the daily bars now that a real expected headcount exists
        if(lastDaily.length) renderDailyBars(lastDaily);
      })
      .catch(function(){ /* silent */ });
  }
  // company headcount feeds one KPI tile + daily-bar expected line; it re-renders
  // both when it lands, so a short defer keeps first paint snappy.
  setTimeout(loadActiveCounts, 400);

  function loadShiftEmployees(){
    var farm=$('shift-farm').value;
    var el=$('shift-checklist');
    el.innerHTML='<div class="empty">Loading employees\u2026</div>';
    var qs=new URLSearchParams({farm:farm,company:$('r-company').value}).toString();
    fetch('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_employee_list?'+qs)
      .then(function(r){return r.json()})
      .then(function(res){
        window._shiftEmps=(res.message&&res.message.employees)||[];
        populateShiftFilters(window._shiftEmps);
        renderShiftList();
      })
      .catch(function(e){el.innerHTML='<div class="alert">Load failed: '+esc(e.message)+'</div>'});
  }
  function populateShiftFilters(emps){
    function fill(id, vals, keepFirst){
      var s=$(id); if(!s) return; var cur=s.value;
      var head=[]; for(var i=0;i<keepFirst;i++) head.push(s.options[i].outerHTML);
      s.innerHTML=head.join('')+vals.map(function(v){return '<option value="'+esc(v)+'">'+esc(v)+'</option>'}).join('');
      s.value=cur; if(s.selectedIndex<0) s.selectedIndex=0;
    }
    function uniq(key){ var m={}; emps.forEach(function(r){ var v=r[key]; if(v) m[v]=1; }); return Object.keys(m).sort(); }
    fill('shift-f-et', uniq('employment_type'), 1);
    fill('shift-f-des', uniq('designation'), 1);
    fill('shift-f-shift', uniq('default_shift'), 2);
  }
  window._shiftSort={col:null,dir:1};
  function renderShiftList(){
    var el=$('shift-checklist'); if(!el) return;
    var emps=window._shiftEmps||[];
    var fe=($('shift-f-et')||{}).value||'', fd=($('shift-f-des')||{}).value||'', fs=($('shift-f-shift')||{}).value||'';
    var list=emps.filter(function(r){
      if(fe && (r.employment_type||'')!==fe) return false;
      if(fd && (r.designation||'')!==fd) return false;
      if(fs==='__none__'){ if(r.default_shift) return false; }
      else if(fs && (r.default_shift||'')!==fs) return false;
      return true;
    });
    var st=window._shiftSort;
    var keyMap={emp:'employee_name',code:'name',des:'designation',type:'employment_type',shift:'default_shift'};
    list=list.slice().sort(function(a,b){
      if(st.col){
        var ka=String(a[keyMap[st.col]]||'').toLowerCase(), kb=String(b[keyMap[st.col]]||'').toLowerCase();
        if(ka<kb) return -1*st.dir; if(ka>kb) return 1*st.dir;
        return 0;
      }
      // default: employees WITHOUT a shift first, then by name
      var na=a.default_shift?1:0, nb=b.default_shift?1:0;
      if(na!==nb) return na-nb;
      return String(a.employee_name||'').toLowerCase()<String(b.employee_name||'').toLowerCase()?-1:1;
    });
    var countEl=$('shift-row-count');
    if(countEl) countEl.textContent=list.length+(list.length!==emps.length?' / '+emps.length:'')+' rows';
    if(!list.length){ el.innerHTML='<div class="empty">No employees match the filters.</div>'; return; }
    function ar(c){ return st.col===c?(st.dir>0?' \u2191':' \u2193'):''; }
    el.innerHTML='<table class="tbl"><thead><tr>'+
      '<th style="width:36px"><input type="checkbox" id="shift-chk-all"></th>'+
      '<th class="s" data-sc="emp">Employee'+ar('emp')+'</th><th class="s" data-sc="code">Code'+ar('code')+'</th>'+
      '<th class="s" data-sc="des">Designation'+ar('des')+'</th><th class="s" data-sc="type">Type'+ar('type')+'</th>'+
      '<th class="s" data-sc="shift">Current Shift'+ar('shift')+'</th>'+
    '</tr></thead><tbody>'+
    list.map(function(r){
      return '<tr>'+
        '<td><input type="checkbox" class="shift-chk" data-id="'+esc(r.name)+'" data-name="'+esc(r.employee_name)+'"></td>'+
        '<td class="t-name">'+esc(r.employee_name||'\u2014')+'</td>'+
        '<td class="t-code">'+esc(r.name||'')+'</td>'+
        '<td>'+esc(r.designation||'\u2014')+'</td>'+
        '<td><span style="font-family:var(--f-ui);font-size:12px;color:var(--text-3)">'+esc(r.employment_type||'\u2014')+'</span></td>'+
        '<td>'+(r.default_shift
          ?'<span class="pill pill-farm">'+esc(r.default_shift)+'</span>'
          :'<span style="color:var(--text-3);font-size:12px">\u2014 none \u2014</span>'
        )+'</td>'+
      '</tr>';
    }).join('')+'</tbody></table>';
    var thead=el.querySelector('thead');
    if(thead) thead.addEventListener('click',function(e){
      var th=(e.target&&e.target.closest)?e.target.closest('th.s'):null; if(!th) return;
      var c=th.getAttribute('data-sc');
      st.dir=(st.col===c)?-st.dir:1; st.col=c;
      renderShiftList();
    });
    var chkAll=document.getElementById('shift-chk-all');
    if(chkAll) chkAll.addEventListener('change',function(){
      document.querySelectorAll('.shift-chk').forEach(function(c){c.checked=chkAll.checked});
    });
  }

  ['shift-f-et','shift-f-des','shift-f-shift'].forEach(function(id){ var s=$(id); if(s) s.addEventListener('change', renderShiftList); });
  var sfSel=$('shift-farm'); if(sfSel) sfSel.addEventListener('change', loadShiftEmployees);
  $('shift-select-all').addEventListener('click',function(){
    document.querySelectorAll('.shift-chk').forEach(function(c){c.checked=true});
  });
  $('shift-clear').addEventListener('click',function(){
    document.querySelectorAll('.shift-chk').forEach(function(c){c.checked=false});
  });

  // Hook: after options load, populate shift farm dropdown too
  var _origPopulateOptions=populateOptions;
  populateOptions=function(farms,companies){
    _origPopulateOptions(farms,companies);
    populateShiftFarmDropdown(farms);
  };

  // ══════════════════════════════════════════════════════════════════════════
  // LOST HOURS — per-employee working time lost over a rolling window.
  // Server side is the register's lost=1 mode (piggybacked because new
  // api_methods return HTTP 417 on Frappe Cloud). Every sidebar/topbar filter
  // applies, exactly like the tiles and the trend.
  // ══════════════════════════════════════════════════════════════════════════
  var LOST_ROWS=[], LOST_META=null, LOST_VIEW='all', LOST_DAYS=7;
  var lostSortKey='lost_mins', lostSortDir='desc', _lostSeq=0, _lostTmr=null;

  function lostCols(){
    return [
      {k:'employee',      t:'Employee ID', cls:'t-code'},
      {k:'employee_name', t:'Employee Name'},
      {k:'farm',          t:'Farm'},
      {k:'sched_days',    t:'Rostered',    num:1},
      {k:'loss_days',     t:'Days Losing', num:1},
      {k:'late_mins',     t:'Late In',     num:1},
      {k:'early_mins',    t:'Early Out',   num:1},
      {k:'absent_mins',   t:'Absent',      num:1},
      {k:'no_out_days',   t:'No Checkout', num:1},
      {k:'lost_mins',     t:'Total Lost',  num:1},
      {k:'flag',          t:'Pattern'}
    ];
  }

  function lostFiltered(){
    var q=(($('lost-search')||{}).value||'').trim().toLowerCase();
    var out=[];
    for(var i=0;i<LOST_ROWS.length;i++){
      var r=LOST_ROWS[i];
      if(LOST_VIEW==='freq' && !r.frequent) continue;
      if(LOST_VIEW==='beh'  && !(r.behaviour_mins>0)) continue;
      if(LOST_VIEW==='abs'  && !(r.absent_mins>0)) continue;
      if(LOST_VIEW==='nos'  && !r.never_scanned) continue;
      if(q){
        var hay=(r.employee+' '+(r.employee_name||'')+' '+(r.farm||'')+' '+(r.designation||'')).toLowerCase();
        if(hay.indexOf(q)<0) continue;
      }
      out.push(r);
    }
    var dir=lostSortDir==='asc'?1:-1;
    out.sort(function(a,b){
      var x=a[lostSortKey], y=b[lostSortKey];
      if(typeof x==='string'||typeof y==='string'){
        x=(x||'').toString().toLowerCase(); y=(y||'').toString().toLowerCase();
        return x<y?-dir:(x>y?dir:0);
      }
      x=Number(x)||0; y=Number(y)||0;
      if(x===y) return (Number(b.lost_mins)||0)-(Number(a.lost_mins)||0);
      return (x-y)*dir;
    });
    return out;
  }

  // Empty-state message for the lost-hours table, shown beside the table rather
  // than inside it so it can centre across the full card.
  function lostShowEmpty(msg){
    var wrap=$('lost-wrap'); if(!wrap) return;
    var box=wrap.querySelector('.lost-empty');
    if(!msg){ if(box) box.remove(); return; }
    if(!box){
      box=document.createElement('div');
      box.className='lost-empty';
      wrap.appendChild(box);
    }
    box.textContent=msg;
  }

  function lostRenderTable(){
    var thead=$('lost-thead'), tbody=$('lost-tbody');
    if(!thead||!tbody) return;
    var cols=lostCols();
    var h='<tr>';
    for(var c=0;c<cols.length;c++){
      var col=cols[c];
      var arrow=(lostSortKey===col.k)?(lostSortDir==='asc'?' ▲':' ▼'):'';
      h+='<th class="sortable'+(col.num?' num':'')+'" data-lsort="'+col.k+'">'+col.t+arrow+'</th>';
    }
    thead.innerHTML=h+'</tr>';

    var rows=lostFiltered();
    var cnt=$('lost-count');
    if(cnt) cnt.textContent=rows.length+' employee'+(rows.length===1?'':'s');
    if(!rows.length){
      // Rendered outside the table on purpose. A single <td colspan> is also a
      // first child, and .tile-inline-tbl pins td:first-child to 44px for the
      // row-number column — which squeezed this sentence into a narrow box on
      // the left. A sibling div has no column to be trapped in.
      tbody.innerHTML='';
      lostShowEmpty('No lost time recorded for this selection.');
      return;
    }
    lostShowEmpty(null);
    // shade the total-lost cell relative to the worst offender on screen
    var worst=0;
    for(var w=0;w<rows.length;w++) worst=Math.max(worst, Number(rows[w].lost_mins)||0);
    var b=[];
    for(var i=0;i<rows.length;i++){
      var r=rows[i];
      var flag=r.never_scanned
        ? '<span class="pill st-nocheckout">NO SCANS</span>'
        : (r.frequent ? '<span class="pill st-absent">FREQUENT</span>'
                      : '<span class="pill st-present">OCCASIONAL</span>');
      var share=worst?Math.round((Number(r.lost_mins)||0)/worst*100):0;
      b.push('<tr class="'+(r.frequent?'lost-hot':'')+(r.never_scanned?' lost-gap':'')+'"'+
             ' data-emp="'+r.employee+'" data-nm="'+(r.employee_name||'').replace(/"/g,'&quot;')+'">'+
        '<td class="t-code">'+r.employee+'</td>'+
        '<td>'+(r.employee_name||'')+'</td>'+
        '<td>'+(r.farm||'—')+'</td>'+
        '<td class="num">'+(r.sched_days||0)+'</td>'+
        '<td class="num"><b>'+(r.loss_days||0)+'</b><span class="lost-of">/'+(r.sched_days||0)+'</span></td>'+
        '<td class="num">'+lostCell(r.late_days, r.late_mins)+'</td>'+
        '<td class="num">'+lostCell(r.early_days, r.early_mins)+'</td>'+
        '<td class="num">'+lostCell(r.absent_days, r.absent_mins)+'</td>'+
        '<td class="num">'+(r.no_out_days?('<span class="lost-d">'+r.no_out_days+'d</span>'):'—')+'</td>'+
        '<td class="num lost-total"><span class="lost-bar" style="width:'+share+'%"></span>'+
            '<b>'+fmtMins(r.lost_mins)+'</b></td>'+
        '<td>'+flag+'</td>'+
      '</tr>');
    }
    tbody.innerHTML=b.join('');
    try{ _fitTables(); }catch(e){}
  }
  function lostCell(days, mins){
    if(!mins) return '—';
    return '<b>'+fmtMins(mins)+'</b><span class="lost-of"> · '+(days||0)+'d</span>';
  }

  function lostSummary(){
    var st=$('lost-status'); if(!st) return;
    if(!LOST_META){ st.innerHTML=''; return; }
    var m=LOST_META;
    // Same card system as the Overview KPIs: one modifier class per tile
    // carrying its --tc colour, which drives both the border and the number.
    function tile(mod, label, value, dim){
      return '<span class="lost-sum-i lost-sum-i--'+mod+(dim?' is-zero':'')+'">'
           + '<small>'+label+'</small><b>'+value+'</b></span>';
    }
    st.innerHTML='<div class="lost-sum">'+
      tile('window',   'WINDOW',             m.from+' → '+m.to)+
      tile('total',    'TOTAL LOST',         fmtMins(m.total_lost_mins),   !(m.total_lost_mins||0))+
      tile('behaviour','LATE / EARLY',       fmtMins(m.behaviour_mins),    !(m.behaviour_mins||0))+
      tile('affected', 'EMPLOYEES AFFECTED', (m.employees_affected||0),    !(m.employees_affected||0))+
      tile('frequent', 'FREQUENT LOSERS',    (m.frequent_count||0),        !(m.frequent_count||0))+
      tile('noscan',   'NO SCANS AT ALL',    (m.never_scanned_count||0),   !(m.never_scanned_count||0))+
    '</div>';
  }

  // The window the endpoint is asked for. Derived from the pickers when they
  // hold a valid range, otherwise from the active preset.
  var LOST_MAX_DAYS=31;             // the endpoint caps the window here
  function lostWindow(){
    var to=(($('lost-to')||{}).value||'')||(window._selDate||todayISO());
    var from=(($('lost-from')||{}).value||'');
    if(!from) return {to:to, days:LOST_DAYS};
    var span=Math.round((fromISO(to)-fromISO(from))/86400000)+1;
    if(!isFinite(span)||span<1) span=1;
    return {to:to, days:Math.min(span, LOST_MAX_DAYS)};
  }
  function fromISO(v){var p=String(v||'').split('-');return new Date(+p[0],(+p[1]||1)-1,+p[2]||1)}

  // Push the active preset back into the pickers, so they always describe the
  // window actually being shown.
  function lostSyncDates(){
    var to=(window._selDate||todayISO());
    var from=isoDate(new Date(fromISO(to).getTime()-(LOST_DAYS-1)*86400000));
    var hf=$('lost-from'), ht=$('lost-to');
    if(hf){ hf.value=from; if(hf._setDate) hf._setDate(from); }
    if(ht){ ht.value=to;   if(ht._setDate) ht._setDate(to); }
  }

  function lostInitDates(){
    var to=(window._selDate||todayISO());
    var from=isoDate(new Date(fromISO(to).getTime()-(LOST_DAYS-1)*86400000));
    makeFrappeDate('lost-from-ctl','lost-from',from);
    makeFrappeDate('lost-to-ctl','lost-to',to);
    ['lost-from','lost-to'].forEach(function(id){
      var el=$(id); if(!el) return;
      // The desk control writes the hidden input on change; watch it directly so
      // this works whichever way the value arrives.
      var last=el.value;
      setInterval(function(){
        if(el.value===last) return;
        last=el.value;
        var w=lostWindow();
        LOST_DAYS=w.days;
        var rgx=$('lost-ranges');
        if(rgx) rgx.querySelectorAll('.ti-tab').forEach(function(x){
          x.classList.toggle('active', parseInt(x.getAttribute('data-ld'),10)===w.days);
        });
        LOST_ROWS=[]; scheduleLost();
      }, 400);
    });
  }

  // ── Lost-hours day breakdown ─────────────────────────────────────────
  // One row per day that cost time in the window being shown, so the aggregate
  // in the table ("94h 30m · 7d") can be taken apart.
  function openLostBreakdown(empId, empName){
    var ov=$('lost-bd'); if(!ov) return;
    var w=lostWindow();
    $('lost-bd-name').textContent=empName||empId;
    $('lost-bd-sub').textContent=empId+' · '+(LOST_META?LOST_META.from:'')+' → '+(LOST_META?LOST_META.to:'');
    $('lost-bd-body').innerHTML='<div class="empty" style="padding:40px">Loading days…</div>';
    ov.classList.add('open');

    var qs=new URLSearchParams({lost:'1', days:String(w.days), date:(w.to||''),
      lost_emp:empId,
      farm:(($('r-farm')||{}).value||''),
      company:(($('r-company')||{}).value||''),
      employment_type:(($('r-emptype')||{}).value||'')}).toString();

    fetchJSON('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_register?'+qs, 2)
      .then(function(res){
        var d=(res&&res.message)||{};
        var days=d.lost_days||[];
        if(!days.length){
          $('lost-bd-body').innerHTML='<div class="empty" style="padding:40px">'
            +'No day in this window cost time for this employee.</div>';
          return;
        }
        var total=0;
        var body=days.map(function(r){
          total+=(r.lost_mins||0);
          return '<tr>'
            +'<td>'+esc(r.date||'')+'</td>'
            +'<td>'+esc(r.shift||'—')+'</td>'
            +'<td>'+esc(r.reason||'')+'</td>'
            +'<td class="num">'+(r.late_mins?fmtMins(r.late_mins):'—')+'</td>'
            +'<td class="num">'+(r.early_mins?fmtMins(r.early_mins):'—')+'</td>'
            +'<td class="num">'+(r.absent_mins?fmtMins(r.absent_mins):'—')+'</td>'
            +'<td class="num"><b>'+fmtMins(r.lost_mins||0)+'</b></td></tr>';
        }).join('');
        $('lost-bd-body').innerHTML=
          '<div class="lost-bd-sum"><span>'+days.length+(days.length===1?' day':' days')
          +' losing time</span><span>'+fmtMins(total)+'</span></div>'
          +'<div class="tile-inline-wrap"><table class="tile-inline-tbl"><thead><tr>'
          +'<th>Date</th><th>Shift</th><th>Why</th><th class="num">Late In</th>'
          +'<th class="num">Early Out</th><th class="num">Absent</th><th class="num">Lost</th>'
          +'</tr></thead><tbody>'+body+'</tbody></table></div>';
      })
      .catch(function(e){
        $('lost-bd-body').innerHTML='<div class="alert">Could not load the breakdown: '
          +(e&&e.message?e.message:e)+'</div>';
      });
  }
  (function(){
    var ov=$('lost-bd'); if(!ov) return;
    var close=function(){ ov.classList.remove('open'); };
    var btn=$('lost-bd-close'); if(btn) btn.addEventListener('click', close);
    ov.addEventListener('click', function(e){ if(e.target===ov) close(); });
    document.addEventListener('keydown', function(e){
      if(e.key==='Escape' && ov.classList.contains('open')) close();
    });
  })();

  function loadLost(){
    var seq=++_lostSeq;
    var card=$('lost-card'); if(card) card.classList.add('att-loading');
    var st=$('lost-status');
    if(st && !LOST_ROWS.length) st.innerHTML='<div class="empty" style="padding:22px">Measuring lost time over the last '+LOST_DAYS+' days…</div>';
    var lw=lostWindow();
    var qs=new URLSearchParams({lost:'1', days:String(lw.days),
      date:(lw.to||window._selDate||''),
      farm:(($('r-farm')||{}).value||''),
      company:(($('r-company')||{}).value||''),
      employment_type:(($('r-emptype')||{}).value||'')}).toString();
    fetchJSON('/api/method/upande_ta.upande_ta.api.attendance_insights.attendance_register?'+qs, 2).then(function(res){
      if(seq!==_lostSeq) return;
      if(card) card.classList.remove('att-loading');
      var d=(res&&res.message)||{};
      if(d.error){ if(st) st.innerHTML='<div class="alert">'+d.error+'</div>'; return; }
      LOST_ROWS=d.lost||[]; LOST_META=d;
      lostSummary(); lostRenderTable();
    }).catch(function(e){
      if(seq!==_lostSeq) return;
      if(card) card.classList.remove('att-loading');
      if(st) st.innerHTML='<div class="alert">Could not load lost hours: '+(e&&e.message?e.message:e)+'</div>';
    });
  }
  function scheduleLost(){
    if(_lostTmr) clearTimeout(_lostTmr);
    _lostTmr=setTimeout(function(){ _lostTmr=null; loadLost(); }, 140);
  }
  // reload only while the page is actually open (filters fire on every view)
  window._reloadLost=function(){
    var p=$('panel-lost');
    if(p && p.classList.contains('active')) scheduleLost();
  };

  (function(){
    var btn=$('sb-lost');
    if(btn) btn.addEventListener('click', function(){
      setTimeout(function(){
        var p=$('panel-lost');
        if(p && p.classList.contains('active')) scheduleLost();
      }, 0);
    });
    var rg=$('lost-ranges');
    if(rg) rg.addEventListener('click', function(e){
      var b=(e.target&&e.target.closest)?e.target.closest('.ti-tab'):null; if(!b) return;
      rg.querySelectorAll('.ti-tab').forEach(function(x){ x.classList.remove('active'); });
      b.classList.add('active');
      LOST_DAYS=parseInt(b.getAttribute('data-ld'),10)||7;
      lostSyncDates();              // keep the pickers showing the window
      LOST_ROWS=[]; scheduleLost();
    });

    // ── date filter ──────────────────────────────────────────────────────
    // The endpoint takes an end date plus a day count, so a from/to pair maps
    // onto it directly: date = To, days = the span. The presets stay — they
    // just move the pickers now, so the two can never disagree about which
    // window is on screen.
    lostInitDates();
    var vw=$('lost-views');
    if(vw) vw.addEventListener('click', function(e){
      var b=(e.target&&e.target.closest)?e.target.closest('.ti-tab'):null; if(!b) return;
      vw.querySelectorAll('.ti-tab').forEach(function(x){ x.classList.remove('active'); });
      b.classList.add('active');
      LOST_VIEW=b.getAttribute('data-lv')||'all';
      lostRenderTable();
    });
    var sr=$('lost-search');
    if(sr) sr.addEventListener('input', function(){ lostRenderTable(); });
    var th=$('lost-thead');
    if(th) th.addEventListener('click', function(e){
      var t=(e.target&&e.target.closest)?e.target.closest('th[data-lsort]'):null; if(!t) return;
      var k=t.getAttribute('data-lsort');
      if(lostSortKey===k) lostSortDir=(lostSortDir==='asc')?'desc':'asc';
      else { lostSortKey=k; lostSortDir=(k==='employee'||k==='employee_name'||k==='farm'||k==='flag')?'asc':'desc'; }
      lostRenderTable();
    });
    var tb=$('lost-tbody');
    if(tb) tb.addEventListener('click', function(e){
      var tr=(e.target&&e.target.closest)?e.target.closest('tr[data-emp]'):null; if(!tr) return;
      // The generic drawer answers "what did this person do" over a range. From
      // Lost Hours the question is narrower — WHICH days in this window cost
      // time, and how much — so it opens the breakdown instead.
      openLostBreakdown(tr.getAttribute('data-emp'), tr.getAttribute('data-nm'));
    });
    var xl=$('lost-xls');
    if(xl) xl.addEventListener('click', function(){
      var rows=lostFiltered(), out=[];
      for(var i=0;i<rows.length;i++){
        var r=rows[i];
        out.push({'Employee ID':r.employee,'Employee Name':r.employee_name,'Farm':r.farm,
          'Company':r.company,'Employment Type':r.employment_type,'Designation':r.designation,
          'Days Rostered':r.sched_days,'Days Losing Time':r.loss_days,
          'Late Days':r.late_days,'Late Minutes':r.late_mins,
          'Early Out Days':r.early_days,'Early Out Minutes':r.early_mins,
          'Absent Days':r.absent_days,'Absent Minutes':r.absent_mins,
          'No Checkout Days':r.no_out_days,
          'Total Lost Minutes':r.lost_mins,'Total Lost':fmtMins(r.lost_mins),
          'Pattern':r.never_scanned?'No scans':(r.frequent?'Frequent':'Occasional'),
          'Worst Day':r.worst_day});
      }
      var wn=(LOST_META?(LOST_META.from+'_'+LOST_META.to):('last'+LOST_DAYS+'d'));
      dlXLSX(out,'Lost Hours','lost-hours-'+wn+'.xlsx');
    });
  })();

  // Move the KPI strip OUT of the zoomed `.body` so position:fixed is viewport-relative
// (zoom on an ancestor traps fixed positioning in modern Chrome).
(function(){ try{ var kp=document.getElementById('r-kpi'), tb=document.querySelector('.topbar'); if(kp&&tb&&tb.parentNode && kp.parentNode!==tb.parentNode){ tb.parentNode.insertBefore(kp, tb.nextSibling); } }catch(e){} })();
// Measure the topbar's real geometry and (a) align every tab's flow content to the
// topbar's content-left, (b) pin the tiles as a fixed header exactly over that span.
// Size every visible scrollable table so its CARD's bottom edge lands level with the
// sidebar bottom (viewport-16). Runs twice: the first pass changes layout, the second
// corrects the residual — so it is exact regardless of when it is called.
function _fitTables(){
  if(window.innerWidth<=900) return;
  // .tile-inline-wrap is sized by the flex layout (CSS), not here
  var sels=['.tbl-wrap','.reg-sub-tbl-wrap','.exc-tbl-wrap','.split-tbl-wrap'];
  function pass(){
    for(var s=0;s<sels.length;s++){
      var els=document.querySelectorAll(sels[s]);
      for(var i=0;i<els.length;i++){
        var el=els[i]; if(el.offsetParent===null) continue;
        var card=el.closest?el.closest('.card'):null;
        var target=window.innerHeight-16;
        if(card){
          var cd=card.getBoundingClientRect();
          var delta=target-cd.bottom;                 // >0 card too short, <0 too tall
          var cur=el.getBoundingClientRect().height;
          el.style.maxHeight=Math.max(160, Math.round(cur+delta))+'px';
        } else {
          var wr=el.getBoundingClientRect();
          el.style.maxHeight=Math.max(160, Math.round(target-wr.top))+'px';
        }
        el.style.overflow='auto';
      }
    }
  }
  pass(); pass();
}
function _fitTopbar(){ try{
  var tb=document.querySelector('.topbar'), b=document.querySelector('.body'), kp=document.getElementById('r-kpi');
  if(!tb||!b) return;
  if(window.innerWidth<=900){ if(kp){kp.style.position='';kp.style.left='';kp.style.width='';kp.style.top='';kp.style.right='';kp.style.display='';} b.style.paddingLeft='';b.style.paddingRight=''; b.style.paddingTop=(tb.offsetHeight+8)+'px'; document.documentElement.style.setProperty('--topbar-h', tb.offsetHeight+'px'); return; }
  var r=tb.getBoundingClientRect();
  var root=document.documentElement.style;
  root.setProperty('--topbar-h', r.height+'px');
  root.setProperty('--hdr-top', Math.round(r.bottom)+'px');
  // Tiles use a static calc (left:calc(sb+32)/right:16) that matches the topbar exactly.
  // The BODY sits inside Frappe wrappers with a residual containing-block offset, so we
  // align it INCREMENTALLY to the tiles' (or topbar's) actual rendered left — measurement
  // based, so it converges regardless of any wrapper/container offset.
  var ov=document.getElementById('panel-overview'), ovOn=ov&&ov.classList.contains('active');
  // tiles only belong to the attendance (overview) view; class-based so it beats !important
  document.body.classList.toggle('no-tiles', !ovOn);
  if(window.requestAnimationFrame){ requestAnimationFrame(function(){ try{
    // Topbar + tiles are pinned at calc(sidebar+32) (a tight 16px from the sidebar).
    // Compensate the BODY's margin so it renders at that SAME left, cancelling any wrapper
    // offset — one-shot deterministic (no oscillation): newMargin = target - bodyLeft + curMargin.
    var tgt=tb.getBoundingClientRect().left;
    var curML=parseFloat(getComputedStyle(b).marginLeft)||0;
    // single source of truth for the content's left edge: the topbar's own left
    root.setProperty('--content-left', Math.round(tgt)+'px');
    root.setProperty('--body-ml', '0px');
    // Layout is now PURE CSS (topbar, tiles and body share identical fixed left/right
    // anchors). Clear any inline width/margin overrides left by earlier JS so they
    // can never fight the stylesheet again.
    (function(){ try{
      ['margin-left','margin-right','width','max-width'].forEach(function(p){ b.style.removeProperty(p); });
      var cards=document.querySelectorAll('#panel-overview>.card, #panel-actions>.card, #panel-register>.card, #panel-overview>.row, #panel-actions>.act-bar');
      for(var c=0;c<cards.length;c++){ cards[c].style.removeProperty('margin-left'); cards[c].style.removeProperty('margin-right'); }
    }catch(e){} })();
    // content begins just below the tiles' MEASURED bottom → tight, correct gap every time
    // a hidden tiles strip (e.g. trend-mode) measures 0 — only use it when actually visible.
    // NOTE: offsetParent is ALWAYS null for position:fixed elements, so test display+height.
    var kpVisible=kp && ovOn && getComputedStyle(kp).display!=='none' && kp.getBoundingClientRect().height>0;
    var contentTop=kpVisible ? kp.getBoundingClientRect().bottom : tb.getBoundingClientRect().bottom;
    b.style.paddingTop=Math.round(contentTop+3)+'px';
    // overview charts fill down to the sidebar's bottom level (viewport - 16px)
    var cr=document.getElementById('r-charts-row');
    if(cr && ovOn && cr.offsetParent!==null){
      // Set the CARD height to fill to the sidebar bottom; the chart wrapper flexes to fill
      // whatever remains inside the card (header takes its own space). Card bottom = viewport-16.
      var card=cr.querySelector('.card'), wrap=cr.querySelector('.ch-wrap'), cbody=cr.querySelector('.card-body');
      if(card && wrap){
        var cd=card.getBoundingClientRect();
        var head=Math.max(0, wrap.getBoundingClientRect().top - cd.top);   // everything above the wrapper inside the card
        var ccs=getComputedStyle(card), bcs=cbody?getComputedStyle(cbody):null;
        var below=(parseFloat(ccs.borderBottomWidth)||0)+(bcs?(parseFloat(bcs.paddingBottom)||0):0)+(parseFloat(ccs.borderTopWidth)||0);
        var ch=Math.max(180, Math.round((window.innerHeight - 16) - cd.top - head - below));
        root.setProperty('--charts-h', ch+'px');
        // force Chart.js to re-fit its canvas to the explicit height (else it overflows/clips)
        requestAnimationFrame(function(){ try{ if(typeof charts==='object' && charts){
          if(charts.daily && charts.daily.resize) charts.daily.resize();
          if(charts.hour && charts.hour.resize) charts.hour.resize();
        } }catch(e){} });
      }
    }
    // no global scroll: every visible scrollable table wrap fills to the sidebar bottom and scrolls internally
    _fitTables();
  }catch(e){} }); }
  if(false){ requestAnimationFrame(function(){ try{
    var tgt=tb.getBoundingClientRect().left; var hl=0, bml=0;
    if(kp && getComputedStyle(kp).display!=='none'){ var dk=tgt-kp.getBoundingClientRect().left; if(Math.abs(dk)>0.5) root.setProperty('--hdr-left', Math.round(hl+dk)+'px'); }
    var db=tgt-b.getBoundingClientRect().left; if(Math.abs(db)>0.5) root.setProperty('--body-ml', Math.round(bml+db)+'px');
  }catch(e){} }); }
}catch(e){} }
  window.addEventListener('resize',_fitTopbar); _fitTopbar(); setTimeout(_fitTopbar,250); setTimeout(_fitTopbar,900); setTimeout(_fitTopbar,1800);
  try{ if(window.ResizeObserver){ var _hro=new ResizeObserver(function(){_fitTopbar()}); var _hrb=document.querySelector('.topbar'); if(_hrb)_hro.observe(_hrb); } }catch(e){}
  load();
  scheduleAutoRefresh();

})();



(function () {
  function cookie(n){ var m=document.cookie.match("(^|;)\\s*"+n+"\\s*=\\s*([^;]+)"); return m?decodeURIComponent(m[2]):""; }
  var btn=document.getElementById("wm-avatar");
  var menu=document.getElementById("wm-account-menu");
  if(!btn||!menu) return;
  var user=cookie("user_id")||"Guest";
  var idbox=document.getElementById("wm-account-id");
  var login=document.getElementById("wm-account-login");
  var logout=document.getElementById("wm-account-logout");
  var me=document.getElementById("wm-account-me");
  if(user==="Guest"||!user){
    if(idbox) idbox.parentElement.firstElementChild.textContent="Not signed in";
    if(idbox) idbox.textContent="Log in to work here";
    var _ini=document.getElementById("wm-ini"),_uid=document.getElementById("wm-uid"); if(_ini)_ini.textContent="\u2192"; if(_uid)_uid.textContent="Not signed in";
    if(login){ login.style.display=""; login.href="/login?redirect-to="+encodeURIComponent(location.pathname); }
    if(logout) logout.style.display="none";
    if(me) me.style.display="none";
  } else {
    if(idbox) idbox.textContent=user;
    var _ini=document.getElementById("wm-ini"),_uid=document.getElementById("wm-uid"); if(_ini)_ini.textContent=user.replace(/@.*$/,"").split(/[._ -]/).filter(Boolean).slice(0,2).map(function(p){return p[0].toUpperCase();}).join(""); if(_uid)_uid.textContent=user;
  }
  function close(){ menu.classList.remove("on"); btn.setAttribute("aria-expanded","false"); }
  btn.addEventListener("click", function(e){
    e.stopPropagation();
    var on=!menu.classList.contains("on");
    menu.classList.toggle("on", on);
    btn.setAttribute("aria-expanded", on?"true":"false");
  });
  document.addEventListener("click", function(e){ if(!menu.contains(e.target)) close(); });
  document.addEventListener("keydown", function(e){ if(e.key==="Escape") close(); });
})();
