'use strict';
// Shared by work, notes and wellness after the home check-in panel was retired.
const todayCN=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
function uid(){if(crypto.randomUUID)return crypto.randomUUID();const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const h=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);}
const $=id=>document.getElementById(id);
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>hiddenMoney?'••••':(n/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const financeTypes={income:'收入',expense:'支出',refund:'退款',transfer:'转账',financing:'金融往来',excluded:'已排除',review:'待核对'};
const financeColors=['#7198ed','#a69add','#8eafc6','#8e9ab0','#c5c0d8','#d8dce5'];
const FINANCE_PAGE_SIZE=100;
let records=[],financeImports=[],financeBudgets=[],hiddenMoney=false,tab='overview',page='today';
let financeReady=false,financeLoading=null,financeSaving=false,financePage=1,financeEdit=null,financeDirty=false;
function parseCents(s,allowZero=false){if(!/^\d+(\.\d{1,2})?$/.test(s))return null;const [a,b='']=s.split('.');const n=Number(a)*100+Number(b.padEnd(2,'0'));return Number.isSafeInteger(n)&&(allowZero?n>=0:n>0)&&n<=99999999999?n:null;}
function summary(rs){return rs.reduce((s,r)=>{if(r.confirmed===false)return s;if(r.type==='income')s.income+=r.cents;if(r.type==='expense')s.expense+=r.cents;if(r.type==='refund')s.refund+=r.cents;return s;},{income:0,expense:0,refund:0});}
function financeDirection(r){if(r.type==='income'||r.type==='refund')return 'in';if(r.type==='expense')return 'out';if(Number.isSafeInteger(r.signed_cents))return r.signed_cents>0?'in':r.signed_cents<0?'out':'zero';return ['in','out','zero'].includes(r.direction)?r.direction:'unknown';}
function financeAmount(r){const direction=financeDirection(r);return `${direction==='in'?'+':direction==='out'?'−':''}${money(r.cents)}${direction==='unknown'?'<small class="finance-direction-note">方向未定</small>':''}`;}
function updateFinanceDirection(){const type=$('entry-type').value,fixed=type==='income'||type==='refund'?'in':type==='expense'?'out':null;$('finance-direction-field').hidden=!!fixed;if(fixed)$('entry-direction').value=fixed;}
function financeToday(){return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}
function monthRows(){const month=$('month').value;return records.filter(r=>month==='all'||r.date.startsWith(month));}
function filtered(){const q=$('search').value.trim().toLocaleLowerCase(),category=$('finance-category').value,group=$('finance-group').value,type=$('type').value;return monthRows().filter(r=>(window.financeHub?.includeRecord(r,tab)??true)&&(tab==='overview'||((type==='all'||r.type===type)&&(!category||r.category===category)&&(!group||(r.groups||[]).includes(group))&&[r.name,r.note,r.category,r.subcategory,r.account,r.reason,...(r.groups||[])].join(' ').toLocaleLowerCase().includes(q)))).sort((a,b)=>b.date.localeCompare(a.date)||a.id.localeCompare(b.id));}
function toast(s){$('toast').textContent=s;$('toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').classList.remove('show'),2500);}
function financeStatus(message,error=false){$('finance-status').textContent=message;$('finance-status').classList.toggle('error',error);}
// Retry only idempotent finance reads; a failed save must never be replayed.
async function financeReadFetch(url,options={}){
  const target=new URL(url,location.href),method=(options.method||'GET').toUpperCase();
  if(target.origin!==location.origin||method!=='GET'||!['/api/finance','/api/finance/hub'].includes(target.pathname))return fetch(url,options);
  const signal=options.signal,abort=()=>signal?.reason||new DOMException('读取已取消','AbortError');
  const pause=ms=>new Promise((resolve,reject)=>{
    if(signal?.aborted)return reject(abort());
    const done=()=>{signal?.removeEventListener('abort',cancel);resolve();};
    const timer=setTimeout(done,ms);
    function cancel(){clearTimeout(timer);signal?.removeEventListener('abort',cancel);reject(abort());}
    signal?.addEventListener('abort',cancel,{once:true});
  });
  for(let attempt=0;attempt<3;attempt++){
    if(signal?.aborted)throw abort();
    try{
      const response=await fetch(url,options);
      if(attempt===2||![502,503,504].includes(response.status))return response;
      await response.body?.cancel().catch(()=>{});
    }catch(error){if(signal?.aborted)throw abort();if(error?.name!=='TypeError'||attempt===2)throw error;}
    await pause([400,1200][attempt]);
  }
}
window.tiangongFinanceReadFetch=financeReadFetch;
async function financeRequest(payload){const response=await (payload?fetch:financeReadFetch)('/api/finance',payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{cache:'no-store'});let body;try{body=await response.json();}catch{throw new Error('账本暂时无法连接，请稍后重试。');}if(!response.ok)throw Object.assign(new Error(body.error||'账本暂时无法连接，请稍后重试。'),{code:response.status});return body;}
function financeOptions(preferredMonth){
  const current=financeToday().slice(0,7),existing=[...new Set(records.map(r=>r.date.slice(0,7)))].sort().reverse();
  const months=[...new Set([...existing,...financeBudgets.map(b=>b.month),current])].sort().reverse();
  const chosen=preferredMonth||$('month').value||(existing[0]||current);
  $('month').innerHTML='<option value="all">全部时间</option>'+months.map(m=>`<option value="${escape(m)}">${escape(m.replace('-',' 年 '))} 月${m===current?' · 本月':''}</option>`).join('');
  $('month').value=chosen==='all'||months.includes(chosen)?chosen:(existing[0]||current);
  const categories=[...new Set(records.map(r=>r.category))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
  const groups=[...new Set(records.flatMap(r=>r.groups||[]))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
  for(const [id,items,label] of [['finance-category',categories,'全部分类'],['finance-group',groups,'全部原表']]){const selected=$(id).value;$(id).innerHTML=`<option value="">${label}</option>`+items.map(v=>`<option value="${escape(v)}">${escape(v)}</option>`).join('');$(id).value=items.includes(selected)?selected:'';}
  $('finance-category-options').innerHTML=categories.map(c=>`<option value="${escape(c)}"></option>`).join('');
}
async function loadFinance(){
  if(financeSaving)return false;
  if(financeLoading)return financeLoading;
  financeStatus('正在读取账本…');$('finance-refresh').disabled=true;
  financeLoading=(async()=>{try{
    const state=await financeRequest();
    if(!Array.isArray(state.records)||!Array.isArray(state.imports)||!Array.isArray(state.budgets)||!state.records.every(r=>r&&typeof r.id==='string'&&typeof r.date==='string'&&Number.isSafeInteger(r.cents)&&r.cents>=0&&(r.cents>0||['excluded','review'].includes(r.type))&&Object.hasOwn(financeTypes,r.type)))throw new Error('账本资料格式异常，请稍后重试。');
    records=state.records;financeImports=state.imports;financeBudgets=state.budgets;financeReady=true;financeOptions();render();financeStatus('');window.financeHub?.load();return true;
  }catch(e){financeStatus((financeReady?'暂未刷新，仍显示上次读取的账目。':'账本尚未读取成功。')+e.message,true);if(!financeReady)render();return false;
  }finally{financeLoading=null;$('finance-refresh').disabled=false;}})();return financeLoading;
}
window.loadFinance=loadFinance;
function renderFinanceMetadata(){
  const rs=filtered(),review=rs.filter(r=>r.type==='review').length,other=rs.filter(r=>['transfer','financing','excluded'].includes(r.type)).length;
  $('finance-scope-note').textContent=`当前范围 ${rs.length} 笔${review?'，其中 '+review+' 笔待核对':''}${other?'，'+other+' 笔转账、金融往来或已排除记录':''}。待核对和非收支记录未计入汇总。`;
  $('finance-imports').innerHTML=financeImports.length?financeImports.map(item=>`<div class="finance-import"><strong>${escape(item.filename||'历史账本')}${item.version?' · '+escape(item.version):''}</strong><p>${escape(item.start_date||'')} — ${escape(item.end_date||'')} · ${Number(item.count)||0} 笔</p>${Array.isArray(item.notes)&&item.notes.length?'<ul>'+item.notes.map(note=>`<li>${escape(note)}</li>`).join('')+'</ul>':''}</div>`).join(''):'<p>还没有导入历史账本，可以先记下新的收支。</p>';
}
function render(){
  $('finance').dataset.financeView=tab;
  $('add').disabled=!financeReady;$('export').disabled=!financeReady;
  if(!financeReady){$('metrics').innerHTML='';$('chart').innerHTML='<p class="finance-empty">连接账本后显示收支趋势。</p>';$('categories').innerHTML='';$('budget-list').innerHTML='<p class="finance-empty">连接账本后查看预算。</p>';$('rows').innerHTML='<tr><td colspan="6" class="finance-empty">账本尚未读取，请点击刷新。</td></tr>';$('count').textContent='';$('finance-page-label').textContent='';$('finance-prev').disabled=$('finance-next').disabled=true;return;}
  const rs=filtered(),s=summary(rs),net=s.expense-s.refund,diff=s.income-net;
  $('metrics').classList.toggle('fh-daily-metrics',tab==='overview');
  if(tab==='overview'){
    const daily=summary(monthRows().filter(r=>r.track==='daily')),month=$('month').value;
    $('metrics').innerHTML=`<article class="metric fh-month-spending"><div class="metric-label">${month==='all'?'累计已支出':'本月已支出'}</div><div class="metric-value"><small>¥</small>${money(daily.expense-daily.refund)}</div><div class="metric-note"><span>日常支出 ${money(daily.expense)} − 退款 ${money(daily.refund)}</span><span>按所选时间计算，不随明细筛选改变。</span></div></article><article id="fh-overview-budget" class="fh-overview-budget" aria-label="本月生活预算"><h2>日常预算，还剩多少</h2><p class="fh-caption">正在读取本月预算…</p></article>`;
  }else{
  $('metrics').innerHTML=window.financeHub?.stockMetrics()||[['收支差额',diff,'已归类收入 − 净支出；不等于账户余额'],['已归类收入',s.income,rs.filter(r=>r.type==='income').length+' 笔收入'],['已归类净支出',net,`支出 ${money(s.expense)} − 退款 ${money(s.refund)}`]].map(v=>`<article class="metric"><div class="metric-label">${v[0]}</div><div class="metric-value"><small>¥</small>${money(v[1])}</div><div class="metric-note">${v[2]}</div></article>`).join('');
  }
  renderFinanceMetadata();renderFinanceChart(rs);renderFinanceCategories(rs);renderFinanceBudgets();renderRows();window.financeHub?.render();
}
function renderFinanceChart(rs){
  const month=$('month').value,aggregate=month==='all',keys=aggregate?[...new Set(rs.map(r=>r.date.slice(0,7)))].sort():Array.from({length:new Date(Number(month.slice(0,4)),Number(month.slice(5,7)),0).getDate()},(_,i)=>month+'-'+String(i+1).padStart(2,'0'));
  const totals=new Map(keys.map(k=>[k,0]));for(const r of rs){if(r.confirmed!==false&&(r.type==='expense'||r.type==='refund')){const k=aggregate?r.date.slice(0,7):r.date;totals.set(k,(totals.get(k)||0)+(r.type==='refund'?-r.cents:r.cents));}}
  $('finance-chart-title').textContent=aggregate?'每月净支出':'每日净支出';
  if(!keys.length){$('chart').innerHTML='<p class="finance-empty">这个范围还没有记录。</p>';return;}
  const values=keys.map(k=>totals.get(k)||0),high=Math.max(...values,100),low=Math.min(...values,0),range=high-low,zero=20+high/range*120,step=460/keys.length,width=Math.max(1,Math.min(12,step*.6)),labelStep=Math.max(1,Math.ceil(keys.length/7));
  $('chart').innerHTML=`<svg viewBox="0 0 520 180" role="img" aria-label="${aggregate?'每月':'每日'}净支出，支出减去退款"><path d="M45 ${zero}H510" stroke="#cbd4e2"/><g fill="#718096" font-size="9"><text x="0" y="24">${hiddenMoney?'••':Math.round(high/100)}</text><text x="0" y="144">${hiddenMoney?'••':Math.round(low/100)}</text></g>${values.map((v,i)=>{const y=20+(high-v)/range*120;return `<rect x="${47+i*step}" y="${Math.min(y,zero)}" width="${width}" height="${Math.max(Math.abs(y-zero),1)}" rx="2" fill="${v<0?'#a69add':v?'#7198ed':'#e3e9f2'}"><title>${escape(keys[i])}：${money(v)} 元</title></rect>`;}).join('')}<g font-size="9" fill="#718096">${keys.map((k,i)=>i%labelStep===0?`<text x="${45+i*step}" y="166">${aggregate?escape(k):Number(k.slice(-2))+'日'}</text>`:'').join('')}</g></svg>`;
}
function renderFinanceCategories(rs){
  const grouped=new Map();for(const r of rs)if(r.confirmed!==false&&(r.type==='expense'||r.type==='refund'))grouped.set(r.category,(grouped.get(r.category)||0)+(r.type==='refund'?-r.cents:r.cents));
  const groups=[...grouped].sort((a,b)=>b[1]-a[1]),max=Math.max(...groups.map(g=>Math.abs(g[1])),1);
  $('categories').innerHTML=groups.length?'<div class="finance-category-bars">'+groups.map(([name,value],i)=>`<div class="finance-category-row"><div><span>${escape(name)}</span><b>¥ ${money(value)}</b></div><div class="bar"><span style="width:${Math.abs(value)/max*100}%;background:${financeColors[i%financeColors.length]}"></span></div></div>`).join('')+'</div><p class="finance-caption">按支出减退款统计；负数表示该分类退款多于支出。</p>':'<p class="finance-empty">当前范围没有已归类的支出或退款。</p>';
}
function renderFinanceBudgets(){
  const month=$('month').value,category=$('finance-category').value;
  if(month==='all'){$('budget-list').innerHTML='<p class="finance-empty">请选择一个月份，查看该月分类预算。</p>';return;}
  const list=financeBudgets.filter(b=>b.month===month&&(!category||b.category===category));
  $('budget-list').innerHTML=list.length?list.map((b,i)=>{const s=summary(records.filter(r=>r.date.startsWith(month)&&r.category===b.category)),spent=s.expense-s.refund;return `<div class="budget-row"><div class="budget-label"><span>${escape(b.category)}</span><span>¥ ${money(spent)} <span class="muted">/ ${money(b.cents)}</span></span></div><div class="bar"><span style="width:${b.cents?Math.min(100,Math.max(0,spent/b.cents*100)):0}%;background:${financeColors[i%financeColors.length]}"></span></div></div>`;}).join(''):'<p class="finance-empty">这个月还没有设置预算。</p>';
}
function renderRows(){
  const list=filtered(),pages=Math.max(1,Math.ceil(list.length/FINANCE_PAGE_SIZE));financePage=Math.max(1,Math.min(financePage,pages));const first=(financePage-1)*FINANCE_PAGE_SIZE,visible=list.slice(first,first+FINANCE_PAGE_SIZE);
  $('count').textContent=`${list.length} 笔记录`;$('finance-page-label').textContent=list.length?`${first+1}—${first+visible.length} / ${list.length} 笔 · 第 ${financePage} / ${pages} 页`:'没有记录';$('finance-prev').disabled=financePage===1;$('finance-next').disabled=financePage===pages;
  $('rows').innerHTML=visible.length?visible.map(r=>`<tr><td><div class="transaction-name"><div>${escape(r.name)}<small>${escape(r.date)}${r.note?' · '+escape(r.note):''}</small></div></div></td><td><span class="finance-kind finance-kind-${r.type}">${financeTypes[r.type]}</span>${['review','transfer','financing','excluded'].includes(r.type)?`<small class="finance-reason">${escape(r.reason||'不计入收支')}</small>`:''}</td><td><span class="tag">${escape(r.category)}</span>${r.subcategory?`<small class="finance-subcategory">${escape(r.subcategory)}</small>`:''}</td><td>${escape(window.financeHub?.accountName(r.account_id)||r.account||'尚未核对')}<small class="finance-subcategory">${escape(r.channel||'')}${r.to_account_id?' → '+escape(window.financeHub?.accountName(r.to_account_id)||'转入账户'):''}</small></td><td class="amount money ${r.type==='income'||r.type==='refund'?'income':''}">${financeAmount(r)}</td><td class="edit-cell"><button type="button" data-edit="${escape(r.id)}" aria-label="编辑 ${escape(r.name)}">编辑</button></td></tr>`).join(''):'<tr><td colspan="6" class="finance-empty">没有找到记录，试试调整月份或筛选条件。</td></tr>';
  $('rows').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(b.dataset.edit));
}
function showPage(p){
  const names={todos:'待办',finance:'泉库',today:'今日',work:'工作',notes:'笔记',sport:'运动',health:'健康',travel:'旅行',treasures:'宝物',weather:'天气',people:'人际',recipes:'菜谱'};
  const pages=Object.keys(names);
  if(!pages.includes(p)||!$(p))p='today';
  const changed=page!==p;document.body.dataset.view=p;if(changed)window.scrollTo(0,0);page=p;
  document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===p));
  for(const id of pages){const section=$(id);if(section)section.hidden=p!==id;}
  $('placeholder').hidden=pages.includes(p);$('work-tabs').hidden=p!=='work';$('finance-subnav').hidden=p!=='finance';document.querySelector('.page-heading').hidden=p!=='today';
  $('crumb').textContent=names[p];$('title').textContent=p==='finance'?'泉库，一目了然。':p==='today'?'今天，你好。':names[p];$('subtitle').textContent=p==='finance'?'收支有数，生活有余地。':'日有所记，心有所安。';$('placeholder-title').textContent=names[p]+' · 待开放';$('privacy').hidden=p!=='finance';
  if(p==='finance'&&(!financeReady||changed))loadFinance();

  if(p==='todos'&&typeof loadTodos==='function')loadTodos();
  if(['sport','health'].includes(p)&&typeof loadWellness==='function')loadWellness();
  if(p==='work'&&typeof loadWork==='function')loadWork();
  if(p==='notes'&&typeof loadNotes==='function')loadNotes();
  if(p==='travel'&&typeof loadTravel==='function')loadTravel();
  if(p==='treasures')window.loadTreasures?.();
  if(p==='weather')window.loadWeather?.();
  if(p==='people')window.loadPeople?.();
  if(p==='recipes')window.loadRecipes?.();
}
// V40: keep the original controls and event handlers while grouping ledger filters with the table.
const financeTransactionPanel=$('finance-transactions');
for(const node of [document.querySelector('#finance .finance-filters'),$('finance-scope-note')]){
  if(node&&node.parentNode!==financeTransactionPanel)financeTransactionPanel.insertBefore(node,$('count'));
}
function showTab(t){
  tab=t;showPage('finance');document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===t));
  const ledger=['overview','daily','side','transactions'].includes(t);
  if(!ledger&&$('month').value==='all')$('month').value=financeToday().slice(0,7);
  const allOption=Array.from($('month').options||[]).find(o=>o.value==='all');if(allOption)allOption.disabled=!ledger;
  $('overview').hidden=!['overview','side'].includes(t);$('budget').hidden=t!=='budget';$('finance-transactions').hidden=!['daily','side','transactions'].includes(t);
  document.querySelector('#finance .finance-filters').hidden=!ledger||t==='overview';$('finance-scope-note').hidden=!ledger||t==='overview';$('add').hidden=!ledger;
  $('table-title').textContent=t==='transactions'?'全部账目':t==='side'?'这项事业的流水':'生活账目明细';
  for(const key of ['side','debts','accounts','wishes'])$('fh-'+key).hidden=t!==key;
  financePage=1;render();window.financeHub?.tabChanged(t);
}

