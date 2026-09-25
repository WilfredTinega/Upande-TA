(function(){
  var API='/api/method/upande_ta.upande_ta.api.canteen_analysis.';
  var $=function(id){return document.getElementById(id)};
  var MO=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var WD=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var MEALS=['Breakfast','Lunch','Supper'];
  var filters={companies:[],today:null,meals:MEALS};
  var fc=null, ml=null;            // forecast + meals payloads
  var view='forecast', xtab='off_day', repeatOnly=false, multiOnly=false, seq=0, selDate=null;
  var charts={}, drEmp=null, drSeq=0, drData=null;

  function pad(n){return String(n).padStart(2,'0')}
  function iso(d){return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())}
  function parse(s){var p=String(s).slice(0,10).split('-');return new Date(+p[0],+p[1]-1,+p[2])}
  function addDays(s,n){var d=parse(s);d.setDate(d.getDate()+n);return iso(d)}
  function label(s){var d=parse(s);return WD[d.getDay()]+' '+d.getDate()+' '+MO[d.getMonth()]}
  function labelY(s){var d=parse(s);return d.getDate()+' '+MO[d.getMonth()]+' '+d.getFullYear()}
  function hms(s){var t=String(s||'').split(' ')[1]||'';return t.slice(0,8)}
  function esc(s){if(s==null)return '';return String(s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function fmt(n){return n==null?'—':Number(n).toLocaleString()}
  function money(n){return n==null?'—':Number(n).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:2})}
  function today(){return filters.today||iso(new Date())}
  function curLbl(){return (ml&&ml.currency)?' ('+ml.currency+')':''}
  function dm(s){var d=parse(s);return d.getDate()+' '+MO[d.getMonth()]}

  function get(method,params){
    var qs=Object.keys(params||{}).filter(function(k){return params[k]!==''&&params[k]!=null}).map(function(k){return encodeURIComponent(k)+'='+encodeURIComponent(params[k])}).join('&');
    return fetch(API+method+(qs?'?'+qs:''),{credentials:'same-origin',headers:{Accept:'application/json'}}).then(function(r){
      return r.json().catch(function(){return {}}).then(function(j){
        if(!r.ok){
          var msg='';
          try{msg=JSON.parse(JSON.parse(j._server_messages||'[]')[0]||'{}').message||''}catch(e){}
          throw new Error(msg||j.exception||('HTTP '+r.status));
        }
        return j.message;
      });
    });
  }

  // ── state ──
  function state(){return {date:selDate||today(),company:$('r-company').value,farm:$('r-farm').value,meal:$('r-meal').value||'Lunch'}}
  function readUrl(){
    var q=new URLSearchParams(location.search);
    return {date:q.get('date')||'',company:q.get('company'),farm:q.get('farm')||'',meal:q.get('meal')||'Lunch',view:q.get('view')||''};
  }
  function writeUrl(){
    var st=state(), q=new URLSearchParams();
    if(st.company)q.set('company',st.company);
    if(st.farm)q.set('farm',st.farm);
    if(st.meal!=='Lunch')q.set('meal',st.meal);
    if(view!=='forecast')q.set('view',view);
    var s=q.toString();
    try{history.replaceState(null,'',location.pathname+(s?'?'+s:''))}catch(e){}
  }

  // ── Frappe date control (same build as attendance-insights.js) ──
  var dateCtl=null, settingDate=false;
  function setDate(v,noLoad){
    selDate=v;
    if(dateCtl){settingDate=true;try{dateCtl.set_value(v)}catch(e){}settingDate=false}
    var ni=$('r-date-native'); if(ni) ni.value=v;
    if(!noLoad) load();
  }
  function initDateCtl(){
    function build(){
      var wrap=$('r-date'); if(!wrap||wrap._built) return; wrap._built=true;
      try{
        var ctl=frappe.ui.form.make_control({df:{fieldtype:'Date',fieldname:'r_date',label:'',placeholder:'Select date'},parent:wrap,render_input:true});
        ctl.set_value(selDate);
        try{ if(ctl.datepicker && ctl.datepicker.update) ctl.datepicker.update({minDate:new Date(2000,0,1),maxDate:new Date(2999,11,31),todayButton:true}); }catch(e){}
        function sync(initial){
          try{
            var v=ctl.get_value()||selDate; if(!v||!ctl.datepicker) return;
            var dt=parse(v);
            if(initial && ctl.datepicker.selectDate) ctl.datepicker.selectDate(dt);
            if(ctl.datepicker.setViewDate) ctl.datepicker.setViewDate(dt);
          }catch(e){}
        }
        sync(true);
        ctl.$input.on('focus click',function(){setTimeout(function(){sync(false)},0)});
        var kill=wrap.querySelectorAll('.control-label,label,.help-box,.clearfix,.control-value');
        for(var i=0;i<kill.length;i++){var k=kill[i]; if(k.querySelector&&k.querySelector('input'))continue; if(k.tagName==='INPUT')continue; k.style.display='none';}
        ctl.$input.on('change',function(){ if(settingDate) return; var v=ctl.get_value(); if(v&&v!==selDate){selDate=v;load()} });
        dateCtl=ctl;
      }catch(e){ if(window.console) console.error('date picker init failed',e); }
      if(!wrap.querySelector('input')){
        var ni=document.createElement('input'); ni.type='date'; ni.id='r-date-native'; ni.value=selDate;
        ni.addEventListener('change',function(){ if(ni.value&&ni.value!==selDate){selDate=ni.value;load()} });
        wrap.appendChild(ni);
      }
    }
    if(window.frappe && frappe.ui && frappe.ui.form && frappe.ui.form.make_control) build();
    else if(window.frappe && frappe.require) frappe.require('controls.bundle.js', build);
    else build();
  }

  // ── company picker + sidebar tree ──
  function syncCompanyLabel(){try{syncTitle()}catch(e){}var sel=$('r-company'),lbl=$('r-company-label');if(lbl)lbl.textContent=sel?(sel.options[sel.selectedIndex]||{}).text||'':''}
  function setSelect(id,val){
    var s=$(id),found=false;
    for(var i=0;i<s.options.length;i++){if(s.options[i].value===val){found=true;break}}
    if(!found){var o=document.createElement('option');o.value=val;o.textContent=val;s.appendChild(o)}
    s.value=val;
    if(id==='r-company')syncCompanyLabel();
  }
  function fillFarms(){
    var co=$('r-company').value, sel=$('r-farm'), keep=sel.value, seen={}, html='<option value="">All Units</option>';
    filters.companies.forEach(function(c){
      if(co&&c.company!==co)return;
      c.farms.forEach(function(f){if(f.farm&&!seen[f.farm]){seen[f.farm]=1;html+='<option>'+esc(f.farm)+'</option>'}});
    });
    sel.innerHTML=html; sel.value=seen[keep]?keep:'';
  }
  function buildSidebar(){
    var body=$('att-sb-body'), co=$('r-company').value, html='';
    var list=filters.companies.filter(function(c){return !co||c.company===co});
    list.forEach(function(c){
      var total=c.farms.reduce(function(a,f){return a+(f.active||0)},0);
      html+='<div class="att-sb-company"><div class="att-sb-co-head" data-co="'+esc(c.company)+'"><span class="att-sb-caret">&#9662;</span><span class="att-sb-co-name">'+esc(c.company)+'</span><span class="att-sb-co-cnt">'+fmt(total)+'</span></div><div class="att-sb-farms">';
      c.farms.forEach(function(f){
        if(!f.farm)return;
        html+='<div class="att-sb-farm" data-co="'+esc(c.company)+'" data-farm="'+esc(f.farm)+'"><span class="att-sb-fname">'+esc(f.farm)+'</span><span class="att-sb-n">'+fmt(f.active)+'</span></div>';
      });
      html+='</div></div>';
    });
    body.innerHTML=html||'<div class="att-sb-empty">No data</div>';
    syncSidebarActive();
  }
  function syncSidebarActive(){
    var body=$('att-sb-body'), co=$('r-company').value, farm=$('r-farm').value;
    Array.prototype.forEach.call(body.querySelectorAll('.active'),function(e){e.classList.remove('active')});
    if(!co) return;
    var el=farm?body.querySelector('.att-sb-farm[data-co="'+CSS.escape(co)+'"][data-farm="'+CSS.escape(farm)+'"]'):body.querySelector('.att-sb-co-head[data-co="'+CSS.escape(co)+'"]');
    if(el)el.classList.add('active');
  }
  function unitLabel(){var f=$('r-farm').value;$('r-unit').textContent=f||''}
  function initFilters(init){
    return get('canteen_filters').then(function(m){
      filters=m||filters; filters.meals=filters.meals||MEALS;
      var cs=$('r-company');
      cs.innerHTML=(filters.companies.length>1?'<option value="">All Companies</option>':'')+filters.companies.map(function(c){return '<option>'+esc(c.company)+'</option>'}).join('');
      var saved=init.company;
      if(saved==null){try{saved=localStorage.getItem('canteen_company')}catch(e){}}
      if(saved&&filters.companies.some(function(c){return c.company===saved}))cs.value=saved;
      syncCompanyLabel();
      fillFarms();
      if(init.farm){setSelect('r-farm',init.farm)}
      $('r-meal').innerHTML=filters.meals.map(function(x){return '<option>'+esc(x)+'</option>'}).join('');
      $('r-meal').value=init.meal||'Lunch';
      $('p-meal').innerHTML='<option value="">All Meals</option>'+filters.meals.map(function(x){return '<option>'+esc(x)+'</option>'}).join('');
      $('e-meal').innerHTML=$('p-meal').innerHTML;
      buildSidebar(); unitLabel();
    });
  }

  // ── load ──
  // ── skeletons: every part shows a placeholder the size of what replaces it,
  // and each part is replaced as soon as its own request returns ──
  var fcLoading=false, mlLoading=false, fcErr=null, mlErr=null;
  var PARTS={
    fc:{bodies:['g-body','d-body','x-body','pe-body'],feet:['g-foot'],charts:['ch-trend','ch-cost','ch-excl','ch-unit']},
    ml:{bodies:['e-body','p-body','c-body','s-body'],feet:['c-foot','s-foot'],charts:['ch-hour','ch-type','ch-pmeals','ch-pcost']}
  };
  function skRows(id,n){
    var tb=$(id); if(!tb)return;
    var cols=tb.closest('table').querySelectorAll('thead th').length||6, h='';
    for(var i=0;i<n;i++){h+='<tr class="sk-row">';for(var c=0;c<cols;c++)h+='<td><span class="sk sk-cell" style="width:'+(45+((i*7+c*13)%45))+'%"></span></td>';h+='</tr>'}
    tb.innerHTML=h;
  }
  function skChart(id){
    var cv=$(id); if(!cv)return;
    if(charts[id]){try{charts[id].destroy()}catch(e){}charts[id]=null}
    var w=cv.parentNode, e=w.querySelector('.chart-empty'); if(e)e.remove();
    cv.style.visibility='hidden'; w.classList.add('sk-chart');
  }
  function skTiles(){
    var k='';
    for(var i=0;i<6;i++)k+='<div class="kpi kpi-sk"><div class="sk sk-lbl"></div><div><div class="sk sk-val"></div><div class="sk sk-sub"></div></div></div>';
    $('kpis').innerHTML=k;
  }
  function skeleton(part){
    var P=PARTS[part];
    if(part==='fc'&&!$('x-head').innerHTML)$('x-head').innerHTML='<tr>'+XCOLS[xtab].map(function(c){return '<th>'+c[1]+'</th>'}).join('')+'</tr>';
    P.bodies.forEach(function(id){skRows(id,8)});
    P.feet.forEach(function(id){$(id).innerHTML=''});
    P.charts.forEach(skChart);
  }
  function failed(part,e){
    var P=PARTS[part], msg=esc(e&&e.message||String(e));
    $('r-status').textContent=e&&e.message||String(e);
    P.bodies.forEach(function(id){var tb=$(id);var cols=tb.closest('table').querySelectorAll('thead th').length||1;tb.innerHTML='<tr><td colspan="'+cols+'" class="empty">'+msg+'</td></tr>'});
    P.charts.forEach(function(id){draw(id,null,false)});
    renderTiles();
  }
  function fcParts(){
    renderGroups(); renderDays(); renderExcluded(); renderPeriodEmployees();
    if(ml&&!mlLoading)renderPeriod();
  }
  function mlParts(){
    renderEmployees(); renderPunches(); renderPeriod();
    if(fc&&!fcLoading)renderPeriodEmployees();
  }
  function load(){
    var st=state(), my=++seq;
    writeUrl(); markQuick(); syncSidebarActive(); unitLabel();
    $('r-status').textContent='';
    fcLoading=mlLoading=true; fcErr=mlErr=null;
    skTiles(); skeleton('fc'); skeleton('ml');
    get('canteen_forecast',{date:st.date,company:st.company,farm:st.farm,meal:st.meal,history_days:14,ahead_days:6}).then(function(d){
      if(my!==seq)return;
      fc=d; fcLoading=false; renderTiles(); fcParts(); renderCharts();
    }).catch(function(e){
      if(my!==seq)return;
      fc=null; fcLoading=false; fcErr=e; failed('fc',e);
    });
    get('canteen_meals',{date:st.date,company:st.company,farm:st.farm}).then(function(d){
      if(my!==seq)return;
      ml=d; mlLoading=false; PAYROLL={}; (ml.punches||[]).forEach(function(p){if(p.payroll_number)PAYROLL[p.employee]=p.payroll_number});
      renderTiles(); mlParts(); renderCharts();
    }).catch(function(e){
      if(my!==seq)return;
      ml=null; mlLoading=false; mlErr=e; failed('ml',e);
    });
  }
  function markQuick(){
    var d=selDate||today();
    Array.prototype.forEach.call(document.querySelectorAll('#r-quick .btn'),function(b){b.classList.toggle('active',addDays(today(),+b.dataset.off)===d)});
  }

  function render(){
    renderTiles();
    if(fc&&!fcLoading)fcParts();
    if(ml&&!mlLoading)mlParts();
    renderCharts();
  }

  // ── tiles (per view) ──
  function tile(cls,lbl,val,sub,attrs){
    return '<div class="kpi kpi--'+cls+(attrs?' click':'')+'"'+(attrs||'')+'><div class="kpi-lbl">'+esc(lbl)+'</div><div><div class="kpi-val">'+val+'</div>'+(sub?'<div class="kpi-sub">'+sub+'</div>':'')+'</div></div>';
  }
  function renderTiles(){
    var need=view==='forecast'?'fc':'ml';
    if(need==='fc'?fcLoading:mlLoading){skTiles();return}
    if(need==='fc'?(fcErr||!fc):(mlErr||!ml)){$('kpis').innerHTML='';return}
    var h='';
    if(view==='forecast'&&fc){
      var s=fc.summary||{}, past=fc.date<=today(), m=fc.meal;
      h+=tile('expected','Expected for '+m,fmt(s.expected),label(fc.date));
      h+=tile('active','Active',fmt(s.active));
      h+=tile('off','Off Day',fmt(s.off_day),'',' data-x="off_day"');
      h+=tile('sick','Sick Off',fmt(s.sick),'',' data-x="sick"');
      h+=tile('leaves','On Leave',fmt(s.leave),'',' data-x="leave"');
      var pct=(past&&s.expected)?Math.round((s.meals||0)*100/s.expected)+'% of expected':'';
      h+=tile('meals',m+' Eaten',past?fmt(s.meals):'—',pct,' data-view="meals"');
    }else if(view==='meals'&&ml){
      var d=ml.day;
      h+=tile('total','Employees Ate',fmt(d.employees),label(ml.date));
      h+=tile('meals','Meals',fmt(d.eaten));
      h+=tile('punch','Punches',fmt(d.punches));
      (d.by_meal||[]).forEach(function(b){
        h+=tile(b.meal==='Breakfast'?'break':b.meal==='Supper'?'supper':'lunch',b.meal,fmt(b.eaten),fmt(b.punches)+' punches',' data-pmeal="'+esc(b.meal)+'"');
      });
    }else if(view==='period'&&ml){
      var p=ml.period, t=p.totals, w=periodRange(p);
      h+=tile('meals','Meals Eaten',fmt(t.meals),w);
      h+=tile('total','Employees',fmt(t.employees));
      h+=tile('punch','Punches',fmt(t.punches));
      var cost=(t.meals&&t.unpriced_meals===t.meals)?'—':money(t.cost);
      h+=tile('cost','Estimated Cost'+curLbl(),cost,t.unpriced_meals?fmt(t.unpriced_meals)+' meals · '+fmt(t.unpriced_days)+' days unpriced':'');
      var daysPast=(p.days||[]).filter(function(x){return !x.future}).length||1;
      h+=tile('lunch','Meals per Day',fmt(Math.round(t.meals/daysPast)));
      h+=tile('active','Cost per Day'+curLbl(),(cost==='—')?'—':money(Math.round(t.cost/daysPast)));
    }
    $('kpis').innerHTML=h;
  }
  function periodRange(p){
    var ws=p.windows||[]; if(!ws.length) return '';
    var same=ws.every(function(w){return w.start===ws[0].start&&w.end===ws[0].end});
    if(same) return dm(ws[0].start)+' – '+dm(ws[0].end);
    var s=ws.map(function(w){return w.start}).sort()[0], e=ws.map(function(w){return w.end}).sort().pop();
    return dm(s)+' – '+dm(e);
  }

  // ── forecast view ──
  function sumInto(t,g){['active','off_day','sick','leave','expected','meals'].forEach(function(k){t[k]=(t[k]||0)+(g[k]||0)})}
  function groupRow(cls,a,b,g,past,attrs){
    return '<tr class="'+cls+'"'+(attrs||'')+'><td>'+a+'</td><td>'+b+'</td><td class="n">'+fmt(g.active)+'</td><td class="n">'+fmt(g.off_day)+'</td><td class="n">'+fmt(g.sick)+'</td><td class="n">'+fmt(g.leave)+'</td><td class="n exp">'+fmt(g.expected)+'</td><td class="n">'+(past?fmt(g.meals):'—')+'</td></tr>';
  }
  function renderGroups(){
    var gs=fc.groups||[], past=fc.date<=today(), html='', total={}, byCo={}, order=[];
    $('g-meals-h').textContent=fc.meal+' Ate';
    gs.forEach(function(g){if(!byCo[g.company]){byCo[g.company]=[];order.push(g.company)}byCo[g.company].push(g)});
    order.forEach(function(co){
      var sub={};
      byCo[co].forEach(function(g){
        sumInto(sub,g); sumInto(total,g);
        html+=groupRow('click','<span class="muted">'+esc(co)+'</span>',esc(g.farm||'—'),g,past,' data-co="'+esc(co)+'" data-farm="'+esc(g.farm)+'"');
      });
      if(order.length>1&&byCo[co].length>1)html+=groupRow('sub',esc(co),'',sub,past);
    });
    $('g-body').innerHTML=html||'<tr><td colspan="8" class="empty">No employees</td></tr>';
    $('g-foot').innerHTML=gs.length>1?groupRow('','Total','',total,past):'';
    $('g-count').textContent=gs.length+' unit'+(gs.length===1?'':'s');
  }
  function menuOf(r,m){return ((r&&r.menu)||{})[m||(fc&&fc.meal)||'Lunch']||''}
  function costCell(v,unpriced){return v==null?'—':(unpriced?'<span title="'+fmt(unpriced)+' unpriced">'+money(v)+'<span class="more">*</span></span>':money(v))}
  function renderDays(){
    var html='';
    $('d-meal-tag').textContent=fc.meal; $('d-meals-h').textContent=fc.meal+' Ate'; $('d-menu-h').textContent=fc.meal+' Menu';
    $('d-fcost-h').textContent='Forecast Cost'+curLbl(); $('d-acost-h').textContent='Actual Cost'+curLbl();
    (fc.days||[]).forEach(function(r){
      var fut=r.meals==null, wd=parse(r.date).getDay(), v='', menu=menuOf(r);
      if(!fut){var diff=(r.meals||0)-r.expected; v='<span class="'+(diff>=0?'pos':'neg')+'">'+(diff>0?'+':'')+fmt(diff)+'</span>'}
      var cls=['click']; if(r.date===fc.date)cls.push('sel'); if(fut)cls.push('future'); if(wd===0||wd===6)cls.push('wknd');
      var fcost=(r.forecast_unpriced&&r.forecast_unpriced===r.expected)?null:r.forecast_cost;
      var acost=fut?null:((r.actual_unpriced&&r.actual_unpriced===r.meals)?null:r.actual_cost);
      html+='<tr class="'+cls.join(' ')+'" data-date="'+r.date+'"><td>'+label(r.date)+'</td><td>'+(menu?esc(menu):'<span class="pill rep">No cost</span>')+'</td><td class="n exp">'+fmt(r.expected)+'</td><td class="n">'+fmt(r.off_day)+'</td><td class="n">'+fmt(r.sick)+'</td><td class="n">'+fmt(r.leave)+'</td><td class="n">'+(fut?'—':fmt(r.meals))+'</td><td class="n">'+(fut?'—':fmt(r.present))+'</td><td class="n">'+(fut?'—':fmt(r.punched))+'</td><td class="n">'+(fut?'—':v)+'</td><td class="n">'+costCell(fcost,r.forecast_unpriced)+'</td><td class="n">'+costCell(acost,r.actual_unpriced)+'</td></tr>';
    });
    $('d-body').innerHTML=html;
    var sel=$('d-body').querySelector('tr.sel');
    if(sel){var w=sel.closest('.tbl-wrap');w.scrollTop=Math.max(0,sel.offsetTop-w.clientHeight/2)}
  }
  var XCOLS={
    off_day:[['employee','Employee'],['employee_name','Name'],['company','Company'],['farm','Unit'],['designation','Designation'],['detail','Off Day']],
    sick:[['employee','Employee'],['employee_name','Name'],['company','Company'],['farm','Unit'],['designation','Designation'],['detail','Leave Type'],['from_date','From'],['to_date','To'],['reference','Reference']],
    leave:[['employee','Employee'],['employee_name','Name'],['company','Company'],['farm','Unit'],['designation','Designation'],['detail','Leave Type'],['from_date','From'],['to_date','To'],['reference','Reference']]
  };
  function xRows(){
    var rows=((fc&&fc.excluded)||{})[xtab]||[], q=($('x-search').value||'').toLowerCase().trim();
    if(!q)return rows;
    return rows.filter(function(r){return XCOLS[xtab].some(function(c){return String(r[c[0]]||'').toLowerCase().indexOf(q)>=0})});
  }
  function xCell(k,r){
    var v=r[k];
    if(k==='detail'){var cls=xtab==='sick'?'sick':xtab==='leave'?'leave':(v==='Weekly Off'?'':'hol');return '<span class="pill '+cls+'">'+esc(v||'—')+'</span>'}
    if(k==='from_date'||k==='to_date')return v?label(v):'—';
    if(k==='reference'&&v&&/^HR-LAP-/.test(v))return '<a href="/app/leave-application/'+encodeURIComponent(v)+'" target="_blank" rel="noopener">'+esc(v)+'</a>';
    if(k==='employee'&&v)return empLink(v);
    return esc(v||'—');
  }
  function renderExcluded(){
    var ex=(fc&&fc.excluded)||{};
    ['off_day','sick','leave'].forEach(function(k){$('x-c-'+k).textContent=fmt((ex[k]||[]).length)});
    Array.prototype.forEach.call(document.querySelectorAll('#x-tabs .et-btn'),function(b){b.classList.toggle('active',b.dataset.tab===xtab)});
    var cols=XCOLS[xtab], rows=xRows();
    $('x-head').innerHTML='<tr>'+cols.map(function(c){return '<th>'+c[1]+'</th>'}).join('')+'</tr>';
    $('x-body').innerHTML=rows.length?rows.map(function(r){return '<tr>'+cols.map(function(c){return '<td>'+xCell(c[0],r)+'</td>'}).join('')+'</tr>'}).join(''):'<tr><td colspan="'+cols.length+'" class="empty">None</td></tr>';
  }

  // ── meal checkins view ──
  function pRows(){
    var rows=(ml&&ml.punches)||[], m=$('p-meal').value, q=($('p-search').value||'').toLowerCase().trim();
    return rows.filter(function(r){
      if(m&&r.meal!==m)return false;
      if(repeatOnly&&r.count<2)return false;
      if(!q)return true;
      return [r.employee,r.employee_name,r.company,r.farm,r.meal,r.payroll_number,r.name].some(function(v){return String(v||'').toLowerCase().indexOf(q)>=0});
    });
  }
  function renderPunches(){
    var rows=pRows(), all=(ml&&ml.punches)||[];
    $('p-count').textContent=fmt(rows.length)+(rows.length!==all.length?' of '+fmt(all.length):'')+' · '+label(ml.date);
    $('p-repeat-n').textContent=fmt(all.filter(function(r){return r.count>1}).length);
    $('p-repeat').classList.toggle('active',repeatOnly);
    var html=rows.map(function(r){
      return '<tr'+(r.count>1?' class="rep"':'')+'><td>'+empLink(r.employee,r.payroll_number||r.employee)+'</td><td>'+esc(r.employee_name)+'</td><td>'+esc(dtf(r.time))+'</td><td>'+esc(r.farm||'—')+'</td><td>'+esc(r.company)+'</td><td><span class="pill m-'+esc(r.meal)+'">'+esc(r.meal)+'</span></td><td class="n">'+(r.count>1?'<span class="pill rep">'+r.count+'&times;</span>':'1')+'</td><td><a href="/app/meal-checkin/'+encodeURIComponent(r.name)+'" target="_blank" rel="noopener">'+esc(r.name)+'</a></td></tr>';
    }).join('');
    $('p-body').innerHTML=html||'<tr><td colspan="8" class="empty">No meal checkins</td></tr>';
  }

  // ── payroll period view ──
  function renderPeriod(){
    var p=ml.period, t=p.totals, html='', wins={}, bc=((fc&&fc.period&&fc.period.by_company)||{}), lunch=(fc&&fc.meal)||'Lunch';
    (p.windows||[]).forEach(function(w){wins[w.company]=dm(w.start)+' – '+dm(w.end)});
    var fTot=0, aTot=0;
    (p.by_meal||[]).forEach(function(b){
      var f=b.meal===lunch&&bc[b.company]?bc[b.company].forecast_cost_to_date:null;
      if(f!=null){fTot+=f;aTot+=b.cost||0}
      var diff=f!=null&&b.cost!=null?b.cost-f:null;
      html+='<tr><td>'+esc(b.company)+'</td><td class="muted">'+esc(wins[b.company]||'')+'</td><td><span class="pill m-'+esc(b.meal)+'">'+esc(b.meal)+'</span></td><td class="n">'+fmt(b.meals)+'</td><td class="n">'+fmt(b.punches)+'</td><td class="n">'+(b.unpriced?'<span class="neg">'+fmt(b.unpriced)+'</span>':'0')+'</td><td class="n">'+(b.cost_per_meal==null?'—':money(b.cost_per_meal))+'</td><td class="n">'+(f==null?'':money(f))+'</td><td class="n exp">'+(b.cost==null?'—':money(b.cost))+'</td><td class="n">'+(diff==null?'':'<span class="'+(diff<=0?'pos':'neg')+'">'+(diff>0?'+':'')+money(diff)+'</span>')+'</td></tr>';
    });
    $('c-body').innerHTML=html||'<tr><td colspan="10" class="empty">No meal checkins</td></tr>';
    $('c-foot').innerHTML=(p.by_meal||[]).length?'<tr><td>Total</td><td></td><td></td><td class="n">'+fmt(t.meals)+'</td><td class="n">'+fmt(t.punches)+'</td><td class="n">'+fmt(t.unpriced_meals)+'</td><td></td><td class="n">'+money(fTot)+'</td><td class="n">'+((t.meals&&t.unpriced_meals===t.meals)?'—':money(t.cost))+'</td><td></td></tr>':'';
    var sh='', tot={meals:0,punches:0,cost:0};
    (p.days||[]).forEach(function(d){
      var wd=parse(d.date).getDay(), cls=[], menu=(d.menu||{})[lunch]||'';
      if(d.date===ml.date)cls.push('sel'); if(d.future)cls.push('future'); if(wd===0||wd===6)cls.push('wknd'); cls.push('click');
      tot.meals+=d.meals; tot.punches+=d.punches; tot.cost+=d.cost||0;
      sh+='<tr class="'+cls.join(' ')+'" data-date="'+d.date+'"><td>'+label(d.date)+'</td><td>'+(menu?esc(menu):'<span class="pill rep">No cost</span>')+'</td><td class="n exp">'+(d.future?'—':fmt(d.meals))+'</td><td class="n">'+(d.future?'—':fmt(d.employees))+'</td><td class="n">'+(d.future?'—':fmt(d.punches))+'</td><td class="n">'+(d.future?'—':(d.unpriced?'<span class="neg">'+fmt(d.unpriced)+'</span>':'0'))+'</td><td class="n">'+(d.future||!p.priced?'—':money(d.cost))+'</td></tr>';
    });
    $('s-body').innerHTML=sh||'<tr><td colspan="7" class="empty">No meal checkins</td></tr>';
    $('s-foot').innerHTML=(p.days||[]).length?'<tr><td>Total</td><td></td><td class="n">'+fmt(tot.meals)+'</td><td class="n">'+fmt(t.employees)+'</td><td class="n">'+fmt(tot.punches)+'</td><td class="n">'+fmt(t.unpriced_meals)+'</td><td class="n">'+(p.priced?money(tot.cost):'—')+'</td></tr>':'';
    $('s-menu-h').textContent=lunch+' Menu';
    $('c-cost-h').textContent='Actual Cost'+curLbl(); $('c-fcost-h').textContent='Forecast Cost'+curLbl(); $('s-cost-h').textContent='Cost'+curLbl();
    $('s-range').textContent=periodRange(p);
    var sel=$('s-body').querySelector('tr.sel');
    if(sel){var w=sel.closest('.tbl-wrap');w.scrollTop=Math.max(0,sel.offsetTop-w.clientHeight/2)}
  }

  // ── punches per employee ──
  function empLink(id,text){return '<span class="emp-link" data-emp="'+esc(id)+'" title="'+esc(id)+'">'+esc(text||id)+'</span>'}
  var DATE_FMT=((window.frappe&&frappe.boot&&frappe.boot.sysdefaults&&frappe.boot.sysdefaults.date_format)||'dd-mm-yyyy').toLowerCase();
  function fmtDate(s){var p=String(s||'').slice(0,10).split('-');if(p.length<3)return '';return DATE_FMT.replace('yyyy',p[0]).replace('mm',p[1]).replace('dd',p[2])}
  // full date-time as the site shows it, e.g. 09-09-2026 12:47:31
  function dtf(s){return s?fmtDate(s)+' '+hms(s):''}
  function toDate(s){if(!s)return null;var m=String(s).match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}):(\d{2}))?/);if(!m)return null;return new Date(+m[1],+m[2]-1,+m[3],+(m[4]||0),+(m[5]||0),+(m[6]||0))}
  var PAYROLL={};
  function payroll(emp){return PAYROLL[emp]||emp}
  function secs(a,b){return Math.round((new Date(String(b).replace(' ','T'))-new Date(String(a).replace(' ','T')))/1000)}
  function gap(s){
    if(s==null)return '';
    if(s<60)return '+'+s+'s';
    var m=Math.floor(s/60);return '+'+pad(Math.floor(m/60))+':'+pad(m%60);
  }
  // same rule as canteen_analysis.punch_sequence: first 4 punches, the gaps
  // between them, and how many more
  function sequence(rows){
    var t=rows.map(function(r){return r.time}).sort(), shown=t.slice(0,4), iv=[];
    for(var i=1;i<shown.length;i++)iv.push(secs(shown[i-1],shown[i]));
    return {count:t.length,times:shown,intervals:iv,more:Math.max(0,t.length-4)};
  }
  function eRows(){
    var m=$('e-meal').value, q=($('e-search').value||'').toLowerCase().trim(), out=[];
    ((ml&&ml.by_employee)||[]).forEach(function(r){
      var rows=m?r.rows.filter(function(x){return x.meal===m}):r.rows;
      if(!rows.length)return;
      var seq=m?sequence(rows):r, meals=[];
      rows.forEach(function(x){if(meals.indexOf(x.meal)<0)meals.push(x.meal)});
      var row={employee:r.employee,employee_name:r.employee_name,company:r.company,farm:r.farm,meals:meals,count:seq.count,times:seq.times,intervals:seq.intervals,more:seq.more};
      if(multiOnly&&row.count<2)return;
      if(q&&![row.employee,payroll(row.employee),row.employee_name,row.farm,row.company].some(function(v){return String(v||'').toLowerCase().indexOf(q)>=0}))return;
      out.push(row);
    });
    out.sort(function(a,b){return a.times[0]<b.times[0]?-1:a.times[0]>b.times[0]?1:0});
    return out;
  }
  function renderEmployees(){
    var rows=eRows(), all=(ml&&ml.by_employee)||[];
    $('e-count').textContent=fmt(rows.length)+' · '+label(ml.date);
    $('e-multi-n').textContent=fmt(all.filter(function(r){return r.count>1}).length);
    $('e-multi').classList.toggle('active',multiOnly);
    var html=rows.slice(0,3000).map(function(r){
      var cells='';
      for(var i=0;i<4;i++){
        var t=r.times[i];
        if(!t){cells+='<td class="muted">—</td>';continue}
        var iv=i?r.intervals[i-1]:null;
        cells+='<td>'+esc(dtf(t))+(i?'<span class="iv'+(iv<600?' short':'')+'">'+gap(iv)+'</span>':'')+'</td>';
      }
      return '<tr'+(r.count>1?' class="multi"':'')+'><td>'+empLink(r.employee,payroll(r.employee))+'</td><td>'+esc(r.employee_name)+'</td>'+cells+'<td>'+esc(r.farm||'—')+'</td><td>'+r.meals.map(function(m){return '<span class="pill m-'+esc(m)+'">'+esc(m)+'</span>'}).join(' ')+'</td><td class="n">'+fmt(r.count)+(r.more?'<span class="more">+'+r.more+' more</span>':'')+'</td></tr>';
    }).join('');
    $('e-body').innerHTML=html||'<tr><td colspan="9" class="empty">No meal checkins</td></tr>';
  }
  function eXls(){
    var cols=[['payroll','Payroll Number'],['employee_name','Name'],['p1','1st Punch',null,'dt'],['p2','2nd Punch',null,'dt'],['i2','1st to 2nd'],['p3','3rd Punch',null,'dt'],['i3','2nd to 3rd'],['p4','4th Punch',null,'dt'],['i4','3rd to 4th'],['farm','Unit'],['company','Company'],['meals','Meals'],['count','Punches'],['more','More'],['employee','Employee']];
    var rows=eRows().map(function(r){
      var o={payroll:payroll(r.employee),employee:r.employee,employee_name:r.employee_name,company:r.company,farm:r.farm,meals:r.meals.join(' / '),count:r.count,more:r.more||''};
      for(var i=0;i<4;i++){o['p'+(i+1)]=r.times[i]||'';if(i)o['i'+(i+1)]=r.intervals[i-1]!=null?gap(r.intervals[i-1]).slice(1):''}
      return o;
    });
    excel('punches_per_employee_'+ml.date,'Punches per Employee',cols,rows);
  }

  // ── charts ──
  var C={expected:'#38a160',ate:'#0891b2',short:'#cb2929',off:'#7c3aed',sick:'#cb2929',leave:'#318ad8',Breakfast:'#db2777',Lunch:'#38a160',Supper:'#3730a3',cost:'#3730a3'};
  function alpha(hex,a){var n=parseInt(hex.slice(1),16);return 'rgba('+(n>>16)+','+((n>>8)&255)+','+(n&255)+','+a+')'}
  var chartDefaultsSet=false;
  function chartDefaults(){
    if(chartDefaultsSet||!window.Chart)return; chartDefaultsSet=true;
    Chart.defaults.font.size=11;
    Chart.defaults.font.family="'Poppins',system-ui,sans-serif";
    Chart.defaults.color='#7c7a72';
    Chart.defaults.borderColor='rgba(10,10,10,.08)';
    Chart.defaults.plugins.legend.labels.boxWidth=8;
    Chart.defaults.plugins.legend.labels.boxHeight=8;
    Chart.defaults.plugins.legend.labels.padding=12;
    Chart.defaults.plugins.tooltip.padding=8;
    Chart.defaults.plugins.tooltip.boxPadding=4;
    Chart.defaults.maintainAspectRatio=false;
  }
  function draw(key,cfg,hasData){
    var cv=$(key); if(!cv)return;
    var wrap=cv.parentNode, prior=wrap.querySelector('.chart-empty');
    wrap.classList.remove('sk-chart');
    if(prior)prior.remove();
    if(charts[key]){try{charts[key].destroy()}catch(e){}charts[key]=null}
    if(!hasData||!window.Chart){
      var d=document.createElement('div');d.className='chart-empty';d.textContent='No data';wrap.appendChild(d);
      cv.style.visibility='hidden';return;
    }
    cv.style.visibility='';
    chartDefaults();
    try{charts[key]=new Chart(cv.getContext('2d'),cfg)}catch(e){if(window.console)console.error(key,e)}
  }
  function legendBottom(){return {position:'bottom',labels:{usePointStyle:true}}}
  function compact(v){v=Number(v);var a=Math.abs(v);return a>=1e6?(v/1e6).toFixed(a>=1e7?0:1)+'M':a>=1e3?(v/1e3).toFixed(a>=1e4?0:1)+'k':String(v)}
  function renderCharts(){
    if(view==='forecast'){if(fc&&!fcLoading)chartsForecast()}
    else if(ml&&!mlLoading){if(view==='meals')chartsMeals();else chartsPeriod()}
  }
  function chartsForecast(){
    var P=fc.period||{days:[],totals:{},windows:[]}, days=P.days||[], m=fc.meal, t=P.totals||{};
    $('ch-trend-h').textContent='Forecast vs Actual · '+m;
    $('ch-trend-tag').textContent=periodRange(P);
    var pct=t.expected_to_date?Math.round((t.ate||0)*100/t.expected_to_date)+'%':'—';
    var st=function(tc,l,v){return '<div class="ch-stat" style="--tc:'+tc+'"><span class="l">'+esc(l)+'</span><span class="v">'+v+'</span></div>'};
    $('ch-trend-stats').innerHTML=st(C.expected,'Forecast '+m+'es',fmt(t.expected))+(t.expected_to_date!==t.expected?st(C.expected,'Forecast to Date',fmt(t.expected_to_date)):'')+st(C.ate,m+'es Eaten',fmt(t.ate))+st('#0a0a0a','Of Forecast',pct)+st('#64748b','Employees Ate',fmt(t.ate_employees))+st('#d9a514','Employees Scanned',fmt(t.scanned_employees));
    draw('ch-trend',{
      data:{labels:days.map(function(d){return label(d.date)}),datasets:[
        {type:'line',label:'Forecast',data:days.map(function(d){return d.expected}),borderColor:C.expected,backgroundColor:alpha(C.expected,.12),fill:true,tension:.3,borderWidth:2,
          pointRadius:days.map(function(d){return d.date===fc.date?5:2}),pointBackgroundColor:C.expected,
          segment:{borderDash:function(c){var d=days[c.p1DataIndex];return d&&d.future?[5,4]:undefined}},order:1},
        {type:'bar',label:'Actual',data:days.map(function(d){return d.ate}),backgroundColor:days.map(function(d){return d.date===fc.date?C.ate:alpha(C.ate,.55)}),borderRadius:4,maxBarThickness:26,order:2}
      ]},
      options:{interaction:{mode:'index',intersect:false},plugins:{legend:legendBottom(),tooltip:{callbacks:{footer:function(it){var d=days[it[0].dataIndex];if(!d)return '';var out=[];if(menuOf(d))out.push(menuOf(d));if(d.ate!=null&&d.expected)out.push(Math.round(d.ate*100/d.expected)+'% of forecast');return out}}}},
        scales:{x:{grid:{display:false}},y:{beginAtZero:true}},
        onClick:function(e,els){if(els&&els.length){var d=days[els[0].index];if(d&&d.date!==selDate)setDate(d.date)}},
        onHover:function(e,els){e.native.target.style.cursor=els.length?'pointer':'default'}}
    },days.some(function(d){return d.expected||d.ate}));

    chartCost(P,days,m,t);

    var s=fc.summary||{}, ex=[s.off_day||0,s.sick||0,s.leave||0];
    $('ch-excl-tag').textContent=fmt(ex[0]+ex[1]+ex[2])+' · '+label(fc.date);
    draw('ch-excl',{type:'doughnut',
      data:{labels:['Off Day','Sick Off','On Leave'],datasets:[{data:ex,backgroundColor:[C.off,C.sick,C.leave],borderColor:'#fff',borderWidth:2,hoverOffset:6}]},
      options:{cutout:'62%',plugins:{legend:{position:'right',labels:{usePointStyle:true}}}}
    },ex[0]+ex[1]+ex[2]>0);

    var gs=(fc.groups||[]).filter(function(g){return g.active}), multi=(fc.groups||[]).some(function(g){return g.company!==(fc.groups[0]||{}).company});
    $('ch-unit-h').textContent='Expected vs Ate by Unit';
    var past=fc.date<=today();
    var ds=[{label:'Expected',data:gs.map(function(g){return g.expected}),backgroundColor:alpha(C.expected,.75),borderRadius:4,maxBarThickness:22}];
    if(past)ds.push({label:'Ate',data:gs.map(function(g){return g.meals||0}),backgroundColor:alpha(C.ate,.8),borderRadius:4,maxBarThickness:22});
    draw('ch-unit',{type:'bar',
      data:{labels:gs.map(function(g){return g.farm||'—'}),datasets:ds},
      options:{layout:{padding:{left:6}},plugins:{legend:legendBottom(),tooltip:{callbacks:{title:function(it){var g=gs[it[0].dataIndex];return (multi?g.company+' · ':'')+(g.farm||'—')}}}},scales:{x:{grid:{display:false},ticks:{autoSkip:false,maxRotation:45,minRotation:0}},y:{beginAtZero:true}},
        onClick:function(e,els){if(els&&els.length){var g=gs[els[0].index];setSelect('r-company',g.company);fillFarms();setSelect('r-farm',g.farm);buildSidebar();load()}}}
    },gs.some(function(g){return g.expected||g.meals}));
  }
  function chartCost(P,days,m,t){
    var pctC=t.forecast_cost_to_date?Math.round((t.actual_cost||0)*100/t.forecast_cost_to_date)+'%':'—', diff=(t.actual_cost||0)-(t.forecast_cost_to_date||0);
    var st=function(tc,l,v){return '<div class="ch-stat" style="--tc:'+tc+'"><span class="l">'+esc(l)+'</span><span class="v">'+v+'</span></div>'};
    $('ch-cost-h').textContent='Forecast vs Actual Cost'+curLblF();
    $('ch-cost-stats').innerHTML=st(C.expected,'Forecast',money(Math.round(t.forecast_cost||0)))+(t.forecast_cost_to_date!==t.forecast_cost?st(C.expected,'Forecast to Date',money(Math.round(t.forecast_cost_to_date||0))):'')+st(C.cost,'Actual',money(Math.round(t.actual_cost||0)))+st(diff<=0?'#2e7d46':'#c0392b','Difference',(diff>0?'+':'')+money(Math.round(diff)))+st('#0a0a0a','Of Forecast',pctC)+(t.cost_unpriced_days?st('#c2410c','Days Unpriced',fmt(t.cost_unpriced_days)):'');
    draw('ch-cost',{
      data:{labels:days.map(function(d){return label(d.date)}),datasets:[
        {type:'line',label:'Forecast',data:days.map(function(d){return (d.forecast_unpriced&&d.forecast_unpriced===d.expected)?null:Math.round(d.forecast_cost||0)}),borderColor:C.expected,backgroundColor:alpha(C.expected,.12),fill:true,tension:.3,borderWidth:2,spanGaps:false,
          pointRadius:days.map(function(d){return d.date===fc.date?5:2}),pointBackgroundColor:C.expected,segment:{borderDash:function(c){var d=days[c.p1DataIndex];return d&&d.future?[5,4]:undefined}},order:1},
        {type:'bar',label:'Actual',data:days.map(function(d){return d.actual_cost==null||(d.actual_unpriced&&d.actual_unpriced===d.ate)?null:Math.round(d.actual_cost)}),backgroundColor:days.map(function(d){return d.date===fc.date?C.cost:alpha(C.cost,.5)}),borderRadius:4,maxBarThickness:26,order:2}
      ]},
      options:{interaction:{mode:'index',intersect:false},layout:{padding:{left:4}},plugins:{legend:legendBottom(),tooltip:{callbacks:{footer:function(it){var d=days[it[0].dataIndex];if(!d)return '';var out=[];if(menuOf(d))out.push(menuOf(d));if(d.forecast_unpriced)out.push(fmt(d.forecast_unpriced)+' unpriced');return out}}}},
        scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{callback:compact}}},
        onClick:function(e,els){if(els&&els.length){var d=days[els[0].index];if(d&&d.date!==selDate)setDate(d.date)}}}
    },days.some(function(d){return d.forecast_cost||d.actual_cost}));
  }
  function curLblF(){var c=(ml&&ml.currency)||'';return c?' ('+c+')':''}
  function chartsMeals(){
    var p=ml.punches||[], hours=[], byMeal={};
    for(var h=0;h<24;h++)hours.push(h);
    MEALS.forEach(function(m){byMeal[m]=hours.map(function(){return 0})});
    p.forEach(function(r){var h=+(hms(r.time).slice(0,2));if(!byMeal[r.meal])byMeal[r.meal]=hours.map(function(){return 0});byMeal[r.meal][h]++});
    var lo=23,hi=0;
    Object.keys(byMeal).forEach(function(m){byMeal[m].forEach(function(v,h){if(v){lo=Math.min(lo,h);hi=Math.max(hi,h)}})});
    if(lo>hi){lo=5;hi=21}
    lo=Math.max(0,lo-1);hi=Math.min(23,hi+1);
    var hrs=hours.slice(lo,hi+1);
    $('ch-hour-tag').textContent=fmt(p.length)+' punches · '+label(ml.date);
    draw('ch-hour',{type:'bar',
      data:{labels:hrs.map(function(h){return pad(h)+':00'}),datasets:Object.keys(byMeal).map(function(m){return {label:m,data:byMeal[m].slice(lo,hi+1),backgroundColor:alpha(C[m]||'#64748b',.8),borderRadius:3,maxBarThickness:30,stack:'p'}})},
      options:{plugins:{legend:legendBottom()},scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,beginAtZero:true}}}
    },p.length>0);
    var bm=(ml.day&&ml.day.by_meal)||[];
    $('ch-type-tag').textContent=fmt(ml.day.eaten)+' meals';
    draw('ch-type',{type:'doughnut',
      data:{labels:bm.map(function(b){return b.meal}),datasets:[{data:bm.map(function(b){return b.eaten}),backgroundColor:bm.map(function(b){return C[b.meal]||'#64748b'}),borderColor:'#fff',borderWidth:2,hoverOffset:6}]},
      options:{cutout:'62%',plugins:{legend:{position:'right',labels:{usePointStyle:true}}}}
    },bm.some(function(b){return b.eaten}));
  }
  function chartsPeriod(){
    var days=(ml.period.days||[]), past=days.filter(function(d){return !d.future}), names=[];
    days.forEach(function(d){Object.keys(d.by_meal||{}).forEach(function(m){if(names.indexOf(m)<0)names.push(m)})});
    names.sort(function(a,b){return MEALS.indexOf(a)-MEALS.indexOf(b)});
    $('ch-pmeals-tag').textContent=periodRange(ml.period);
    var sel=function(d){return d.date===ml.date};
    draw('ch-pmeals',{type:'bar',
      data:{labels:days.map(function(d){return label(d.date)}),datasets:names.map(function(m){return {label:m,data:days.map(function(d){return d.future?null:((d.by_meal||{})[m]||0)}),backgroundColor:days.map(function(d){return alpha(C[m]||'#64748b',sel(d)?1:.75)}),borderRadius:2,maxBarThickness:22,stack:'m'}})},
      options:{plugins:{legend:legendBottom()},scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,beginAtZero:true}},
        onClick:function(e,els){if(els&&els.length){var d=days[els[0].index];if(d&&d.date!==selDate)setDate(d.date)}}}
    },past.some(function(d){return d.meals}));
    $('ch-pcost-h').textContent='Estimated Cost per Day'+curLbl();
    draw('ch-pcost',{type:'line',
      data:{labels:days.map(function(d){return label(d.date)}),datasets:[{label:'Cost',data:days.map(function(d){return d.future?null:Math.round(d.cost||0)}),borderColor:C.cost,backgroundColor:alpha(C.cost,.12),fill:true,tension:.3,borderWidth:2,pointRadius:days.map(function(d){return sel(d)?5:2}),pointBackgroundColor:C.cost}]},
      options:{layout:{padding:{left:4}},plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{callback:compact}}},
        onClick:function(e,els){if(els&&els.length){var d=days[els[0].index];if(d&&d.date!==selDate)setDate(d.date)}}}
    },ml.period.priced&&past.some(function(d){return d.cost}));
  }

  // ── employee drawer (from the left) ──
  var OUT_LABEL={ate:'Ate',missed:'Missed',today:'Today',upcoming:'',off_day:'Off',sick:'Sick',leave:'Leave',not_employed:''};
  function openDrawer(emp){
    drEmp=emp; var my=++drSeq;
    $('dr-name').textContent=emp; $('dr-sub').textContent='';
    $('dr-body').innerHTML=drSkeleton();
    $('emp-overlay').classList.add('open'); $('emp-overlay').setAttribute('aria-hidden','false');
    document.body.style.overflow='hidden';
    get('canteen_employee',{employee:emp,date:selDate||today(),meal:$('r-meal').value||'Lunch'}).then(function(d){
      if(my!==drSeq)return; renderDrawer(d);
    }).catch(function(e){if(my===drSeq)$('dr-body').innerHTML='<div class="dr-empty">'+esc(e.message||String(e))+'</div>'});
  }
  function drSkeleton(){
    var h='<div class="sk sk-line" style="width:180px;margin-bottom:14px"></div><div class="dr-kpis">';
    for(var i=0;i<8;i++)h+='<div class="dr-kpi dr-kpi-sk"><div class="sk sk-lbl"></div><div class="sk sk-val" style="margin-top:8px"></div></div>';
    h+='</div><div class="sk sk-line" style="width:140px;margin:18px 0 10px"></div><div class="dr-list">';
    for(var j=0;j<10;j++)h+='<span class="sk sk-pill"></span>';
    h+='</div><div class="sk sk-line" style="width:120px;margin:18px 0 10px"></div><div class="dr-tbl-wrap"><table class="dr-tbl"><tbody>';
    for(var r=0;r<8;r++){h+='<tr>';for(var c=0;c<6;c++)h+='<td><span class="sk sk-cell" style="width:'+(45+((r*7+c*13)%45))+'%"></span></td>';h+='</tr>'}
    return h+'</tbody></table></div>';
  }
  function closeDrawer(){
    drSeq++; $('emp-overlay').classList.remove('open'); $('emp-overlay').setAttribute('aria-hidden','true');
    document.body.style.overflow='';
  }
  var STATUS_LABEL={expected:'Working',off_day:'Off Day',sick:'Sick Off',leave:'On Leave',not_employed:'Not Employed'};
  function drExcel(d){
    excel('employee_'+d.employee+'_'+d.period.start+'_'+d.period.end,d.employee,[
      ['date','Date',null,'d'],['day','Day',function(x){return WD[parse(x.date).getDay()]}],['status','Day Status',function(x){return x.status==='expected'?'Working':(x.detail||STATUS_LABEL[x.status]||x.status)}],
      ['attendance','Attendance'],['scans','Biometric Punches',function(x){return (x.biometric||{}).count||0}],['first','First Scan',function(x){return x.biometric&&x.biometric.first||''},'dt'],['last','Last Scan',function(x){return x.biometric&&x.biometric.count>1?x.biometric.last:''},'dt'],
      ['meals','Meals',function(x){return (x.punches||[]).map(function(p){return p.meal+' '+dtf(p.time)}).join(', ')}],['menu',d.meal+' Menu',function(x){return (x.menu||{})[d.meal]||''}],['missed','Missed '+d.meal,function(x){return x.outcome==='missed'?'Yes':''}]
    ],d.days);
  }
  function renderDrawer(d){
    drData=d;
    var s=d.summary, m=d.meal;
    $('dr-name').textContent=d.employee_name||d.employee;
    $('dr-sub').textContent=[d.employee,d.company,d.farm,d.designation].filter(Boolean).join(' · ');
    var k=function(tc,l,v){return '<div class="dr-kpi" style="--tc:'+tc+'"><div class="lbl">'+esc(l)+'</div><div class="val">'+v+'</div></div>'};
    var h='<div class="dr-period">'+esc(labelY(d.period.start))+' – '+esc(labelY(d.period.end))+'</div>';
    h+='<div class="dr-kpis">'
      +k('#0a0a0a','Days in Period',fmt(s.days_in_period))
      +k('#64748b','Working Days',fmt(s.working_days))
      +k(C.expected,'Days Present',fmt(s.present))
      +k(C.ate,'Days Ate '+m,fmt(s.ate))
      +k(C.short,'Days Missed',fmt(s.missed))
      +k('#d9a514','Biometric Punches',fmt(s.biometric_punches))
      +k(C.off,'Meals',fmt(s.meals))
      +k(C.cost,'Estimated Cost'+(d.currency?' ('+d.currency+')':''),d.priced?money(d.cost)+(s.unpriced_meals?'<span class="more">+'+fmt(s.unpriced_meals)+' unpriced</span>':''):'—')
      +'</div>';
    var eaten=d.days.filter(function(x){return x.ate});
    var missed=d.days.filter(function(x){return x.outcome==='missed'});
    h+='<div class="dr-sec">Days Ate '+esc(m)+' · '+eaten.length+'</div>';
    h+=eaten.length?'<div class="dr-list">'+eaten.map(function(x){return '<span class="pill m-Lunch">'+esc(label(x.date))+'</span>'}).join('')+'</div>':'<div class="dr-empty">None</div>';
    h+='<div class="dr-sec">Days Not Eaten · '+missed.length+'</div>';
    h+=missed.length?'<div class="dr-list">'+missed.map(function(x){return '<span class="pill sick">'+esc(label(x.date))+'</span>'}).join('')+'</div>':'<div class="dr-empty">None</div>';
    h+='<div class="dr-sec">Per Day</div><div class="dr-tbl-wrap"><table class="dr-tbl dr-days"><thead><tr><th>Date</th><th>Day Status</th><th>Attendance</th><th class="n">Scans</th><th>First Scan</th><th>Last Scan</th><th>Meals</th><th>'+esc(m)+' Menu</th><th></th></tr></thead><tbody>';
    var todayIso=today();
    h+=d.days.map(function(x){
      var fut=x.date>todayIso, b=x.biometric||{count:0};
      var st=x.status==='off_day'?'<span class="pill hol">'+esc(x.detail||'Off Day')+'</span>':x.status==='sick'?'<span class="pill sick">'+esc(x.detail||'Sick Off')+'</span>':x.status==='leave'?'<span class="pill leave">'+esc(x.detail||'On Leave')+'</span>':'<span class="muted">'+esc(STATUS_LABEL[x.status]||x.status)+'</span>';
      var meals=(x.punches||[]).map(function(p){return '<span class="pill m-'+esc(p.meal)+'">'+esc(p.meal)+' · '+esc(dtf(p.time))+'</span>'}).join(' ');
      var flag=x.outcome==='missed'?'<span class="pill rep">Missed</span>':'';
      return '<tr class="'+(x.date===d.date?'sel ':'')+(fut?'future':'')+'"><td>'+esc(label(x.date))+'</td><td>'+st+'</td><td>'+esc(x.attendance||(fut?'':'—'))+'</td><td class="n">'+(fut?'':fmt(b.count))+'</td><td>'+(b.first?esc(dtf(b.first)):'')+'</td><td>'+(b.last&&b.count>1?esc(dtf(b.last)):'')+'</td><td class="dr-meals">'+meals+'</td><td class="muted">'+esc((x.menu||{})[m]||'')+'</td><td>'+flag+'</td></tr>';
    }).join('');
    h+='</tbody></table></div>';
    h+='<div class="dr-sec">Meals</div><table class="dr-tbl"><thead><tr><th>Meal</th><th class="n">Meals</th><th class="n">Punches</th><th class="n">Cost / Meal</th><th class="n">Cost'+esc(d.currency?' ('+d.currency+')':'')+'</th></tr></thead><tbody>';
    h+=(d.by_meal||[]).length?d.by_meal.map(function(b){return '<tr><td><span class="pill m-'+esc(b.meal)+'">'+esc(b.meal)+'</span></td><td class="n">'+fmt(b.meals)+'</td><td class="n">'+fmt(b.punches)+'</td><td class="n">'+(b.cost_per_meal==null?'—':money(b.cost_per_meal))+'</td><td class="n">'+(b.cost==null?'—':money(b.cost))+'</td></tr>'}).join(''):'<tr><td colspan="5" class="dr-empty">None</td></tr>';
    h+='</tbody>'+((d.by_meal||[]).length?'<tfoot><tr><td>Total</td><td class="n">'+fmt(s.meals)+'</td><td class="n">'+fmt(s.meal_punches)+'</td><td></td><td class="n">'+(d.priced?money(d.cost):'—')+'</td></tr></tfoot>':'')+'</table>';
    $('dr-body').innerHTML=h;
  }

  // ── payroll period: every employee ──
  var PE_COLS=[['employee','Employee'],['employee_name','Name'],['farm','Unit'],['expected','Days Expected'],['eaten','Days Ate'],['missed','Days Missed'],['present','Days Present'],['Breakfast','Breakfast'],['Lunch','Lunch'],['Supper','Supper'],['cost','Cost']];
  var peSort={key:'missed',dir:-1};
  function peRows(){
    var q=($('pe-search').value||'').toLowerCase().trim(), rows=((fc&&fc.period&&fc.period.employees)||[]).map(function(r){
      var o=Object.assign({},r); MEALS.forEach(function(m){o[m]=(r.meals||{})[m]||0}); return o;
    });
    if(q)rows=rows.filter(function(r){return [r.employee,r.employee_name,r.farm,r.company,r.designation].some(function(v){return String(v||'').toLowerCase().indexOf(q)>=0})});
    var k=peSort.key, dir=peSort.dir;
    rows.sort(function(a,b){var x=a[k],y=b[k];if(typeof x==='number'||typeof y==='number')return ((x||0)-(y||0))*dir||String(a.employee_name).localeCompare(String(b.employee_name));return String(x||'').localeCompare(String(y||''))*dir});
    return rows;
  }
  function renderPeriodEmployees(){
    if(!fc)return;
    var rows=peRows(), all=((fc.period||{}).employees||[]), P=fc.period||{};
    $('pe-count').textContent=fmt(rows.length)+(rows.length!==all.length?' of '+fmt(all.length):'')+' · '+periodRange(P);
    $('pe-cost-h').textContent='Cost'+curLbl();
    Array.prototype.forEach.call(document.querySelectorAll('#pe-tbl thead th[data-k]'),function(th){th.classList.toggle('s',th.dataset.k===peSort.key);th.classList.toggle('asc',th.dataset.k===peSort.key&&peSort.dir>0);th.classList.toggle('desc',th.dataset.k===peSort.key&&peSort.dir<0)});
    var html=rows.slice(0,3000).map(function(r){
      return '<tr><td>'+empLink(r.employee)+'</td><td>'+esc(r.employee_name)+'</td><td>'+esc(r.farm||'—')+'</td><td class="n">'+fmt(r.expected)+'</td><td class="n exp">'+fmt(r.eaten)+'</td><td class="n'+(r.missed?' neg':'')+'">'+fmt(r.missed)+'</td><td class="n">'+fmt(r.present)+'</td><td class="n">'+fmt(r.Breakfast)+'</td><td class="n">'+fmt(r.Lunch)+'</td><td class="n">'+fmt(r.Supper)+'</td><td class="n">'+(P.priced?money(r.cost):'—')+'</td></tr>';
    }).join('');
    $('pe-body').innerHTML=html||'<tr><td colspan="11" class="empty">No employees</td></tr>';
  }

  // ── Excel export (SheetJS, same helper as Attendance Insights) ──
  function dlXLSX(data,sheet,filename,header){if(!window.XLSX)return;var ws=XLSX.utils.json_to_sheet(data,header?{header:header}:undefined);var wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,ws,String(sheet).slice(0,31));XLSX.writeFile(wb,filename)}
  // filenames and the tab title follow the company picked in the page's filter
  function companySlug(){var c=($('r-company')||{}).value||'';return c?c.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,''):'all_companies'}
  function syncTitle(){var c=($('r-company')||{}).value||'';document.title='Canteen Analysis'+(c?' · '+c:'')}
  var XL_FMT={dt:'dd-mm-yyyy hh:mm:ss',d:'dd-mm-yyyy'};
  function excel(file,sheet,cols,rows){
    var header=cols.map(function(c){return c[1]});
    var data=rows.map(function(r){var o={};cols.forEach(function(c){var v=typeof c[2]==='function'?c[2](r):r[c[0]];if(c[3]&&v)v=toDate(v)||v;o[c[1]]=v==null?'':v});return o});
    if(!window.XLSX)return;
    var ws=XLSX.utils.json_to_sheet(data,{header:header,cellDates:true});
    // date and date-time columns: real Excel dates with the site's day-first format
    cols.forEach(function(c,ci){
      if(!c[3])return;
      for(var ri=1;ri<=data.length;ri++){var cell=ws[XLSX.utils.encode_cell({r:ri,c:ci})];if(cell&&cell.v instanceof Date){cell.t='d';cell.z=XL_FMT[c[3]]}}
    });
    var wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,ws,String(sheet).slice(0,31));
    XLSX.writeFile(wb,'canteen_'+companySlug()+'_'+file+'.xlsx',{cellDates:true});
  }
  function pr(p){var w=(p&&p.windows)||[];if(!w.length)return '';var s=w.map(function(x){return x.start}).sort()[0],e=w.map(function(x){return x.end}).sort().pop();return s+'_'+e}

  // ── views ──
  function setView(v){
    view=v;
    Array.prototype.forEach.call(document.querySelectorAll('.sb-view-btn'),function(b){b.classList.toggle('active',b.dataset.view===v)});
    Array.prototype.forEach.call(document.querySelectorAll('.view'),function(s){s.classList.toggle('active',s.id==='view-'+v)});
    var ms=$('r-meal-ta')||$('r-meal'); ms.style.display=v==='forecast'?'':'none';
    writeUrl();
    renderTiles();renderCharts();
  }

  function wire(){
    $('r-refresh').addEventListener('click',function(){setDate(today())});
    $('r-company').addEventListener('change',function(){
      syncCompanyLabel(); try{localStorage.setItem('canteen_company',$('r-company').value)}catch(e){}
      $('r-farm').value=''; fillFarms(); buildSidebar(); load();
    });
    $('r-meal').addEventListener('change',load);
    $('r-quick').addEventListener('click',function(e){var b=e.target.closest('.btn');if(b)setDate(addDays(today(),+b.dataset.off))});
    $('att-sb-body').addEventListener('click',function(e){
      var farm=e.target.closest('.att-sb-farm'), head=e.target.closest('.att-sb-co-head');
      if(farm){setSelect('r-company',farm.dataset.co);fillFarms();setSelect('r-farm',farm.dataset.farm);$('att-sidebar').classList.remove('open');load();return}
      if(head){
        var grp=head.parentNode;
        if(head.classList.contains('active')){grp.classList.toggle('collapsed');return}
        setSelect('r-company',head.dataset.co);fillFarms();$('r-farm').value='';buildSidebar();load();
      }
    });
    $('d-body').addEventListener('click',function(e){var tr=e.target.closest('tr[data-date]');if(tr&&tr.dataset.date!==selDate)setDate(tr.dataset.date)});
    $('s-body').addEventListener('click',function(e){var tr=e.target.closest('tr[data-date]');if(tr&&tr.dataset.date!==selDate)setDate(tr.dataset.date)});
    $('g-body').addEventListener('click',function(e){
      var tr=e.target.closest('tr[data-co]');if(!tr)return;
      setSelect('r-company',tr.dataset.co);fillFarms();setSelect('r-farm',tr.dataset.farm);buildSidebar();load();
    });
    $('x-tabs').addEventListener('click',function(e){var b=e.target.closest('.et-btn');if(b){xtab=b.dataset.tab;renderExcluded()}});
    $('x-search').addEventListener('input',renderExcluded);
    $('x-xls').addEventListener('click',function(){if(fc)excel(xtab+'_'+fc.date,{off_day:'Off Day',sick:'Sick Off',leave:'On Leave'}[xtab],XCOLS[xtab],xRows())});
    $('p-search').addEventListener('input',renderPunches);
    $('e-search').addEventListener('input',renderEmployees);
    $('pe-search').addEventListener('input',renderPeriodEmployees);
    $('pe-xls').addEventListener('click',function(){if(fc)excel('period_employees_'+pr(fc.period),'Employees',PE_COLS,peRows())});
    $('pe-tbl').querySelector('thead').addEventListener('click',function(e){var th=e.target.closest('th[data-k]');if(!th)return;var k=th.dataset.k;peSort=peSort.key===k?{key:k,dir:-peSort.dir}:{key:k,dir:(['employee','employee_name','farm'].indexOf(k)>=0?1:-1)};renderPeriodEmployees()});
    $('e-meal').addEventListener('change',renderEmployees);
    $('e-multi').addEventListener('click',function(){multiOnly=!multiOnly;renderEmployees()});
    $('e-xls').addEventListener('click',function(){if(ml)eXls()});
    document.addEventListener('click',function(e){var l=e.target.closest('.emp-link');if(l&&l.dataset.emp)openDrawer(l.dataset.emp)});
    $('emp-overlay').addEventListener('click',function(e){if(e.target===$('emp-overlay'))closeDrawer()});
    $('dr-close').addEventListener('click',closeDrawer);
    document.addEventListener('keydown',function(e){if(e.key==='Escape'&&$('emp-overlay').classList.contains('open'))closeDrawer()});
    $('p-meal').addEventListener('change',renderPunches);
    $('p-repeat').addEventListener('click',function(){repeatOnly=!repeatOnly;renderPunches()});
    $('p-xls').addEventListener('click',function(){if(ml)excel('punches_'+ml.date,'Meal Checkins',[['payroll_number','Payroll Number',function(r){return r.payroll_number||r.employee}],['employee_name','Name'],['time','Date & Time',null,'dt'],['farm','Unit'],['company','Company'],['meal','Meal'],['count','Punches'],['name','Checkin'],['employee','Employee']],pRows())});
    $('s-xls').addEventListener('click',function(){if(ml)excel('period_days_'+pr(ml.period),'Per Day',[['date','Date',null,'d'],['menu',(fc&&fc.meal||'Lunch')+' Menu',function(d){return (d.menu||{})[(fc&&fc.meal)||'Lunch']||''}],['meals','Meals'],['employees','Employees'],['punches','Punches'],['unpriced','Unpriced'],['cost','Cost']],(ml.period.days||[]).filter(function(d){return !d.future}))});
    $('c-xls').addEventListener('click',function(){if(!ml)return;var bc=((fc&&fc.period&&fc.period.by_company)||{}),lunch=(fc&&fc.meal)||'Lunch';excel('period_cost_'+pr(ml.period),'Estimated Cost',[['company','Company'],['meal','Meal'],['meals','Meals'],['punches','Punches'],['unpriced','Unpriced'],['cost_per_meal','Avg Cost / Meal'],['forecast','Forecast Cost',function(b){return b.meal===lunch&&bc[b.company]?bc[b.company].forecast_cost_to_date:''}],['cost','Actual Cost']],ml.period.by_meal||[])});
    $('dr-xls').addEventListener('click',function(){if(drData)drExcel(drData)});
    $('kpis').addEventListener('click',function(e){
      var k=e.target.closest('.kpi.click');if(!k)return;
      if(k.dataset.x){xtab=k.dataset.x;renderExcluded();$('x-tabs').scrollIntoView({behavior:'smooth',block:'center'})}
      else if(k.dataset.view){setView(k.dataset.view)}
      else if(k.dataset.repeat){repeatOnly=true;renderPunches()}
      else if(k.dataset.pmeal){$('p-meal').value=k.dataset.pmeal;repeatOnly=false;renderPunches()}
    });
    Array.prototype.forEach.call(document.querySelectorAll('.sb-view-btn'),function(b){b.addEventListener('click',function(){setView(b.dataset.view);$('att-sidebar').classList.remove('open')})});
    var t=$('att-sb-toggle'),c=$('att-sb-close'),sb=$('att-sidebar');
    t.addEventListener('click',function(){sb.classList.toggle('open')});
    c.addEventListener('click',function(){sb.classList.remove('open')});
  }

  function start(){
    var init=readUrl();
    selDate=iso(new Date());
    wire();
    initFilters(init).then(function(){
      selDate=today();
      initDateCtl();
      if(init.view&&$('view-'+init.view))setView(init.view);
      load();
    }).catch(function(e){$('r-status').textContent=e.message||String(e)});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();


/* ── Signed-in user block (same as attendance-insights.js) ── */
(function () {
  function cookie(n){ var m=document.cookie.match("(^|;)\\s*"+n+"\\s*=\\s*([^;]+)"); return m?decodeURIComponent(m[2]):""; }
  // Static identity block: avatar initials + full name + email. No dropdown --
  // the only thing it ever offered (Open Desk) is now the Home button in the
  // sidebar header, so clicking here does nothing by design.
  var box=document.getElementById("wm-avatar");
  if(!box) return;
  var user=cookie("user_id")||"Guest";
  var full=cookie("full_name")||"";
  var login=document.getElementById("wm-account-login");
  var ini=document.getElementById("wm-ini");
  var nameEl=document.getElementById("wm-name");
  var uidEl=document.getElementById("wm-uid");
  function initials(s){
    return String(s||"").replace(/@.*$/,"").split(/[._ -]/).filter(Boolean)
      .slice(0,2).map(function(p){return p[0].toUpperCase();}).join("");
  }
  if(user==="Guest"||!user){
    if(ini) ini.textContent="\u2192";
    if(nameEl) nameEl.textContent="Not signed in";
    if(uidEl) uidEl.textContent="";
    if(login){ login.style.display=""; login.href="/login?redirect-to="+encodeURIComponent(location.pathname); }
  } else {
    // Administrator has no separate full_name, so the email line would just
    // repeat the name -- show it once in that case rather than twice.
    var name=full||user;
    if(ini) ini.textContent=initials(full||user);
    if(nameEl) nameEl.textContent=name;
    if(uidEl) uidEl.textContent=(user===name)?"":user;
  }
})();

/* ── Styled dropdowns ─────────────────────────────────────────────────────
   A native <select>'s open option list is drawn by the browser and takes no
   CSS, so every visible <select> on the page gets a styled trigger and menu.
   The <select> itself stays in the DOM, hidden: it keeps the value, fires the
   same 'change' the page already listens for, and anything that sets .value,
   .selectedIndex, disabled or replaces its options is reflected here. */
(function(){
  var CHEVRON='<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
  var CHECK='<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  var SEARCH_FROM=10;
  var openMenu=null;

  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]}); }

  function closeMenu(){
    if(!openMenu) return;
    openMenu.menu.remove();
    openMenu.trigger.classList.remove('open');
    openMenu.trigger.setAttribute('aria-expanded','false');
    openMenu=null;
  }

  function enhance(sel){
    if(sel.__taSel || sel.hasAttribute('data-native')) return;
    if(getComputedStyle(sel).display==='none') return;   // deliberately hidden ones stay so
    sel.__taSel=true;

    var trigger=document.createElement('button');
    trigger.type='button';
    trigger.className='ta-sel';
    trigger.setAttribute('aria-haspopup','listbox');
    trigger.setAttribute('aria-expanded','false');
    if(sel.id) trigger.id=sel.id+'-ta';
    trigger.innerHTML='<span class="ta-sel-text"></span><span class="ta-sel-chev">'+CHEVRON+'</span>';
    var w=sel.style.width, mw=sel.style.minWidth, xw=sel.style.maxWidth;
    trigger.style.width = w && w!=='auto' ? w : '';
    if(mw) trigger.style.minWidth=mw;
    if(xw) trigger.style.maxWidth=xw;
    if(sel.style.height) trigger.style.height=sel.style.height;
    if(sel.id==='r-company'){
      trigger.classList.add('ta-sel-plain');
      var wrap=sel.closest('.r-company-wrap'); if(wrap) wrap.classList.add('ta-enhanced');
    }
    sel.parentNode.insertBefore(trigger, sel.nextSibling);
    sel.classList.add('ta-sel-native');

    function sync(){
      var o=sel.options[sel.selectedIndex];
      trigger.querySelector('.ta-sel-text').textContent = o ? o.text : '';
      trigger.disabled=!!sel.disabled;
      trigger.classList.toggle('ta-sel-empty', !o || o.value==='');
    }
    sel.__taSync=sync;

    // code that sets .value / .selectedIndex fires no event, so the setters are
    // wrapped on this element to keep the trigger's text right
    ['value','selectedIndex'].forEach(function(prop){
      var desc=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
      Object.defineProperty(sel, prop, {
        configurable:true,
        get:function(){ return desc.get.call(this); },
        set:function(v){ desc.set.call(this, v); sync(); }
      });
    });
    sel.addEventListener('change', sync);
    new MutationObserver(sync).observe(sel, {childList:true, subtree:true, attributes:true, attributeFilter:['disabled']});

    trigger.addEventListener('click', function(e){
      e.stopPropagation();
      if(openMenu && openMenu.sel===sel){ closeMenu(); return; }
      open(sel, trigger);
    });
    trigger.addEventListener('keydown', function(e){
      if(e.key==='ArrowDown' || e.key==='ArrowUp' || e.key==='Enter' || e.key===' '){
        e.preventDefault();
        if(!openMenu) open(sel, trigger);
      }
    });
    sync();
  }

  function open(sel, trigger){
    closeMenu();
    var menu=document.createElement('div');
    menu.className='ta-sel-menu';
    menu.setAttribute('role','listbox');
    var many=sel.options.length>=SEARCH_FROM;
    menu.innerHTML=(many?'<div class="ta-sel-search"><input type="text" placeholder="Search…" autocomplete="off"></div>':'')+
      '<div class="ta-sel-list"></div>';
    document.body.appendChild(menu);
    var list=menu.querySelector('.ta-sel-list');
    var active=-1, shown=[];

    function render(q){
      q=(q||'').toLowerCase().trim();
      shown=[];
      var html='';
      for(var i=0;i<sel.options.length;i++){
        var o=sel.options[i];
        if(o.hidden) continue;
        if(q && o.text.toLowerCase().indexOf(q)<0) continue;
        shown.push(i);
        var on=i===sel.selectedIndex;
        html+='<div class="ta-sel-opt'+(on?' selected':'')+(o.disabled?' disabled':'')+(o.value===''?' placeholder':'')+
          '" role="option" data-i="'+i+'" aria-selected="'+on+'"><span class="ta-sel-opt-text">'+esc(o.text)+
          '</span><span class="ta-sel-check">'+(on?CHECK:'')+'</span></div>';
      }
      list.innerHTML=html || '<div class="ta-sel-none">No matches</div>';
      active=shown.indexOf(sel.selectedIndex);
      highlight();
    }
    function highlight(){
      var opts=list.querySelectorAll('.ta-sel-opt');
      for(var k=0;k<opts.length;k++) opts[k].classList.toggle('active', k===active);
      // scroll the list only: scrollIntoView could move the page, and a page
      // scroll closes the menu
      var el=opts[active];
      if(el){
        if(el.offsetTop < list.scrollTop) list.scrollTop=el.offsetTop-4;
        else if(el.offsetTop+el.offsetHeight > list.scrollTop+list.clientHeight)
          list.scrollTop=el.offsetTop+el.offsetHeight-list.clientHeight+4;
      }
    }
    function choose(i){
      if(i==null || i<0) return;
      var o=sel.options[i]; if(!o || o.disabled) return;
      var changed=sel.selectedIndex!==i;
      sel.selectedIndex=i;
      closeMenu();
      trigger.focus({preventScroll:true});
      if(changed) sel.dispatchEvent(new Event('change', {bubbles:true}));
    }
    function place(){
      var r=trigger.getBoundingClientRect();
      menu.style.minWidth=Math.max(r.width, 180)+'px';
      var below=window.innerHeight-r.bottom, h=Math.min(menu.scrollHeight, 320);
      menu.style.left=Math.min(r.left, window.innerWidth-menu.offsetWidth-8)+'px';
      menu.style.top=(below<h+12 && r.top>h+12 ? r.top-h-6 : r.bottom+6)+'px';
    }

    list.addEventListener('mousedown', function(e){ e.preventDefault(); });
    list.addEventListener('click', function(e){
      var el=e.target.closest('.ta-sel-opt'); if(el) choose(+el.getAttribute('data-i'));
    });
    list.addEventListener('mousemove', function(e){
      var el=e.target.closest('.ta-sel-opt'); if(!el) return;
      var k=shown.indexOf(+el.getAttribute('data-i')); if(k!==active){ active=k; highlight(); }
    });
    function onKey(e){
      if(e.key==='ArrowDown'){ e.preventDefault(); active=Math.min(active+1, shown.length-1); highlight(); }
      else if(e.key==='ArrowUp'){ e.preventDefault(); active=Math.max(active-1, 0); highlight(); }
      else if(e.key==='Enter'){ e.preventDefault(); choose(shown[active]); }
      else if(e.key==='Escape' || e.key==='Tab'){ closeMenu(); trigger.focus({preventScroll:true}); }
    }
    menu.addEventListener('keydown', onKey);
    trigger.__taKey=onKey;

    render('');
    var search=menu.querySelector('.ta-sel-search input');
    if(search){ search.addEventListener('input', function(){ render(search.value); place(); }); }
    trigger.classList.add('open');
    trigger.setAttribute('aria-expanded','true');
    openMenu={sel:sel, trigger:trigger, menu:menu};
    place();
    // preventScroll: focusing may scroll the page, and a page scroll closes the menu
    if(search){ search.focus({preventScroll:true}); }
    else { menu.tabIndex=-1; menu.focus({preventScroll:true}); }
  }

  document.addEventListener('click', function(e){
    if(openMenu && !openMenu.menu.contains(e.target)) closeMenu();
  });
  // the menu is fixed to the viewport, so anything scrolling underneath it closes it
  window.addEventListener('scroll', function(e){
    if(openMenu && !openMenu.menu.contains(e.target)) closeMenu();
  }, true);
  window.addEventListener('resize', closeMenu);

  function enhanceAll(){ document.querySelectorAll('select').forEach(enhance); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', enhanceAll);
  else enhanceAll();
  // cards that start hidden (the other action tabs) are enhanced when they appear
  document.addEventListener('click', function(){ setTimeout(enhanceAll, 0); });
})();
