'use strict';
(() => {
  const tracks={daily:'日常流水',fudai:'个人工作',smart_home:'全屋智能',zhejiang_tv:'浙江广电数据维护',stocks:'股市',qqsg:'QQ 三国',unknown:'待确认归属'};
  const kinds={mortgage:'房贷 · 等额本息',interest_only:'消费贷 · 先息后本',short_term:'短期借款',credit_card:'信用卡',personal:'亲友借款'};
  const accountKinds={bank:'银行卡',e_wallet:'支付账户余额',cash:'现金',investment:'投资账户',other:'其他资产'};
  const pageCopy={daily:['生活的每一笔，都记在这里。','消费、工资、人情往来，按日期查阅。'],overview:['把日常，过得有数。','消费、工资、人情往来，记在这里。'],side:['每一份努力，都有自己的账。','副业、股市和游戏往来分开看，同一笔流水只记一次。'],debts:['一笔一笔，离轻松更近。','每一种借款都有自己的约定，记录实际发生的归还。'],accounts:['钱在哪里，一目了然。','付款渠道和扣款账户分开记录，余额跟着实际资金走。'],wishes:['想要的，慢慢靠近。','先还清负债，再用存款的 10% 奖励自己。'],budget:['给生活，留足能量。','餐饮、日用、娱乐三项预算，剩余额度像血条一样看得见。'],transactions:['每一笔，都有来处。','全部原始流水、分类与核对记录保留在一起。']};
  let state=null,side='fudai',loading=null,loadGeneration=0,editState=null,saving=false,dirty=false,suggestion=null;
  const e=escape, yuan=v=>v==null?'待核对':'¥ '+money(v), date=v=>v||'待补充', checked=v=>v?' checked':'', selected=(a,b)=>a===b?' selected':'';
  const htmlOptions=(map,value)=>Object.entries(map).map(([k,n])=>`<option value="${e(k)}"${selected(k,value)}>${e(n)}</option>`).join('');
  const currentMonth=()=>/^\d{4}-\d{2}$/.test($('month').value)?$('month').value:financeToday().slice(0,7);
  const accountName=id=>state?.accounts.find(a=>a.id===id)?.name||'';
  const stat=(label,value,note,action='')=>`<article class="fh-stat"><small>${e(label)}</small><strong>${value}</strong><p>${action?`<button type="button" class="fh-review-link" data-fh="${action}">${e(note)}</button>`:e(note)}</p></article>`;
  const note=text=>`<p class="fh-note">${e(text)}</p>`;
  const empty=text=>`<div class="fh-empty">${e(text)}</div>`;
  const source=row=>row.source_note?`<details class="fh-source"><summary>来源与备注</summary>${e(row.source_note)}</details>`:'';
  const button=(label,action,id='',primary=false)=>`<button type="button" class="${primary?'primary':'fh-button'}" data-fh="${action}" data-id="${e(id)}">${e(label)}</button>`;
  const field=(name,label,value='',type='text',opts='')=>`<label>${e(label)}<input name="${name}" type="${type}" value="${e(value??'')}" ${opts}></label>`;
  const amount=(name,label,value=null,required=false)=>field(name,label,value==null?'':(value/100).toFixed(2),'text',`inputmode="decimal" ${required?'required':''} placeholder="${required?'0.00':'不清楚可留空'}"`);
  const choose=(name,label,map,value)=>`<label>${e(label)}<select name="${name}">${htmlOptions(map,value)}</select></label>`;
  const checkbox=(name,label,value=false)=>`<label class="wide fh-check"><input type="checkbox" name="${name}"${checked(value)}><span>${e(label)}</span></label>`;
  const sourceField=value=>`<label class="wide">来源与备注<textarea name="source_note" rows="3" maxlength="3000">${e(value||'')}</textarea></label>`;
  const accountField=value=>choose('account_id','实际扣款账户',{'':'尚未核对',...Object.fromEntries((state?.accounts||[]).filter(a=>['bank','e_wallet','cash'].includes(a.kind)).map(a=>[a.id,a.name]))},value||'');
  const channelField=value=>choose('channel','付款渠道',{'':'未填写','微信支付':'微信支付','支付宝':'支付宝','银行卡':'银行卡','现金':'现金','其他':'其他'},value||'');
  async function request(payload,month=currentMonth()){
    const response=await (payload?fetch:(window.tiangongFinanceReadFetch||fetch))(payload?'/api/finance/manage':'/api/finance/hub?month='+encodeURIComponent(month),payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{cache:'no-store'});
    let body;try{body=await response.json();}catch{throw new Error('泉库资料暂时无法读取，请稍后重试。');}
    if(!response.ok)throw Object.assign(new Error(body.error||'保存尚未成功，请稍后重试。'),{code:response.status});return body;
  }
  async function load(force=false){
    const month=currentMonth();if(loading?.month===month&&!force)return loading.promise;
    const generation=++loadGeneration;$('fh-status').textContent='正在核对账户与负债…';$('fh-status').classList.remove('error');
    const promise=(async()=>{try{
      const data=await request();if(generation!==loadGeneration)return false;
      if(!['accounts','debts','payments','wishes','budgets'].every(k=>Array.isArray(data[k])))throw new Error('泉库资料格式异常，请刷新后再试。');
      state=data;includeDebtMonths();suggestion=null;$('fh-status').textContent='';render();if(tab==='budget')await loadSuggestion();return true;
    }catch(error){if(generation===loadGeneration){$('fh-status').textContent=error.message;$('fh-status').classList.add('error');}return false;}
    finally{if(generation===loadGeneration)loading=null;}})();loading={month,promise};return promise;
  }
  function includeDebtMonths(){
    const select=$('month'),existing=new Set(Array.from(select.options).map(option=>option.value)),months=new Set();
    for(const d of [...state.debts,...(state.archived_debts||[])])for(const value of [d.as_of,d.borrowed_on,d.closed_at,d.archived_from,...(d.snapshots||[]).map(snapshot=>snapshot.as_of)])if(value)months.add(value.slice(0,7));
    for(const p of state.payments)months.add(p.date.slice(0,7));
    for(const s of state.card_statements||[])for(const value of [s.consumption_month,s.due_month])months.add(value);
    for(const month of [...months].filter(value=>/^\d{4}-\d{2}$/.test(value)).sort().reverse())if(!existing.has(month))select.add(new Option(month.replace('-',' 年 ')+' 月',month));
  }
  function stockFlow(rows){
    const flows=rows.filter(r=>r.track==='stocks'&&r.confirmed!==false&&!r.voidedAt&&['financing','transfer'].includes(r.type));
    return {incoming:flows.filter(r=>financeDirection(r)==='in').reduce((sum,r)=>sum+r.cents,0),outgoing:flows.filter(r=>financeDirection(r)==='out').reduce((sum,r)=>sum+r.cents,0),unknown:flows.filter(r=>financeDirection(r)==='unknown').length};
  }
  function stockHistory(){
    const {incoming,outgoing,unknown}=stockFlow(records),net=incoming-outgoing;
    return `<section class="fh-stock-history"><div><strong>${net>=0?'累计已回款超过投入':'累计尚未转回的投入'} ${yuan(Math.abs(net))}</strong><p>全部记录 · 转出 ${yuan(outgoing)}，转回 ${yuan(incoming)}。资金净回流；持仓收益另行核对。${unknown?'另有 '+unknown+' 笔资金方向待核对。':''}</p></div>${$('month').value!=='all'?button('查看全部股票记录','stock-all'):''}</section>`;
  }
  function stockMetrics(){
    if(tab!=='side'||side!=='stocks')return null;
    const {incoming,outgoing,unknown}=stockFlow(monthRows());
    return [['资金净回流',incoming-outgoing,'转回 − 转出；真实投资收益待核对'],['证券资金转出',outgoing,'投入证券账户的资金'],['证券资金转回',incoming,'从证券账户转回的资金'+(unknown?'；另有 '+unknown+' 笔方向待核对':'')]].map(([label,value,caption])=>`<article class="metric"><div class="metric-label">${e(label)}</div><div class="metric-value"><small>¥</small>${money(value)}</div><div class="metric-note">${e(caption)}</div></article>`).join('');
  }
  function qqGroups(){
    const groups=new Map();for(const row of monthRows().filter(r=>r.track==='qqsg'&&!r.voidedAt)){const label=row.subcategory||'尚未分项';if(!groups.has(label))groups.set(label,[]);groups.get(label).push(row);}
    const order=['截图确认购币','腾讯QQ充值','游戏商家转账（用途待核）','代练','查询服务'];
    return [...groups].sort(([a],[b])=>(order.includes(a)?order.indexOf(a):99)-(order.includes(b)?order.indexOf(b):99)||a.localeCompare(b,'zh-CN'));
  }
  function renderQQGroups(){
    const groups=qqGroups();if(!groups.length)return empty('这个时间范围还没有 QQ 三国分项记录。');
    return `<section class="fh-qq-breakdown" aria-label="QQ 三国分项核对"><header><h3>QQ 三国 · 分项核对</h3><p>${$('month').value==='all'?'全部时间':e($('month').value)} · 点击卡片查看原始明细，也可以修改归属。</p></header><div class="fh-qq-groups">${groups.map(([label,rows],index)=>{const totals=summary(rows);return `<button type="button" data-fh="qq-group" data-id="${index}"><span>${e(label)}</span><strong>${yuan(totals.expense-totals.refund)}</strong><small>${rows.length} 笔 · 支出减退款${totals.income?' · 另有收入 '+yuan(totals.income):''}</small></button>`;}).join('')}</div></section>`;
  }
  function renderSide(){
    const previousScroll=$('fh-side').querySelector('.fh-business-list')?.scrollLeft||0;
    const icons={fudai:'M5 8h14v12H5z M4 4h16v4H4z M12 4v16',smart_home:'m3 10 9-7 9 7 M5 9v12h14V9 M10 21v-7h4v7',zhejiang_tv:'M4 5h16v12H4z M8 21h8 M12 17v4',stocks:'M4 20V4 M4 20h16 M7 14l4-4 4 2 5-7',qqsg:'M7 8h10a4 4 0 0 1 4 4l1 5a2 2 0 0 1-3 2l-3-3H8l-3 3a2 2 0 0 1-3-2l1-5a4 4 0 0 1 4-4 M6 10v4 M4 12h4 M16 11h.1 M18 13h.1'};
    const selectedCopy={fudai:'个人工作项目的收入、成本和往来。',smart_home:'全屋智能项目的收入与支出，统一归在这里。',zhejiang_tv:'浙江广电数据维护的项目收入与支出，按月分别记录。',stocks:'这里统计资金转出与转回，不把资金净回流当作投资收益。',qqsg:'经营成本按原账分项核对；用途待核的商家转账保留原记录。'};
    $('fh-side').innerHTML=`<div class="fh-track-list fh-business-list" aria-label="副业与投资分类">${Object.entries(tracks).filter(([k])=>!['daily','unknown'].includes(k)).map(([key,label])=>{
      const rows=monthRows().filter(r=>r.track===key),totals=summary(rows),stock=key==='stocks'?stockFlow(rows):null,incoming=stock?stock.incoming:totals.income,outgoing=stock?stock.outgoing:totals.expense-totals.refund,pending=rows.filter(r=>r.confirmed===false||r.type==='review').length;
      return `<button type="button" data-fh="track" data-id="${key}" class="fh-business-card ${key===side?'selected':''}" aria-pressed="${key===side}"><span class="fh-business-top"><span class="fh-business-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[key]}"/></svg></span><small>${rows.length} 笔${pending?' · '+pending+' 待核':''}</small></span><strong>${e(label)}</strong><span class="fh-business-value">${yuan(incoming-outgoing)}</span><span class="fh-business-caption">${stock?'资金净回流':'收支差额'}</span><span class="fh-business-split"><span>${stock?'转回':'收入'}<b>${yuan(incoming)}</b></span><span>${stock?'转出':'净支出'}<b>${yuan(outgoing)}</b></span></span><span class="fh-business-open">${key===side?'正在查看':'查看明细'}</span></button>`;
    }).join('')}</div><div class="fh-business-detail-heading"><div><h2>${e(tracks[side])}</h2><p>${e(selectedCopy[side])}</p></div><span>${$('month').value==='all'?'全部时间':e($('month').value)}</span></div>${side==='stocks'?stockHistory():side==='qqsg'?renderQQGroups():''}`;
    $('fh-side').querySelector('.fh-business-list').scrollLeft=previousScroll;
  }
  const allDebts=()=>[...(state?.debts||[]),...(state?.archived_debts||[])];
  const monthOffset=(month,delta)=>{const [year,part]=month.split('-').map(Number),value=new Date(Date.UTC(year,part-1+delta,1));return value.toISOString().slice(0,7);};
  const details=rows=>`<dl class="fh-detail">${rows.map(([label,value])=>`<dt>${e(label)}</dt><dd>${value}</dd>`).join('')}</dl>`;
  const debtBaseline=d=>d.remaining_principal_cents==null&&d.snapshot_principal_cents!=null?`<small>历史记录 ${d.kind==='credit_card'&&d.snapshot_card_balance_cents>0?'溢缴款 +'+yuan(d.snapshot_card_balance_cents):yuan(d.snapshot_principal_cents)} · ${d.snapshot_as_of?e(d.snapshot_as_of):'日期待核实'}</small>`:'';
  function statementRow(s){
    return `<div class="fh-statement" data-statement-id="${e(s.id)}"><div><strong>${e(s.consumption_month)} 消费账单</strong><p>${e(s.due_month)} 应还 ${yuan(s.amount_cents)} · 剩余 ${yuan(s.remaining_cents)}</p>${s.credit_applied_cents?`<p>卡内余额抵扣 ${yuan(s.credit_applied_cents)} · 非本次实还</p>`:''}</div><div class="fh-actions">${s.remaining_cents>0?button('记录这期还款','statement-pay',s.id):`<span class="fh-badge">✓ 这期${s.credit_applied_cents?'已结清':'已还清'}</span>`}${button('修改账单','statement-edit',s.id)}</div></div>`;
  }
  function debtCard(d,archived=false){
    let rows=[],status='',actions='',extra='';
    if(d.kind==='personal'){
      rows=[['已记录归还',yuan(d.total_principal_paid_cents)],['借款约定','无息'],['欠款核对日期',e(date(d.snapshot_as_of||d.as_of))]];
      status=d.remaining_principal_cents===0?'<span class="fh-badge">✓ 已还清</span>':'';
      actions=button('记录还款','payment-new',d.id,true);
    }else if(d.kind==='short_term'){
      const noDebt=d.remaining_principal_cents===0;
      if(noDebt){
        rows=[['本金核对日期',e(date(d.snapshot_as_of||d.as_of))]];
        const history=[];
        if(d.original_principal_cents!=null)history.push(['原借款金额',yuan(d.original_principal_cents)]);
        if(d.borrowed_on)history.push(['借款日期',e(d.borrowed_on)]);
        if(d.total_principal_paid_cents)history.push(['已记录归还本金',yuan(d.total_principal_paid_cents)]);
        if(d.settled_on)history.push(['还清日期',e(d.settled_on)]);
        if(d.duration_days!=null)history.push(['用款天数',e(d.duration_days)+' 天']);
        if(d.total_interest_paid_cents)history.push(['已记录实际利息',yuan(d.total_interest_paid_cents)]);
        if(d.total_fees_paid_cents)history.push(['已记录费用',yuan(d.total_fees_paid_cents)]);
        if(history.length)extra=`<details class="fh-statements"><summary>已记录的借还历史</summary>${details(history)}</details>`;
        status='<span class="fh-badge">✓ 已核对无欠款</span>';
        if(!d.settled_on)extra+='<p class="fh-caption">历史借款资料可选填，不影响本次零欠款核对。</p>';
      }else{
        rows=[['原借款金额',yuan(d.original_principal_cents)],['借款日期',e(date(d.borrowed_on))],['已记录归还本金',yuan(d.total_principal_paid_cents)],['还清日期',d.remaining_principal_cents==null?'待核对':'尚未还清'],['用款天数',d.duration_days==null?'待补齐借还日期':e(d.duration_days)+' 天'],['已记录实际利息',yuan(d.total_interest_paid_cents)]];
        if(d.total_fees_paid_cents)rows.push(['已记录费用',yuan(d.total_fees_paid_cents)]);
      }
      actions=button(noDebt?'补记历史还款':'记录还款','payment-new',d.id,!noDebt)+(d.remaining_principal_cents>0?button('记录还清','payment-settle',d.id):'');
    }else if(d.kind==='credit_card'){
      const bill=d.card_statement,statements=(state.card_statements||[]).filter(s=>s.debt_id===d.id).sort((a,b)=>b.consumption_month.localeCompare(a.consumption_month));
      if(bill){
        rows=[['消费账单月',e(bill.consumption_month)],['次月应还',e(bill.due_month)+' · '+yuan(bill.amount_cents)],['这期已还',yuan(bill.paid_cents)],['这期未还',yuan(bill.remaining_cents)]];
        if(bill.credit_applied_cents)rows.splice(3,0,['卡内余额抵扣',yuan(bill.credit_applied_cents)]);
        status=`<span class="fh-badge ${bill.status==='paid'?'':'pending'}">${bill.credit_applied_cents?(bill.remaining_cents===0?'✓ 这期账单已结清':'已抵扣卡内余额 · 尚有待还'):({paid:'✓ 这期账单已还清',partial:'这期账单已还一部分',unpaid:'这期账单尚未还款'}[bill.status]||'账单待核对')}</span>`;
        actions=(bill.remaining_cents>0?button('记录这期还款','statement-pay',bill.id,true):'')+button('修改这期账单','statement-edit',bill.id);
      }else{extra=`<p class="fh-caption">${d.remaining_principal_cents!=null?'当前欠款已经核对。':''}${e(state.month)} 的应还账单尚未录入；它与当前总欠款分开记录。可补充 ${e(monthOffset(state.month,-1))} 消费账单，查看这一期应还金额。</p>`;}
      if(d.due_day)rows.push(['每期还款日','每月 '+e(d.due_day)+' 日']);
      actions+=button('＋ 新增每月账单','statement-new',d.id,!bill);
      if(statements.length)extra+=`<details class="fh-statements"><summary>全部账单 · ${statements.length} 期</summary>${statements.map(statementRow).join('')}</details>`;
      if(d.status==='closed')status+=`<span class="fh-badge">已注销${d.closed_at?' · '+e(d.closed_at):' · 日期未记'}</span>`;
    }else{
      const interest=d.kind==='interest_only',zeroBalance=d.payment_quote?.source==='checked_balance',interestReference=d.payment_quote?.scheduled_total_cents??d.monthly_payment_cents;
      rows=[['本金核对日期',e(date(d.snapshot_as_of||d.as_of))],[interest?'本月实付利息':'本月实还',interest&&d.linked_payment_cents?'扣款本息待拆分':yuan(interest?d.month_interest_cents:d.month_paid_cents)],[interest?'本月利息参考':'月供参考',(interest?interestReference:d.monthly_payment_cents)==null?'未填写':yuan(interest?interestReference:d.monthly_payment_cents)],['还款日',d.due_day?'每月 '+e(d.due_day)+' 日':'未填写'],['合同到期日',e(date(d.maturity))]];
      if(d.annual_rate_bp!=null)rows.push(['年利率',e((d.annual_rate_bp/100).toFixed(2))+'%']);
      if(d.kind==='mortgage'){if(d.original_principal_cents!=null)rows.push(['原借款金额',yuan(d.original_principal_cents)]);if(d.borrowed_on)rows.push(['借款日期',e(d.borrowed_on)]);}
      const paid=d.monthly_status==='paid'||(d.payment_quote?.status==='paid'&&!zeroBalance);
      status=`<span class="fh-badge ${paid?'':'pending'}">${zeroBalance?'本金已核为零 · 付款按原账':paid?(interest?'✓ 本月利息已付':'✓ 本月已还'):interest?'本月利息待核对':d.monthly_status==='recorded'?'已记还款 · 本月是否还完待确认':'本月尚未记录还款'}</span>`;
      actions=button(zeroBalance?'查看本金核对':interest?(paid?'查看本月利息付款':'本月利息已付'):(paid?'查看本月月供扣款':'记录本月月供'),'payment-month',d.id,true)+button(interest?'归还本金':'提前还本 / 其他还款','payment-new',d.id);
    }
    // Keep the first action within reach; secondary operations and full history remain expandable.
    const actionEnd=actions.indexOf('</button>')+9,mainAction=archived?'':actions.slice(0,actionEnd),otherActions=archived?'':actions.slice(actionEnd);
    const zero=d.remaining_principal_cents===0,unknown=d.remaining_principal_cents==null;
    const compactStatus=archived?'已归档':zero?'已核对无欠款':unknown?'本金待核对':d.kind==='credit_card'?(d.card_statement?.status==='paid'?'这期账单已还':d.card_statement?'按次月账单归还':'当前欠款已核对'):['mortgage','interest_only'].includes(d.kind)?(d.monthly_status==='paid'||d.payment_quote?.status==='paid'?(d.kind==='interest_only'?'本月利息已付':'本月已还'):'本月待记录'):'按实际归还';
    const quote=d.kind==='interest_only'?d.payment_quote?.scheduled_total_cents??d.monthly_payment_cents:d.kind==='mortgage'?d.monthly_payment_cents:d.kind==='credit_card'?d.card_statement?.remaining_cents:null;
    const quoteLabel=d.kind==='interest_only'?'月息参考':d.kind==='mortgage'?'月供参考':d.kind==='credit_card'?(d.card_statement?'这期待还':'本期账单'):'已记录归还';
    const quoteValue=d.kind==='credit_card'&&!d.card_statement?'尚未录入':yuan(['personal','short_term'].includes(d.kind)?d.total_principal_paid_cents:quote);
    const terms=d.kind==='personal'?'无息':d.kind==='short_term'?(d.borrowed_on?'借于 '+e(d.borrowed_on):zero?'历史资料可选填':'按实际借还日期'):[d.annual_rate_bp!=null?'年利率 '+e((d.annual_rate_bp/100).toFixed(2))+'%':'',d.due_day?'每月 '+e(d.due_day)+' 日':''].filter(Boolean).join(' · ');
    return `<article class="fh-card fh-debt-card" data-debt-id="${e(d.id)}" data-debt-kind="${e(d.kind)}"><div class="fh-debt-card-title"><small>${e(kinds[d.kind]||d.kind)}</small><h3>${e(d.name)}</h3></div><div class="fh-debt-balance"><small>${d.kind==='credit_card'?'当前欠款':d.kind==='personal'?'还欠多少':'剩余本金'}</small><div class="fh-value">${yuan(d.remaining_principal_cents)}</div>${debtBaseline(d)}</div><div class="fh-debt-at-a-glance"><div><span>${quoteLabel}</span><strong>${quoteValue}</strong></div><small>${terms||'还款安排可补充'}</small></div><span class="fh-debt-state ${unknown?'pending':''}">${e(compactStatus)}</span><div class="fh-debt-main-actions">${mainAction}${button('编辑资料','debt-edit',d.id)}</div><details class="fh-debt-details"><summary>资料与记录</summary>${details(rows)}${status}${d.kind==='credit_card'&&d.credit_balance_cents>0?`<div class="fh-card-credit"><small>溢缴款 · 卡内可用余额</small><strong>+${yuan(d.credit_balance_cents)}</strong><p>不计入存款，也不抵减其他负债。</p></div>`:''}${extra}${archived?`<p class="fh-caption">从 ${e(d.archived_from||'')} 起归档；注销日期${d.closed_at?'为 '+e(d.closed_at):'尚未记录'}。</p>`:''}${otherActions?`<div class="fh-actions">${otherActions}</div>`:''}${source(d)}</details></article>`;
  }
  function debtDashboard(known,paid,pending){
    const debts=allDebts(),active=debts.filter(d=>d.remaining_principal_cents!==0),principal=debts.reduce((n,d)=>n+(d.total_principal_paid_cents||0),0),interest=debts.reduce((n,d)=>n+(d.total_interest_paid_cents||0),0);
    const number=(label,value)=>`<div class="t44-debt-number"><small>${e(label)}</small><strong>${value}</strong></div>`;
    const forecast=(f,i)=>`<article class="t44-debt-month"><h3>${i?'下月还款参考':'本月待还参考'} <small>${e(f.month)}</small></h3><strong>${yuan(f.known_total_cents)}</strong><p>${f.unknown_count?'已知部分 · '+f.unknown_count+' 笔账单或金额未齐':'按已录账单与合同参数'}<br>借款本金 ${yuan(f.principal_cents)} · 利息 ${yuan(f.interest_cents)}${f.unscheduled_count?'<br>'+f.unscheduled_count+' 笔短期／亲友借款按实际约定另记':''}</p><details><summary>查看 ${f.rows.length} 笔安排</summary><ul>${f.rows.map(r=>`<li><span>${e(r.name)}<br>${r.due_day?e(r.due_day)+' 日 · ':''}${e(r.note)}</span><span>${r.total_cents==null?'待补充':yuan(r.total_cents)}</span></li>`).join('')||'<li>暂无按月还款安排</li>'}</ul></details></article>`;
    const groups=[['high','高息','年利率 > 8%',d=>d.annual_rate_bp>800],['mid','中息','年利率 5%—8%',d=>d.annual_rate_bp>=500&&d.annual_rate_bp<=800],['low','低息／无息','年利率 < 5%',d=>d.kind==='personal'||d.annual_rate_bp!=null&&d.annual_rate_bp<500],['unknown','利率未记','按账单或合同核对',d=>d.kind!=='personal'&&d.annual_rate_bp==null]];
    return `<section class="t44-debt-dashboard"><div class="t44-debt-heading"><h2>债务看板</h2><span>${e(state.month)} · ${active.length} 项尚有欠款或待核对</span></div><div class="t44-debt-numbers">${number('已核实剩余欠款',yuan(known))}${number('本月已记录还款',yuan(paid))}${number('待核对金额',pending.length?button(pending.length+' 项，去核对','debt-review'):'0 项')}${number('累计已录归还本金',yuan(principal))}${number('累计已录实际利息',yuan(interest))}${number('当前无欠款',debts.filter(d=>d.remaining_principal_cents===0).length+' 项')}</div><div class="t44-debt-forecast">${(state.debt_forecasts||[]).map(forecast).join('')}</div><p class="fh-caption">还款参考不是银行账单：消费贷按当前本金与合同年利率估算月息，房贷按已录月供估算。未确定的到期本金、未来消费与浮动利率变化不纳入。累计值只统计已录记录。</p></section><div class="t44-rate-groups">${groups.map(([key,label,sub,filter])=>{const items=active.filter(filter);return `<article class="t44-rate-group ${key}"><h3>${label} · ${items.length} 项</h3><p>${sub}</p><details><summary>查看借款</summary>${items.map(d=>`<p>${e(d.name)}${d.annual_rate_bp!=null?' '+(d.annual_rate_bp/100).toFixed(2)+'%':''}<br>${yuan(d.remaining_principal_cents)}</p>`).join('')||'<p>暂无</p>'}</details></article>`;}).join('')}</div>`;
  }
  function renderDebts(){
    const debts=state.debts,archived=state.archived_debts||[],month=state.month,payments=state.payments.filter(p=>p.date.startsWith(month+'-')&&p.date<=state.as_of);
    const linkedEntries=new Map();for(const debt of allDebts())for(const link of debt.linked_payments||[])for(const id of link.entry_ids||[]){const row=records.find(r=>r.id===id&&r.date.startsWith(month+'-')&&r.date<=state.as_of);if(row&&!linkedEntries.has(id))linkedEntries.set(id,{row,debt});}
    const linked=[...linkedEntries.values()],paid=payments.filter(p=>!p.voidedAt).reduce((n,p)=>n+p.total_cents,0)+linked.reduce((sum,item)=>sum+item.row.cents,0);
    const pending=allDebts().filter(d=>d.remaining_principal_cents==null);
    const zeroOpen=$('fh-zero-debts')?.open,knownDebtSubtotal=allDebts().filter(d=>Number.isSafeInteger(d.remaining_principal_cents)).reduce((sum,d)=>sum+d.remaining_principal_cents,0);
    const expanded=new Set([...$('fh-debts').querySelectorAll('.fh-debt-details[open]')].map(el=>el.closest('[data-debt-id]').dataset.debtId));
    const positions=new Map([...$('fh-debts').querySelectorAll('.fh-debt-strip')].map(el=>[el.dataset.rail,el.scrollLeft]));
    const ongoing=debts.filter(d=>d.remaining_principal_cents!==0),cleared=debts.filter(d=>d.remaining_principal_cents===0);
    const rail=(items,key,archived=false)=>`<div class="fh-debt-strip" data-rail="${key}" tabindex="0" aria-label="${key==='ongoing'?'正在归还与待核对':key==='zero'?'当前无欠款':'已归档'}负债卡片，可左右滚动">${items.map(d=>debtCard(d,archived)).join('')}</div>`;
    $('fh-debts').innerHTML=`${debtDashboard(knownDebtSubtotal,paid,pending)}
    <details class="fh-debt-method"><summary>查看统计口径</summary><p>已核欠款小计包括已核对的归档欠款，不含待核部分，不等于完整负债。本月还款按实际付款日期汇总；完整未偿本金需核对本金、日期及相关流水。零欠款是有效的核对结果，不要求补齐旧借款资料。</p></details>
    <div class="fh-panel-heading fh-debt-section-heading"><div><h2>正在归还 <small>${ongoing.length} 项</small></h2><p class="fh-caption">${e(month)} · 信用卡看账单，借款看实际欠款与归还</p></div>${button('＋ 添加负债','debt-new','',true)}</div>
    ${ongoing.length?rail(ongoing,'ongoing'):empty('当前没有正在归还或待核对的借款。')}
    ${cleared.length?`<details id="fh-zero-debts" class="fh-panel fh-debt-group"${zeroOpen?' open':''}><summary>当前无欠款 · ${cleared.length} 项 <span>${cleared.map(d=>e(d.name)).join('、')}</span></summary>${rail(cleared,'zero')}</details>`:''}

    <section class="fh-panel"><div class="fh-panel-heading"><h3>本月还款流水</h3><p class="fh-caption">按实际还款日查看，关联的银行扣款只展示一次。</p></div>${linked.length?`<div class="fh-linked-payments">${linked.map(({row,debt})=>`<article><div><strong>${e(debt.name)} · ${yuan(row.cents)}</strong><p>${e(row.date)} · ${e(row.name)} · 已关联原账</p></div>${button('查看原账','payment-month',debt.id)}</article>`).join('')}</div>`:''}${payments.length?`<div class="fh-table"><table><thead><tr><th>日期 / 负债</th><th>本金</th><th>利息 / 费用</th><th>实际扣款</th><th>操作</th></tr></thead><tbody>${payments.map(p=>`<tr class="${p.voidedAt?'fh-payment-void':''}"><td>${e(p.date)}<br>${e(allDebts().find(d=>d.id===p.debt_id)?.name||'负债')}</td><td>${yuan(p.principal_cents)}</td><td>${yuan(p.interest_cents)} / ${yuan(p.fees_cents)}</td><td>${e(accountName(p.account_id)||'待核对')}${p.channel?'<br>'+e(p.channel):''}</td><td>${p.voidedAt?'已撤销':button('修改','payment-edit',p.id)+button('撤销','payment-void',p.id)}</td></tr>`).join('')}</tbody></table></div>`:linked.length?'':empty('还没有记录这个月的实际还款。')}</section>
    <section class="fh-panel"><label class="fh-check"><input id="fh-debt-complete" type="checkbox"${checked(state.settings.debt_inventory_confirmed)}><span>我已把所有尚未还清的负债加入清单（包括信用卡、短期借款和亲友借款）。</span></label><p class="fh-caption">用于心愿判断；没有确认完整清单时，不会误判为已经无债。</p></section>`;
    for(const el of $('fh-debts').querySelectorAll('.fh-debt-details'))el.open=expanded.has(el.closest('[data-debt-id]').dataset.debtId);
    for(const el of $('fh-debts').querySelectorAll('.fh-debt-strip'))el.scrollLeft=positions.get(el.dataset.rail)||0;
  }
  function renderAccounts(){
    const box=$('fh-accounts'),position=box.querySelector('.fh-compact-strip')?.scrollLeft||0,expanded=new Set([...box.querySelectorAll('.fh-compact-details[open]')].map(el=>el.closest('[data-account-id]').dataset.accountId)),methodOpen=!!box.querySelector('.fh-account-method[open]');
    box.innerHTML=`<div class="fh-stats fh-account-stats fh-compact-stats">${stat('已核余额小计',yuan(state.summary.confirmed_cash_subtotal_cents),'仅含已核账户；不等于完整存款')}${stat('完整可用现金',yuan(state.summary.confirmed_cash_cents),state.summary.confirmed_cash_cents==null?'尚有账户待核对':'全部现金账户均已核对')}${stat('资金账户',state.accounts.length+' 个','银行卡、钱包分别记账')}${stat('余额待核对',state.summary.accounts_pending_count+' 个',state.summary.accounts_pending_count?'补充余额、日期或资金账户':'基准及已记录流水均已核对')}</div>
    <details class="fh-account-method"${methodOpen?' open':''}><summary>余额如何计算</summary><p>已核余额小计仅加总已有核对结果的银行卡、现金和支付账户，不等于完整存款。完整可用现金不含投资、房产或信用卡溢缴款；尚有余额待核对时，心愿不会据此解锁。</p><p>例如用微信支付 30 元、实际扣招商银行卡：渠道记“微信支付”，账户选“招商银行卡”，只减少银行卡余额。账户之间转账不会计成收入或消费。</p></details>
    <div class="fh-panel-heading fh-compact-section-heading"><div><h2>钱放在哪里</h2><p class="fh-caption">${e(state.as_of)} · 余额随已记录流水计算</p></div>${button('＋ 添加账户','account-new','',true)}</div>
    <div class="fh-compact-strip" tabindex="0" aria-label="资金账户卡片，可左右滚动">${state.accounts.map(a=>`<article class="fh-card fh-compact-card" data-account-id="${e(a.id)}"><div class="fh-compact-title"><small>${e(accountKinds[a.kind]||a.kind)}</small><h3>${e(a.name)}</h3></div><div class="fh-compact-balance"><small>当前可核算余额</small><div class="fh-value">${yuan(a.current_balance_cents)}</div></div><div class="fh-compact-meta"><span>基准日期</span><time>${e(date(a.snapshot_as_of||a.as_of))}</time></div><p class="fh-compact-state${a.current_balance_cents==null?' pending':''}">${a.current_balance_cents==null?(a.pending_entry_ids?.length?`${a.pending_entry_ids.length} 笔资金账户待核实`:'余额基准待核对'):'已核对基准及已记录流水'}</p><div class="fh-compact-actions">${button('核对余额 / 编辑','account-edit',a.id)}</div><details class="fh-compact-details"><summary>基准与来源</summary><dl class="fh-detail"><dt>最近基准金额</dt><dd>${a.snapshot_balance_cents==null?'尚未填写':yuan(a.snapshot_balance_cents)}</dd><dt>基准日期（日终）</dt><dd>${e(date(a.snapshot_as_of||a.as_of))}</dd><dt>核对状态</dt><dd>${a.current_balance_cents==null?'待核对':'已核对基准及已记录流水'}</dd></dl>${a.pending_entry_ids?.length?`<p class="fh-caption">其后有 ${a.pending_entry_ids.length} 笔实际资金账户尚待核实，暂不显示确定余额。</p>`:''}${source(a)}</details></article>`).join('')||empty('添加实际存钱的账户，随后记录收支会按所选账户计算余额。')}</div>`;
    for(const el of box.querySelectorAll('.fh-compact-details'))el.open=expanded.has(el.closest('[data-account-id]').dataset.accountId);
    box.querySelector('.fh-compact-strip').scrollLeft=position;
  }
  function renderWishes(){
    const box=$('fh-wishes'),position=box.querySelector('.fh-compact-strip')?.scrollLeft||0,expanded=new Set([...box.querySelectorAll('.fh-compact-details[open]')].map(el=>el.closest('[data-wish-id]').dataset.wishId));
    box.innerHTML=`<div class="fh-rule fh-compact-rule"><strong>清债之后，留一份小奖励。</strong><p>全部负债已还清，且购买金额不超过已核对可用现金的 10%，才会点亮“可以购买”。</p><details><summary>看看规则示例</summary><p>例如想买 18,000 元的东西，需要先还清负债，并拥有至少 180,000 元存款。这只是规则示例。</p></details></div>
    <div class="fh-panel-heading fh-compact-section-heading"><h2>想要的东西</h2>${button('＋ 记下心愿','wish-new','',true)}</div><div class="fh-compact-strip" tabindex="0" aria-label="心愿卡片，可左右滚动">${state.wishes.map(w=>{const status={unknown_inputs:'先核对完整负债与账户余额',debt_outstanding:'先把负债还清',saving:'再攒一点存款',eligible:'✓ 可以购买'}[w.unlock_status];const ratio=w.confirmed_cash_cents==null?0:Math.min(100,Math.max(0,w.confirmed_cash_cents/w.target_savings_cents*100));return `<article class="fh-card fh-compact-card" data-wish-id="${e(w.id)}"><div class="fh-compact-title"><small>我的心愿</small><h3>${e(w.name)}</h3></div><div class="fh-compact-balance"><small>预计购买金额</small><div class="fh-value">${yuan(w.price_cents)}</div></div><p class="fh-compact-state${w.unlocked?'':' pending'}">${e(status)}</p><dl class="fh-detail fh-wish-progress-detail"><dt>存款目标</dt><dd>${yuan(w.target_savings_cents)}</dd><dt>已核存款</dt><dd>${yuan(w.confirmed_cash_cents)}</dd></dl><div class="fh-progress" aria-label="存款目标完成度"><span style="width:${ratio}%"></span></div><div class="fh-compact-actions">${button('编辑心愿','wish-edit',w.id)}</div><details class="fh-compact-details"><summary>说明与来源</summary><p class="fh-caption">满足规则仅供你决定，购买不会自动扣款。</p>${source(w)}</details></article>`;}).join('')||empty('把想买的东西记下来，不急着现在下单。')}</div>`;
    for(const el of box.querySelectorAll('.fh-compact-details'))el.open=expanded.has(el.closest('[data-wish-id]').dataset.wishId);
    box.querySelector('.fh-compact-strip').scrollLeft=position;
  }
  function coverageCopy(){const c=state.coverage||{},months=[...new Set([...(c.complete_months||[]),...(c.available_months||[]),...(c.partial_months||[])])].sort();return months.length?`已有记录范围 ${months[0]} 至 ${months[months.length-1]}，共 ${months.length} 个月；${(c.complete_months||[]).length} 个完整核对月份。缺失月份不按零消费计算。`:'尚未有完整核对的月份，暂不把缺失资料当作零消费。';}
  async function loadSuggestion(){
    if(!state||suggestion||window.tiangongAccount?.user?.role==='demo')return;
    const month=state.month,generation=loadGeneration;
    try{const data=await request({action:'budget_suggest',month});if(state?.month===month&&generation===loadGeneration){suggestion=data;if(tab==='budget')renderBudget();}}
    catch(error){if(tab==='budget')toast(error.message);}
  }
  const budgetDetailsButton=(b,index)=>`<button type="button" class="fh-budget-details-link" data-fh="budget-details" data-id="${index}" aria-label="${e(b.category)}：查看 ${e(b.month)} 消费明细">${e(b.category)}</button>`;
  function budgetEntries(b){
    const members=new Set(b.categories||[]);
    return records.filter(r=>!r.voidedAt&&r.confirmed!==false&&r.track==='daily'&&members.has(r.budget_category)&&['expense','refund'].includes(r.type)&&r.date.startsWith(b.month+'-')&&r.date<=state.as_of)
      .slice().sort((a,b)=>b.date.localeCompare(a.date)||String(b.id).localeCompare(String(a.id)));
  }
  async function openBudgetDetails(index,target){
    if(!state||!financeReady||state.month!==currentMonth()){toast('正在读取所选月份的账目，请稍后再试。');return;}
    const category=state.budgets[index]?.category,month=state.month;if(!category)return;
    let budget=state.budgets[index],rows=budgetEntries(budget);
    const total=()=>rows.reduce((sum,r)=>sum+(r.type==='refund'?-r.cents:r.cents),0);
    // Hub and ledger are separate reads: refresh once if another device changed
    // records between them, so the opened detail agrees with the visible budget.
    if(total()!==budget.consumed_cents){
      target.disabled=true;
      try{if(!await loadFinance()||!await load(true)){toast('账目暂未同步完成，请稍后刷新再查看。');return;}}
      finally{target.disabled=false;}
      if(state.month!==month||currentMonth()!==month)return;
      budget=state.budgets.find(b=>b.category===category);if(!budget)return;
      rows=budgetEntries(budget);
      if(total()!==budget.consumed_cents){toast('账目正在同步，请稍后刷新再查看明细。');return;}
    }
    openForm('budget-details',budget,{rows});
  }
  function budgetDetailsFields(b,rows){
    const spent=rows.filter(r=>r.type==='expense').reduce((sum,r)=>sum+r.cents,0),refund=rows.filter(r=>r.type==='refund').reduce((sum,r)=>sum+r.cents,0);
    return `<section class="wide fh-budget-detail"><div class="fh-budget-detail-total"><div><span>本月净消费</span><strong>${yuan(spent-refund)}</strong></div><p>${rows.length} 笔 · 支出 ${yuan(spent)}${refund?' − 退款 '+yuan(refund):''}</p></div>${rows.length?`<ol class="fh-budget-detail-list" tabindex="0" aria-label="${e(b.month)} ${e(b.category)}消费记录，可上下滚动">${rows.map(r=>`<li data-budget-entry-id="${e(r.id)}"><div class="fh-budget-detail-row"><div><strong>${e(r.name)}</strong><p><time datetime="${e(r.date)}">${e(r.date)}</time> · ${e(r.budget_category)}</p></div><span class="fh-budget-detail-amount ${r.type==='refund'?'refund':''}">${r.type==='refund'?'+':'−'}${yuan(r.cents)}<small>${r.type==='refund'?'退款':'支出'}</small></span></div>${r.channel||r.account_id?`<p class="fh-budget-detail-account">${e([r.channel,accountName(r.account_id)||''].filter(Boolean).join(' · '))}</p>`:''}${r.note?`<details class="fh-budget-detail-note"><summary>备注</summary><p>${e(r.note)}</p></details>`:''}</li>`).join('')}</ol>`:empty('这个月还没有已核对的'+b.category+'类消费。')}<p class="fh-caption">仅含 ${e((b.categories||[]).join('、'))} 的已核对日常支出与退款，与本月预算使用同一口径。</p></section>`;
  }
  function budgetCard(b,i){
    const set=b.cents!=null,ratio=b.remainder_ratio,width=set?Math.max(0,Math.min(100,(ratio||0)*100)):0,low=set&&(ratio==null||ratio<=.3);
    return `<article class="fh-budget t44-budget-card ${low?'is-low':''} ${set&&b.remaining_cents<0?'is-over':''}"><header><span class="t44-budget-icon" aria-hidden="true">${({'餐饮':'碗','日用':'物','娱乐':'乐'})[b.category]||'余'}</span><h3>${budgetDetailsButton(b,i)}</h3><span class="t44-budget-limit">${set?yuan(b.cents):'未设定'}<small> / 月</small></span></header><div class="t44-budget-balance"><small>${set?(b.remaining_cents<0?'已超预算':'剩余额度'):'本月已记消费'}</small><strong>${yuan(set?Math.abs(b.remaining_cents):b.consumed_cents)}</strong></div><div class="fh-healthbar ${!set?'unset':low?'low':''}" role="progressbar" aria-label="${e(b.category)}剩余预算" aria-valuemin="0" aria-valuemax="100" ${hiddenMoney?'':'aria-valuenow="'+Math.round(width)+'"'}><span style="width:${hiddenMoney?0:width}%"></span></div><footer><span>已用 ${yuan(b.consumed_cents)}${set?' / '+yuan(b.cents):''}</span>${button(set?'调整':'设置预算','budget-edit',String(i))}</footer></article>`;
  }
  function renderBudget(){
    $('budget-list').innerHTML=`<div class="fh-panel-heading"><div><h2>${e(state.month)} · 生活能量</h2><p class="fh-caption">绿色表示充足；剩余 30% 及以下变红。</p></div>${button('查看历史月均参考','budget-suggest')}</div>${note(coverageCopy())}
      <p class="fh-caption">仅计日常支出减退款；本金、转账、副业、投资往来不消耗这三项预算。${state.summary.unmapped_daily_count?`本月有 ${state.summary.unmapped_daily_count} 笔消费未纳入这三组（含其他类别与待核对），预算不是全部开销。`:''}</p>
      ${suggestion?`<details class="fh-panel"><summary>已有记录的月均参考 · 展开查看</summary><p class="fh-caption">${e(suggestion.based_on_months?.join('、')||suggestion.reference_months?.join('、')||'完整月份不足')}。${suggestion.status==='ready'?'按已核对月份的分类净消费计算。':'资料尚未齐全，以下仅统计已识别消费，不是完整 10 个月的预算建议。'}</p><div class="fh-grid">${suggestion.suggestions.map((b,i)=>{const v=b.cents??b.provisional_cents;return `<div><strong>${e(b.category)}</strong><p>${v==null?'待补齐消费记录':yuan(v)+' / 月'}</p>${v!=null?button('采用这项参考','budget-adopt',String(i)):''}</div>`;}).join('')}</div></details>`:''}
      <div class="fh-budget-grid">${state.budgets.map(budgetCard).join('')}</div>`;
  }

  function renderOverviewBudget(){
    const box=$('fh-overview-budget');if(!box)return;
    const month=$('month').value;
    if(month==='all'){box.innerHTML='<h2>日常预算，还剩多少</h2><p class="fh-caption">选择一个月份，查看和设置这三项生活预算。</p>';return;}
    if(!state||state.month!==month){box.innerHTML='<h2>日常预算，还剩多少</h2><p class="fh-caption">正在读取所选月份的预算…</p>';return;}
    box.innerHTML=`<div class="fh-overview-budget-heading"><h2>日常预算，还剩多少</h2><span>餐饮 · 日用 · 娱乐</span></div><div class="t44-budget-grid">${state.budgets.map(budgetCard).join('')}</div>`;
  }

  function renderHub(){
    renderOverviewBudget();const copy=pageCopy[tab]||pageCopy.overview;$('fh-page-title').textContent=copy[0];$('fh-page-description').textContent=copy[1];renderSide();
    if(!state){for(const k of ['debts','accounts','wishes'])$('fh-'+k).innerHTML=empty('正在读取资料…');return;}
    renderDebts();renderAccounts();renderWishes();if(tab==='budget')renderBudget();
    const complete=$('fh-debt-complete');if(complete)complete.onchange=async()=>{const next=complete.checked;complete.disabled=true;try{await request({action:'settings_save',id:'profile',revision:state.settings.revision,request_id:uid(),debt_inventory_confirmed:next});await load(true);toast('已更新负债清单状态');}catch(error){complete.checked=!next;complete.disabled=false;toast(error.message);}};
  }
  function debtTypeFields(row,kind){
    const rate=()=>field('annual_rate_bp','年利率（%，可留空）',row.annual_rate_bp==null?'':(row.annual_rate_bp/100).toFixed(2),'text','inputmode="decimal" placeholder="按合同填写"');
    const due=()=>field('due_day','每月还款日',row.due_day||'','number','min="1" max="31" step="1" placeholder="1—31，可留空"');
    const maturity=()=>field('maturity','合同到期日（可留空）',row.maturity||'','date')+checkbox('maturity_confirmed','填写的是已经确认的到期日；展期已办理并获准',row.maturity_confirmed);
    if(kind==='personal')return '<p class="wide fh-caption">亲友借入按无息记录。只记还欠多少、实际归还和备注，不设置月供。</p>';
    if(kind==='short_term')return (!editState.context.existingDebt?checkbox('new_borrowing','这是一笔新借款，尚未还款',true):'')+`<p id="fh-short-zero-note" class="wide fh-caption" hidden>已核对无欠款。历史原借款金额与借款日期可以留空，直接保存即可；核对日期不会当作还清日期。</p><details id="fh-short-history" class="wide fh-short-history" open><summary>借款资料</summary><div class="fh-form-section">`+amount('original_principal_cents','这一笔原借款金额（元）',row.original_principal_cents)+field('borrowed_on','这一笔借款日期',row.borrowed_on||'','date',`max="${financeToday()}"`)+`<p class="wide fh-caption">每次借入单独建一笔。新借且尚未还款时，原本金及借款日期就是初始欠款；补录旧借款，请先核对剩余本金。缺少的历史资料可以留空，实际借还日期和利息按凭证补充。</p></div></details>`;
    if(kind==='credit_card')return (row.id?checkbox('reconfirm_balance','重新核对这一天信用卡余额（包含目前已记录账单）',false):'')+due()+choose('status','卡片状态',{active:'使用中',closed:'已注销'},row.status||'active')+field('closed_at','注销日期（记不清可留空）',row.closed_at||'','date',`max="${financeToday()}"`)+field('archived_from','从哪月起归档',row.archived_from||'','month',`max="${financeToday().slice(0,7)}"`)+`<p class="wide fh-caption">注销日期不清楚可以留空。选择已注销并填写归档月份后，默认收进归档；不据此把未核清的欠款当作 0。每月消费账单另行记录，次月核对还款。</p>`;
    return amount('monthly_payment_cents',kind==='interest_only'?'约定每月利息（元，可留空）':'约定月供（本息合计，元）',row.monthly_payment_cents)+due()+rate()+(kind==='mortgage'?field('term_months','剩余期数（月，可留空）',row.term_months||'','number','min="1" max="1200" step="1"'):'')+maturity();
  }
  function bindDebtForm(){
    const select=$('fh-form').elements.namedItem('kind');let previous=select.value;const drafts={};
    function borrowingMode(){
      const form=$('fh-form'),newBorrowing=select.value==='short_term'&&!!form.elements.namedItem('new_borrowing')?.checked;
      $('fh-debt-balance-fields').hidden=newBorrowing;
      $('fh-debt-balance-fields').querySelectorAll('input,select').forEach(input=>input.disabled=newBorrowing);
      for(const name of ['principal_cents','card_balance_cents']){const input=form.elements.namedItem(name),shown=(name==='card_balance_cents')===(select.value==='credit_card');input.closest('label').hidden=!shown;input.disabled=newBorrowing||!shown;}
      for(const name of ['original_principal_cents','borrowed_on']){const input=form.elements.namedItem(name);if(input)input.required=newBorrowing;}
      const history=$('fh-short-history');
      if(history){
        const amount=form.elements.namedItem('principal_cents').value.trim(),asOf=form.elements.namedItem('as_of');
        const zero=!newBorrowing&&amount!==''&&Number(amount)===0&&form.elements.namedItem('confirmed').checked&&!!asOf.value&&asOf.validity.valid;
        $('fh-short-zero-note').hidden=!zero;
        history.querySelector('summary').textContent=zero?'历史借款资料（选填）':'借款资料'+(newBorrowing?'':'（历史资料可留空）');
        if(history.dataset.zero!==String(zero)){history.open=!zero;history.dataset.zero=String(zero);}
      }
    }
    $('fh-debt-kind-fields').addEventListener('change',event=>{if(event.target.name==='new_borrowing')borrowingMode();});
    $('fh-debt-balance-fields').addEventListener('input',borrowingMode);
    $('fh-debt-balance-fields').addEventListener('change',borrowingMode);
    borrowingMode();
    select.addEventListener('change',()=>{
      drafts[previous]=Array.from($('fh-debt-kind-fields').querySelectorAll('input,select,textarea')).map(input=>({name:input.name,value:input.value,checked:input.checked}));
      const next=select.value;$('fh-debt-kind-fields').innerHTML=debtTypeFields(next===editState.row.kind?editState.row:{},next);
      for(const saved of drafts[next]||[]){const input=$('fh-form').elements.namedItem(saved.name);if(input){input.value=saved.value;input.checked=saved.checked;}}
      previous=next;dirty=true;borrowingMode();
    });
  }
  function paymentForm(row,context){
    const debt=allDebts().find(d=>d.id===(row.debt_id||context.debt_id));if(!debt)return null;
    Object.assign(context,{debt_id:debt.id,debt_revision:debt.revision,debt_kind:debt.kind,remaining_principal_cents:debt.remaining_principal_cents});
    const editing=!!row.id,mode=editing?'edit':context.mode||'principal',monthly=['mortgage','interest_only'].includes(debt.kind),personal=debt.kind==='personal',card=debt.kind==='credit_card';
    const quoted=!editing&&monthly&&(mode==='interest'||(debt.kind==='mortgage'&&context.settles_month));context.quoteEnabled=quoted;context.quote=quoted?debt.payment_quote:null;context.amountsEdited=false;context.totalEdited=false;
    const quote=context.quote;
    let title=editing?'修改实际还款':card?'记录信用卡还款':context.close_loan?'记录这笔还清':mode==='interest'?'记录本月利息已付':debt.kind==='mortgage'&&context.settles_month?'记录本月月供':debt.kind==='interest_only'?'记录归还本金':'记录实际还款';
    let hint=debt.name+'。请按真实付款填写；保存后同步账本，勿另记同一笔。';
    if(personal)hint+=' 这笔亲友借款无息，只记录实际归还本金。';
    if(context.close_loan)hint+=' 核实本次本金与剩余本金一致后保存，实际利息另填。';
    if(mode==='interest'&&!editing)hint+=' 此处只记实际利息和费用，归还本金请使用另一按钮。';
    let fields=(quoted?'<div class="wide fh-payment-reference" id="fh-payment-reference" role="status"></div>':'')+field('date','实际还款日期',row.date||financeToday(),'date',`required max="${financeToday()}"`);
    if(card){
      const list=(state.card_statements||[]).filter(s=>s.debt_id===debt.id&&(s.remaining_cents>0||s.id===(row.statement_id||context.statement_id)));
      if(!list.length){toast('请先记录对应消费账单，再记录这一期还款。');return null;}
      const chosen=row.statement_id||context.statement_id||list[0].id;
      fields+=choose('statement_id','对应消费账单',Object.fromEntries(list.map(s=>[s.id,`${s.consumption_month} 消费 → ${s.due_month} 应还 · 未还 ${yuan(s.remaining_cents)}`])),chosen);
    }else if(monthly){fields+=field('payment_month',debt.kind==='interest_only'?'利息归属月份':'月供归属月份',row.payment_month||state.month,'month','required');}
    if(debt.kind==='mortgage'&&(context.settles_month||row.settles_month))fields+=amount('total_cents','实付总额（元，本金、利息与费用合计）',editing?row.total_cents:quote?.total_cents??null,true);
    if(debt.kind==='interest_only'&&!editing&&mode==='interest')fields+=amount('interest_cents','实付利息（元）',quote?.interest_cents??null,true)+amount('fees_cents','实付费用（元，没有填 0）',0,true);
    else if(personal)fields+=amount('principal_cents','实际归还本金（元）',row.principal_cents??null,true);
    else if(debt.kind==='interest_only'&&!editing&&mode==='principal')fields+=amount('principal_cents','实还本金（元）',null,true)+amount('fees_cents','实付费用（元，没有填 0）',0,true);
    else fields+=amount('principal_cents',card?'实际归还账单金额（元）':'实还本金（元）',row.principal_cents??(quoted?quote?.principal_cents??null:null),true)+amount('interest_cents','实付利息（元，没有填 0）',row.interest_cents??(quoted?quote?.interest_cents??null:0),true)+amount('fees_cents','实付费用（元，没有填 0）',row.fees_cents??0,true);
    fields+=accountField(row.account_id)+channelField(row.channel);
    if(monthly&&(editing||mode==='interest'||debt.kind==='mortgage'))fields+=checkbox('settles_month',debt.kind==='interest_only'?'确认所选月份的应付利息已经付清':'确认所选月份的应还月供已经还完',row.settles_month??context.settles_month);
    fields+=sourceField(row.source_note);return{title,hint,fields};
  }
  function bindPaymentForm(){
    const active=editState,form=$('fh-form'),context=active.context,amountNames=['principal_cents','interest_cents','fees_cents','total_cents'];let generation=0;
    const input=name=>form.elements.namedItem(name);
    function syncTotal(){if(input('total_cents')&&!context.totalEdited){const parts=['principal_cents','interest_cents','fees_cents'].map(name=>input(name)?parseCents(input(name).value.trim(),true):0);input('total_cents').value=parts.every(value=>value!=null)?(parts.reduce((sum,n)=>sum+n,0)/100).toFixed(2):'';}}
    function applyQuote(){const quote=context.quote;if(!quote||quote.status==='paid')return;for(const name of ['principal_cents','interest_cents','total_cents'])if(input(name))input(name).value=quote[name]==null?'':(quote[name]/100).toFixed(2);context.amountsEdited=false;context.totalEdited=false;dirty=true;}
    function reference(message=''){
      const box=$('fh-payment-reference');if(!box)return;
      const quote=context.quote;
      box.innerHTML=message?`<p>${e(message)}</p>`:quote?`<strong>${e(quote.label||'本期还款参考')}</strong>${quote.estimated?'<p>以下为参考金额，请按银行实际扣款核对；保存后才会记账。</p>':''}${(quote.assumptions||[]).map(text=>`<p>${e(text)}</p>`).join('')}${context.amountsEdited&&quote.status!=='paid'?'<button type="button" class="fh-button" id="fh-use-payment-quote">使用这一期参考金额</button>':''}${quote.status==='paid'?'<p>这一期已有付款记录；请先查看已记原账，避免再次保存同一期还款。</p>':''}`:'<p>这期金额尚未核实，请按实际付款填写。</p>';
      if($('fh-use-payment-quote'))$('fh-use-payment-quote').onclick=()=>{applyQuote();reference();};
    }
    for(const name of amountNames)if(input(name))input(name).addEventListener('input',()=>{context.amountsEdited=true;if(name==='total_cents')context.totalEdited=true;else syncTotal();});
    if(context.quoteEnabled){
      reference();const monthInput=input('payment_month');monthInput.addEventListener('change',async()=>{
        const month=monthInput.value,version=++generation;if(!/^\d{4}-\d{2}$/.test(month)){reference('请先选好还款归属月份。');return;}
        reference('正在读取这一期的还款参考…');
        try{const data=await request(undefined,month);if(editState!==active||version!==generation)return;context.quote=[...data.debts,...(data.archived_debts||[])].find(d=>d.id===context.debt_id)?.payment_quote;context.quoteMonth=month;
          if(!context.amountsEdited&&context.quote?.status!=='paid')applyQuote();reference();
        }catch(error){if(editState===active&&version===generation){context.quote=null;reference('这一期的参考金额暂未读取成功，当前填写内容已保留。请核对后再保存。');}}
      });
    }
  }
  function openForm(kind,row={},context={}){
    if(saving)return;
    if(!state){toast('请等泉库资料读取完成后再试。');return;}
    editState={kind,row:{...row},context,requestId:uid(),lastPayload:null};if(kind==='debt')editState.context.existingDebt=!!row.id;dirty=false;$('fh-form').reset();$('fh-error').textContent='';
    let title='',hint='',fields='';
    if(kind==='account'){
      title=row.id?'核对余额 / 编辑账户':'添加资金账户';hint='余额填所选日期的日终金额。只有勾选已核对后，才按此后记录推算余额；不清楚的金额或日期可以留空。';
      fields=field('name','账户名称',row.name||'','text','required maxlength="200" placeholder="例如：招商银行储蓄卡 8023"')+choose('kind','账户类型',accountKinds,row.kind||'bank')+amount('balance_cents','日终余额（元）',row.balance_cents)+field('as_of','余额日期（日终）',row.as_of||'','date',`max="${financeToday()}"`)+checkbox('confirmed','我已核对这一天的日终余额',row.confirmed)+sourceField(row.source_note);
    }else if(kind==='debt'){
      title=row.id?'编辑负债资料':'添加一笔负债';hint='先选择借款类型，填写实际核对的欠款及日期。未知金额或日期可以留空，不根据旧表猜今天欠款。';
      fields=field('name','负债名称',row.name||'','text','required maxlength="200"')+choose('kind','负债类型',kinds,row.kind||'short_term')+`<div id="fh-debt-balance-fields" class="wide fh-form-section">`+amount('principal_cents','已核对剩余本金 / 总欠款（元）',row.principal_cents)+amount('card_balance_cents','信用卡余额（元，正为溢缴款，负为欠款）',row.card_balance_cents??(row.principal_cents==null?null:-row.principal_cents))+choose('snapshot_basis','核对时点',{after_recorded_payments:'核对时（包含当时已记还款）',end_of_day:'所选日期日终'},row.snapshot_basis||'after_recorded_payments')+field('as_of','余额 / 欠款核对日期',row.as_of||'','date',`max="${financeToday()}"`)+checkbox('confirmed','以上金额、日期及核对时点已经核对',row.confirmed)+`</div><div id="fh-debt-kind-fields" class="wide fh-form-section">${debtTypeFields(row,row.kind||'short_term')}</div>`+sourceField(row.source_note);
    }else if(kind==='debt-review-list'){
      title='核对这 '+context.pending.length+' 项欠款';hint='未确认的金额保持未知；已归档卡片也可以打开核对。';fields=`<div class="wide fh-pending-list">${context.pending.map(d=>`<article><div><strong>${e(d.name)}</strong><p>${e(kinds[d.kind]||'借款')}${d.archived?' · 已归档':''} · ${!d.as_of?'需补核对日期':d.principal_cents==null?'需核对实际金额':'需核对余额及其后记录'}</p></div><button type="button" class="fh-button" data-review-debt="${e(d.id)}">核对这笔</button></article>`).join('')}</div>`;
    }else if(kind==='qq-group'){
      const rows=context.rows,totals=summary(rows);title=context.label;hint=($('month').value==='all'?'全部时间':$('month').value)+' · '+rows.length+' 笔 · 支出减退款 '+yuan(totals.expense-totals.refund)+'。保留原账，可逐笔核对。';
      fields=`<div class="wide fh-qq-detail">${rows.slice().sort((a,b)=>b.date.localeCompare(a.date)).map(r=>`<article><div><strong>${e(r.name)}</strong><p>${e(r.date)} · ${e(financeTypes[r.type])} · ${financeAmount(r)}</p>${r.note?`<p>${e(r.note)}</p>`:''}${r.confirmed===false?'<p>本笔尚待确认，不计入分项收支合计。</p>':''}</div><button type="button" class="fh-button" data-qq-edit="${e(r.id)}">核对 / 编辑</button></article>`).join('')}</div>`;
    }else if(kind==='budget-details'){
      title=row.category+' · '+row.month+' 消费明细';hint='按消费日期从近到远排列，退款抵减本月已用预算。';fields=budgetDetailsFields(row,context.rows);
    }else if(kind==='payment-reference'){
      const quote=row.payment_quote||{},linked=(row.linked_payments||[]).filter(p=>p.month===state.month),ids=new Set(linked.flatMap(p=>p.entry_ids||[])),original=records.filter(r=>ids.has(r.id)),payments=state.payments.filter(p=>p.debt_id===row.id&&p.payment_month===state.month&&!p.voidedAt),dates=quote.paid_dates||linked.flatMap(p=>p.dates||[]);
      const zeroBalance=quote.source==='checked_balance';title=zeroBalance?'本金已核对为零':'这期已经记过付款';hint=row.name+' · '+state.month+(zeroBalance?'。本金余额为零不代表本月已经发生扣款，仍以付款记录为准。':'。这里展示已有扣款，不会再生成一笔月供。');
      fields=`<div class="wide fh-payment-proof"><strong>${e(quote.label||'已记录实际付款')}</strong><dl class="fh-detail"><dt>${zeroBalance?'已有付款记录金额':'已记金额'}</dt><dd>${yuan(quote.recorded_total_cents??(linked.length?linked.reduce((sum,p)=>sum+p.total_cents,0):payments.reduce((sum,p)=>sum+p.total_cents,0)))}</dd><dt>实际日期</dt><dd>${e([...new Set(dates)].join('、')||payments.map(p=>p.date).join('、')||'见原始记录')}</dd></dl>${original.map(r=>`<article><strong>${e(r.name)}</strong><p>${e(r.date)} · ${yuan(r.cents)}</p>${r.note?`<p>${e(r.note)}</p>`:''}</article>`).join('')}${payments.map(p=>`<article><strong>${e(p.date)} · ${yuan(p.total_cents)}</strong><p>本金 ${yuan(p.principal_cents)} · 利息 ${yuan(p.interest_cents)} · 费用 ${yuan(p.fees_cents)}</p></article>`).join('')}<p class="fh-caption">如需纠正，已有还款可在本月还款流水里修改；关联的银行扣款请在全部账目核对。额外归还本金请使用单独入口。</p></div>`;
    }else if(kind==='payment'){
      const form=paymentForm(row,editState.context);if(!form)return;({title,hint,fields}=form);
    }else if(kind==='statement'){
      const debt=allDebts().find(d=>d.id===(row.debt_id||context.debt_id));if(!debt)return;
      editState.context.debt_id=debt.id;title=row.id?'修改消费账单':'新增每月消费账单';hint=debt.name+'。本月消费账单次月还；这里只记录账单应还金额，不重复增加消费流水。总欠款未核清也可以先记录每期账单。';
      fields=field('consumption_month','消费账单月',row.consumption_month||monthOffset(state.month,-1),'month',`required max="${financeToday().slice(0,7)}"${row.id?' readonly':''}`)+amount('amount_cents','这期账单应还金额（元）',row.amount_cents??null,true)+`<p id="fh-statement-due" class="wide fh-caption"></p>`+sourceField(row.source_note);
    }else if(kind==='wish'){
      title=row.id?'编辑心愿':'记下想买的东西';hint='所有负债还清、存款达到价格的 10 倍时，心愿才会显示可以购买。';
      fields=field('name','想买什么',row.name||'','text','required maxlength="200" placeholder="给未来的自己留个心愿"')+amount('price_cents','预计价格（元）',row.price_cents,true)+sourceField(row.source_note);
    }else if(kind==='budget'){
      title='设置 '+row.category+' 预算';hint=state.month+' · '+(context.reference?'根据现有记录月均参考填写，可按你的实际情况调整。':'请填写本月可用于这项消费的金额。')+' 剩余 30% 及以下时血条变红。';
      fields=amount('cents','本月预算（元）',context.reference??row.cents,true)+sourceField(row.source_note);
    }else if(kind==='void'){
      title='撤销这笔还款';hint='撤销后保留原记录与修改历史，并撤回对应的本金、利息和费用账目。不会操作银行卡中的真实资金。';
      fields=field('reason','撤销原因','','text','required maxlength="1000"');
    }
    $('fh-editor-title').textContent=title;$('fh-editor-note').textContent=hint;$('fh-fields').innerHTML=fields;$('fh-save').textContent=kind==='void'?'确认撤销':'保存';$('fh-save').hidden=['debt-review-list','payment-reference','qq-group','budget-details'].includes(kind);$('fh-editor').showModal();
    if(kind==='debt-review-list')$('fh-fields').querySelectorAll('[data-review-debt]').forEach(button=>button.onclick=()=>{const debt=allDebts().find(d=>d.id===button.dataset.reviewDebt);if(debt){$('fh-editor').close();openForm('debt',debt);}});
    if(kind==='qq-group')$('fh-fields').querySelectorAll('[data-qq-edit]').forEach(button=>button.onclick=()=>{$('fh-editor').close();editState=null;dirty=false;edit(button.dataset.qqEdit);});
    if(kind==='debt')bindDebtForm();
    if(kind==='payment')bindPaymentForm();
    if(kind==='statement'){const input=$('fh-form').elements.namedItem('consumption_month'),update=()=>{$('fh-statement-due').textContent=/^\d{4}-\d{2}$/.test(input.value)?'对应 '+monthOffset(input.value,1)+' 还款；金额按这一期账单填写。':'';};input.addEventListener('input',update);update();}
  }
  function formPayload(){
    const f=$('fh-form'),val=k=>f.elements.namedItem(k)?.value.trim()||'',bool=k=>!!f.elements.namedItem(k)?.checked;
    const cents=(k,nullable=false,signed=false)=>{const raw=val(k);if(nullable&&!raw)return null;const negative=signed&&raw.startsWith('-'),v=parseCents(signed&&/^[+-]/.test(raw)?raw.slice(1):raw,true);if(v===null)throw new Error('金额请填写数字，最多保留两位小数。');return negative?-v:v;};
    const int=k=>{const v=val(k);if(!v)return null;if(!/^\d+$/.test(v))throw new Error('还款日和期数请填整数。');return Number(v);};
    const {kind,row,context}=editState;let payload={id:row.id||uid(),revision:row.revision||0,source_note:val('source_note')};
    if(kind==='account')Object.assign(payload,{action:'account_save',name:val('name'),kind:val('kind'),balance_cents:cents('balance_cents',true,true),as_of:val('as_of')||null,confirmed:bool('confirmed')});
    if(kind==='debt')Object.assign(payload,{action:'debt_save',name:val('name'),kind:val('kind'),principal_cents:val('kind')==='credit_card'?null:cents('principal_cents',true),as_of:val('as_of')||null,confirmed:bool('confirmed'),annual_rate_bp:val('kind')==='personal'?0:cents('annual_rate_bp',true),monthly_payment_cents:cents('monthly_payment_cents',true),term_months:int('term_months'),due_day:int('due_day'),maturity:val('maturity')||null,maturity_confirmed:bool('maturity_confirmed'),original_principal_cents:f.elements.namedItem('original_principal_cents')?cents('original_principal_cents',true):val('kind')===row.kind?row.original_principal_cents??null:null,borrowed_on:f.elements.namedItem('borrowed_on')?val('borrowed_on')||null:val('kind')===row.kind?row.borrowed_on||null:null,snapshot_basis:val('snapshot_basis')||row.snapshot_basis||'after_recorded_payments',status:val('status')||'active',closed_at:val('closed_at')||null,archived_from:val('archived_from')||null});
    if(kind==='debt'&&val('kind')==='short_term'&&!context.existingDebt&&bool('new_borrowing')){
      if(!val('borrowed_on')||payload.original_principal_cents==null||payload.original_principal_cents<=0)throw new Error('请填写这笔新借款的原本金和实际借款日期。');
      Object.assign(payload,{principal_cents:payload.original_principal_cents,as_of:val('borrowed_on'),confirmed:true});
    }
    if(kind==='debt'&&val('kind')==='credit_card'){delete payload.principal_cents;payload.card_balance_cents=cents('card_balance_cents',true,true);if(bool('reconfirm_balance'))payload.reconfirm_balance=true;}
    if(kind==='payment'){
      const statement=context.debt_kind==='credit_card'?(state.card_statements||[]).find(item=>item.id===val('statement_id')&&item.debt_id===context.debt_id):null;
      if(context.debt_kind==='credit_card'&&!statement)throw new Error('请选择这次还款对应的消费账单。');
      const paid=k=>f.elements.namedItem(k)?cents(k):0;
      Object.assign(payload,{action:'debt_payment',debt_id:context.debt_id,debt_revision:context.debt_revision,date:val('date'),payment_month:statement?.due_month||val('payment_month')||val('date').slice(0,7),principal_cents:paid('principal_cents'),interest_cents:paid('interest_cents'),fees_cents:paid('fees_cents'),account_id:val('account_id')||null,channel:val('channel'),settles_month:bool('settles_month')});
      if(statement)payload.statement_id=statement.id;
      if(f.elements.namedItem('total_cents')&&cents('total_cents')!==payload.principal_cents+payload.interest_cents+payload.fees_cents)throw new Error('本金、利息及费用合计需要与实付总额一致，请核对后保存。');
      if(context.quoteEnabled&&context.quote?.status==='paid'&&payload.payment_month===context.quote.month&&payload.settles_month)throw new Error('这一期已经记过付款，请先查看已有记录，避免重复。');
      if(context.close_loan&&(context.remaining_principal_cents==null||payload.principal_cents!==context.remaining_principal_cents))throw new Error('请先核对剩余本金，记录还清时实还本金应与剩余本金一致。部分还款请使用“记录还款”。');
    }
    if(kind==='statement')Object.assign(payload,{action:'card_statement_save',debt_id:context.debt_id,consumption_month:val('consumption_month'),amount_cents:cents('amount_cents')});
    if(kind==='wish')Object.assign(payload,{action:'wish_save',name:val('name'),price_cents:cents('price_cents')});
    if(kind==='budget')Object.assign(payload,{action:'budget_save',month:row.month||state.month,category:row.category,cents:cents('cents'),...(row.legacy_revisions?{legacy_revisions:row.legacy_revisions}:{})});
    if(kind==='void')Object.assign(payload,{action:'payment_void',reason:val('reason')});
    // A new entity and a retry keep the same id and request, even after a lost response.
    if(!row.id)editState.row.id=payload.id;
    const fingerprint=JSON.stringify(payload);if(editState.lastPayload&&editState.lastPayload!==fingerprint)editState.requestId=uid();editState.lastPayload=fingerprint;payload.request_id=editState.requestId;return payload;
  }
  async function close(){if(saving)return;if(dirty&&!await window.tiangongConfirm('这里还有未保存的修改，放弃修改并关闭？'))return;$('fh-editor').close();editState=null;dirty=false;}
  $('fh-close').onclick=close;$('fh-editor').addEventListener('cancel',event=>{event.preventDefault();close();});$('fh-form').oninput=()=>{dirty=true;};
  $('fh-form').onsubmit=async event=>{
    event.preventDefault();if(saving||!editState||['debt-review-list','payment-reference','qq-group','budget-details'].includes(editState.kind))return;let payload;try{payload=formPayload();}catch(error){$('fh-error').textContent=error.message;return;}
    saving=true;$('fh-error').textContent='';$('fh-form').querySelectorAll('input,select,textarea,button').forEach(n=>n.disabled=true);
    try{await request(payload);dirty=false;const ledgerReady=await loadFinance(),hubReady=await load(true);$('fh-editor').close();editState=null;toast(ledgerReady&&hubReady?'已保存，账目和余额已同步更新':'已保存；页面暂未刷新，请稍后点击刷新查看。');}
    catch(error){$('fh-error').textContent=error.message+(error.code===409?'。当前填写内容仍保留，请关闭并刷新后重新核对。':'');}
    finally{saving=false;$('fh-form').querySelectorAll('input,select,textarea,button').forEach(n=>n.disabled=false);}
  };
  $('finance').addEventListener('click',async event=>{
    const target=event.target.closest('[data-fh]');if(!target)return;const id=target.dataset.id,action=target.dataset.fh;
    if(action==='stock-all'){$('month').value='all';financePage=1;render();return;}
    if(action==='track'){side=id;financePage=1;render();return;}
    if(action==='qq-group'){const group=qqGroups()[Number(id)];if(group)openForm('qq-group',{}, {label:group[0],rows:group[1]});return;}
    if(action==='debt-review'){const pending=allDebts().filter(d=>d.remaining_principal_cents==null);if(pending.length===1)openForm('debt',pending[0]);else if(pending.length)openForm('debt-review-list',{}, {pending});return;}
    if(!state){toast('泉库资料正在读取，请稍后再试。');return;}
    const debt=allDebts().find(d=>d.id===id),statement=(state.card_statements||[]).find(s=>s.id===id),account=state.accounts.find(a=>a.id===id),wish=state.wishes.find(w=>w.id===id),payment=state.payments.find(p=>p.id===id);
    if(action==='account-new')openForm('account');if(action==='account-edit'&&account)openForm('account',account);
    if(action==='debt-new')openForm('debt');if(action==='debt-edit'&&debt)openForm('debt',debt);
    if(action==='payment-month'&&debt&&(debt.payment_quote?.status==='paid'||debt.monthly_status==='paid')){openForm('payment-reference',debt);return;}
    if(['payment-month','payment-new','payment-settle'].includes(action)&&debt){const existing=action==='payment-month'&&debt.monthly_status==='paid'?state.payments.find(p=>p.debt_id===id&&p.payment_month===state.month&&p.settles_month&&!p.voidedAt):null;openForm('payment',existing||{}, {debt_id:id,settles_month:action==='payment-month',mode:action==='payment-month'&&debt.kind==='interest_only'?'interest':'principal',close_loan:action==='payment-settle'});}
    if(action==='statement-new'&&debt)openForm('statement',{}, {debt_id:id});
    if(action==='statement-edit'&&statement)openForm('statement',statement);
    if(action==='statement-pay'&&statement)openForm('payment',{}, {debt_id:statement.debt_id,statement_id:statement.id});
    if(action==='payment-edit'&&payment)openForm('payment',payment);
    if(action==='payment-void'&&payment)openForm('void',payment);
    if(action==='wish-new')openForm('wish');if(action==='wish-edit'&&wish)openForm('wish',wish);
    if(action==='budget-details'){await openBudgetDetails(Number(id),target);return;}
    if(action==='budget-edit'&&state.budgets[Number(id)]){const b=state.budgets[Number(id)],ref=suggestion?.suggestions.find(r=>r.category===b.category);openForm('budget',b,b.cents==null&&ref?{reference:ref.cents??ref.provisional_cents}:{});}
    if(action==='budget-adopt'&&suggestion){const ref=suggestion.suggestions[Number(id)],budget=state.budgets.find(b=>b.category===ref?.category);if(budget)openForm('budget',budget,{reference:ref.cents??ref.provisional_cents});}
    if(action==='budget-suggest'){
      target.disabled=true;try{suggestion=await request({action:'budget_suggest',month:state.month});renderBudget();}catch(error){toast(error.message);target.disabled=false;}
    }
  });
  function prepareEntry(row){
    const opts='<option value="">尚未核对</option>'+(state?.accounts||[]).map(a=>`<option value="${e(a.id)}">${e(a.name)}</option>`).join('');
    $('entry-funding').innerHTML=opts;$('entry-destination').innerHTML=opts;
    $('entry-funding').value=row?.account_id||'';$('entry-destination').value=row?.to_account_id||'';
    if(row?.channel&&!Array.from($('entry-channel').options).some(o=>o.value===row.channel))$('entry-channel').add(new Option(row.channel,row.channel));
    $('entry-confirmed').checked=row?.confirmed!==false;$('entry-channel').value=row?.channel||'';$('entry-track').value=row?.track||(row?'unknown':tab==='side'?side:'daily');$('entry-budget-category').value=row?.budget_category||'unknown';entryTypeChanged();
  }
  function entryTypeChanged(){$('entry-transfer-field').hidden=$('entry-type').value!=='transfer';}
  function entryFields(){
    const account_id=$('entry-funding').value||null,to_account_id=$('entry-type').value==='transfer'?$('entry-destination').value||null:null;
    if(to_account_id&&account_id===to_account_id)throw new Error('转出和转入请选择不同的账户。');
    return {account_id,to_account_id,confirmed:$('entry-confirmed').checked,channel:$('entry-channel').value,track:$('entry-track').value,budget_category:$('entry-budget-category').value};
  }
  window.financeHub={load,stockMetrics,isReady:()=>!!state,render:renderHub,prepareEntry,entryFields,entryTypeChanged,accountName,trackName:key=>tracks[key]||tracks.unknown,
    includeRecord:(r,t)=>!r.voidedAt&&(['overview','daily'].includes(t)?r.track==='daily':t==='side'?r.track===side:true),
    tabChanged:()=>{if(state?.month!==currentMonth())load();else if(tab==='budget')loadSuggestion();renderHub();},
  };
  renderHub();
})();