function edit(id){
  if(!financeReady||financeSaving)return;if(window.financeHub&&!window.financeHub.isReady()){toast('账户资料尚未读取，请稍后重试。');return;}const r=records.find(r=>r.id===id);if(r?.payment_id){toast('请在负债页面修改或撤销这笔还款，避免重复记账。');return;}if(id&&!r){toast('这笔记录暂时找不到，请刷新账本。');return;}
  $('form').reset();financeEdit=r?{...r}:{id:uid(),revision:0};financeDirty=false;
  $('record-id').value=financeEdit.id;$('editor-title').textContent=r?'编辑账目':'记一笔';$('entry-type').value=r?.type||'expense';$('entry-direction').value=r?financeDirection(r):'unknown';updateFinanceDirection();$('entry-amount').value=r?(r.cents/100).toFixed(2):'';$('entry-name').value=r?.name||'';$('entry-date').value=r?.date||financeToday();$('entry-category').value=r?.category||'';$('entry-subcategory').value=r?.subcategory||'';$('entry-account').value=r?.account||'';$('entry-note').value=r?.note||'';
  const imported=r&&r.source!=='manual',origin=imported?financeImports.find(item=>item.sha256===r.source):null;
  $('finance-entry-source').textContent=r?(imported?`来自 ${origin?.filename||'历史账本'}${r.source_row!=null?' · 原表第 '+r.source_row+' 行':''}${r.groups?.length?' · '+r.groups.join('、'):''}。原记录会保留。`:'本人记录。修改前的内容会保留。'):'保存后，其他设备也能看到这笔账目。';
  $('finance-entry-reason').textContent=r?.reason||'';$('form-error').textContent='';$('finance-reload-entry').hidden=true;window.financeHub?.prepareEntry(r);$('editor').showModal();
}
async function closeFinanceEditor(){if(financeSaving)return;if(financeDirty&&!await window.tiangongConfirm('这笔账目还有未保存的修改。放弃修改并关闭？'))return;$('editor').close();financeEdit=null;financeDirty=false;}
$('form').addEventListener('input',()=>{financeDirty=true;});
$('entry-type').onchange=()=>{if(!['income','expense','refund'].includes($('entry-type').value))$('entry-direction').value='unknown';updateFinanceDirection();window.financeHub?.entryTypeChanged();financeDirty=true;};
$('editor').addEventListener('cancel',event=>{event.preventDefault();closeFinanceEditor();});
$('form').onsubmit=async event=>{
  event.preventDefault();if(financeSaving||!financeEdit)return;if(financeLoading){$('form-error').textContent='正在刷新账本，请稍后再保存。';return;}
  const type=$('entry-type').value,cents=parseCents($('entry-amount').value.trim(),['excluded','review'].includes(type)),name=$('entry-name').value.trim(),category=$('entry-category').value.trim(),date=$('entry-date').value;
  if(cents===null||!name||!category||!/^\d{4}-\d{2}-\d{2}$/.test(date)){$('form-error').textContent='请填写名称、日期、分类及最多两位小数的金额；仅待核对或已排除记录可以为0。';return;}
  const direction=cents===0?'zero':type==='income'||type==='refund'?'in':type==='expense'?'out':$('entry-direction').value;
  if(cents>0&&direction==='zero'){$('form-error').textContent='金额已不为0，请重新选择流入、流出或方向未定。';return;}
  const payload={id:financeEdit.id,revision:financeEdit.revision,date,name,cents,type,direction,category,subcategory:$('entry-subcategory').value.trim(),account:$('entry-account').value.trim(),note:$('entry-note').value.trim()};
  try{Object.assign(payload,window.financeHub?.entryFields()||{});const fingerprint=JSON.stringify(payload);if(!financeEdit.requestId||financeEdit.requestFingerprint!==fingerprint)financeEdit.requestId=uid();financeEdit.requestFingerprint=fingerprint;payload.request_id=financeEdit.requestId;}catch(e){$('form-error').textContent=e.message;return;}
  financeSaving=true;$('form-error').textContent='';$('form').querySelectorAll('input,select,textarea,button').forEach(input=>{input.disabled=true;});
  try{const {entry}=await financeRequest(payload);if(!entry||entry.id!==payload.id)throw new Error('保存结果未确认，请保留内容并稍后重试。');const isNew=financeEdit.revision===0;records=records.filter(r=>r.id!==entry.id).concat(entry);if(isNew){$('search').value='';$('type').value='all';$('finance-category').value='';$('finance-group').value='';}financeOptions(entry.date.slice(0,7));financePage=1;financeDirty=false;financeEdit=null;render();$('editor').close();toast('已保存到账本');window.financeHub?.load(true);}
  catch(e){$('form-error').textContent=e.message;$('finance-reload-entry').hidden=e.code!==409;}
  finally{financeSaving=false;$('form').querySelectorAll('input,select,textarea,button').forEach(input=>{input.disabled=false;});}
};
$('finance-reload-entry').onclick=async()=>{if(financeSaving||!financeEdit)return;if(financeDirty&&!await window.tiangongConfirm('重新读取会替换当前未保存的修改。继续？'))return;const id=financeEdit.id;if(await loadFinance()){if(records.some(r=>r.id===id)){edit(id);}else{$('form-error').textContent='未找到最新账目，当前内容仍保留。';}}};
$('add').onclick=()=>edit();$('close').onclick=closeFinanceEditor;$('finance-refresh').onclick=()=>loadFinance();
$('privacy').onclick=()=>{hiddenMoney=!hiddenMoney;$('privacy').setAttribute('aria-label',hiddenMoney?'显示金额':'隐藏金额');render();};
for(const id of ['month','type','finance-category','finance-group'])$(id).onchange=()=>{financePage=1;render();if(id==='month')window.financeHub?.load(true);};
$('search').oninput=()=>{financePage=1;render();};
$('finance-clear').onclick=()=>{$('search').value='';$('type').value='all';$('finance-category').value='';$('finance-group').value='';financePage=1;render();};
$('finance-prev').onclick=()=>{financePage--;renderRows();};$('finance-next').onclick=()=>{financePage++;renderRows();};
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>showPage(b.dataset.page));document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));$('back').onclick=()=>showPage('finance');$('to-todos').onclick=()=>{history.replaceState(null,'','#todos');showPage('todos');};
function csvCell(v){let s=String(v??'');if(/^[=+@\-\t\r]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
$('export').onclick=()=>{if(!financeReady)return;const rows=[['日期','名称','类型','分类','二级分类','原账户描述','付款渠道','实际出入账账户','转入账户','归属','预算类别','金额（元）','资金方向','备注','原表分组','分类说明'],...filtered().map(r=>[r.date,r.name,financeTypes[r.type],r.category,r.subcategory,r.account,r.channel,window.financeHub?.accountName(r.account_id)||'',window.financeHub?.accountName(r.to_account_id)||'',window.financeHub?.trackName(r.track)||'',r.budget_category,(r.cents/100).toFixed(2),{in:'流入',out:'流出',zero:'零金额',unknown:'方向未定'}[financeDirection(r)],r.note,(r.groups||[]).join('、'),r.reason])];const blob=new Blob(['\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8;'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`天工录_账目_${$('month').value}_${Date.now()}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出全部筛选结果');};
render();

// Navigation preference belongs to this browser; page data remains shared.
function setNavigation(open){
 document.body.classList.toggle('nav-collapsed',!open);
 $('main-navigation').inert=!open;
 $('nav-toggle').setAttribute('aria-expanded',String(open));
 $('nav-toggle').setAttribute('aria-label',open?'收起导航':'展开导航');
 try{localStorage.setItem((window.tiangongAccount?.storageKey('tiangong-navigation-open')||'tiangong-navigation-open'),String(open));}catch(e){}
}
$('nav-toggle').onclick=()=>setNavigation(document.body.classList.contains('nav-collapsed'));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]')){setNavigation(false);$('nav-toggle').focus();}});
try{setNavigation((window.tiangongAccount?.user?.settings?.navigation_open ?? (localStorage.getItem((window.tiangongAccount?.storageKey('tiangong-navigation-open')||'tiangong-navigation-open'))==='true')));}catch(e){setNavigation(false);}

document.addEventListener('click',e=>{
 if(!document.body.classList.contains('nav-collapsed')&&!e.target.closest('#main-navigation')&&!e.target.closest('#nav-toggle'))setNavigation(false);
});
async function loadDailyQuote(){
 try{const r=await fetch('/api/daily-quote',{cache:'no-store'});if(!r.ok)return;const q=await r.json();$('daily-quote-text').textContent=q.text;$('daily-quote-source').textContent=q.source==='山君'?'山君 · '+q.date:q.date;}catch(e){}
}
loadDailyQuote();
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadDailyQuote();});
setInterval(loadDailyQuote,60000);

window.addEventListener("tiangong:settings-error",event=>toast(event.detail?.message||"设置尚未保存，请稍后再试"));
