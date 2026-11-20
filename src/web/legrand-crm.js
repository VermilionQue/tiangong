/* Legrand: one customer, one history, connected channels / documents / receipts. */
(() => {
  'use strict';
  const work=document.getElementById('work'), receipts=document.getElementById('receipts');
  if(!work||!receipts)return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=n=>n==null?'未填写':'¥ '+(n/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const uid=()=>crypto.randomUUID(), today=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date());
  const businessTypes=['全屋智能','开关面板','全屋网络','音响影院'];
  const stages=['首触','跟进A','跟进B','跟进C','跟进H','方案中','报价中','成交','战败','无效'];
  const fileKinds={price:'价格表 · Excel',proposal:'方案图 / 方案PDF',construction:'施工图 · PDF',payment:'付款截图',contract:'合同',other:'其他资料'};
  let rows=[],view='active',busy=false,loaded=false,active=false,seq=0,current=null,attachments=[],attempt=null,dirty=false;
  const shell=document.createElement('div');shell.className='legrand-workspace';shell.hidden=true;
  receipts.before(shell);
  const root=document.createElement('section');root.id='legrand-crm';root.className='crm-panel';
  root.innerHTML=`<header class="crm-heading"><div><p class="crm-eyebrow">客户业务 · 客户与渠道</p><h2>客户管理</h2></div><button class="primary" id="crm-add" disabled>＋ 新客户</button></header>
    <div class="crm-tabs" role="group" aria-label="客户状态"><button data-view="active" aria-pressed="true">跟进中 <span></span></button><button data-view="won" aria-pressed="false">已成交 <span></span></button><button data-view="channels" aria-pressed="false">渠道 <span></span></button><button data-view="trash" aria-pressed="false">回收站 <span></span></button></div>
    <div class="crm-filters"><input id="crm-search" type="search" aria-label="搜索客户或渠道" placeholder="找客户、项目、小区或把控要点"><select id="crm-channel-filter" aria-label="按渠道筛选"><option value="">全部渠道</option></select><button id="crm-refresh" class="text-button">刷新</button></div>
    <p id="crm-status" class="crm-status" role="status"></p><div id="crm-list" class="crm-list" tabindex="0" aria-label="客户列表，可上下滚动"></div>`;
  const summaries=document.createElement('div');summaries.className='crm-month-summaries';
  const signed=document.createElement('section');signed.className='crm-signed-summary';
  signed.innerHTML='<p class="crm-eyebrow">客户业务 · 客户签约</p><h2>当月签约</h2><p id="crm-signed-month" class="crm-muted"></p><button type="button" id="crm-signed-total" class="t45-summary-link" aria-label="查看当月签约客户" disabled>—</button><p class="crm-muted">点击户数查看签约客户</p><button type="button" id="crm-signed-missing" class="text-button" hidden></button>';
  summaries.append(receipts,signed);shell.append(root,summaries);
  const signedDialog=document.createElement('dialog');signedDialog.id='crm-signed-ledger';signedDialog.className='t45-ledger';signedDialog.setAttribute('aria-labelledby','crm-signed-title');document.body.append(signedDialog);
  let signedView='month';

  const panel=work.querySelector('.work-panel');panel?.classList.add('work-report-panel');
  const d=document.createElement('dialog');d.id='crm-editor';d.className='crm-dialog';d.setAttribute('aria-labelledby','crm-dialog-title');document.body.append(d);
  const $=id=>document.getElementById(id);
  async function request(url,payload){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),payload?45000:15000);
    try{const res=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal,...(payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});
      const data=await res.json();if(!res.ok)throw new Error(data.error||'暂时无法读取，请重试');return data;
    }catch(e){if(e.name==='AbortError')throw new Error('网络较慢，内容已保留；请稍后重试。');throw e;}finally{clearTimeout(timer);}
  }
  const isTrash=r=>r.deletedAt||['战败','无效'].includes(r.stage);
  const byID=id=>rows.find(r=>r.id===id);
  const labelRef=r=>[byID(r.unit_id)?.name,byID(r.member_id)?.name||r.source].filter(Boolean).join(' · ')||'未填写来源';
  const status=(s,error=false)=>{$('crm-status').textContent=s;$('crm-status').classList.toggle('error',error);};
  async function load(){
    const n=++seq;status('正在读取客户资料…');
    try{const result=await request('/api/crm');if(n!==seq)return;rows=result.records;loaded=true;render();status(`${rows.filter(r=>r.kind==='customer').length} 位客户 · 资料和文件随账号保存`);}
    catch(e){if(n===seq)status(e.message,true);}
    $('crm-add').disabled=!loaded;
  }
  function options(kind,selected,empty='未关联'){
    return `<option value="">${empty}</option>`+rows.filter(r=>r.kind===kind&&(!r.deletedAt||r.id===selected)).map(r=>`<option value="${esc(r.id)}" ${r.id===selected?'selected':''}>${esc(r.name)}</option>`).join('');
  }
  function render(){
    renderSigned();
    const filter=$('crm-channel-filter').value;$('crm-channel-filter').innerHTML=options('unit',filter,'全部渠道');
    const customers=rows.filter(r=>r.kind==='customer'), counts={active:customers.filter(r=>!isTrash(r)&&r.stage!=='成交').length,won:customers.filter(r=>!isTrash(r)&&r.stage==='成交').length,trash:rows.filter(isTrash).length,channels:rows.filter(r=>r.kind!=='customer'&&!r.deletedAt).length};
    root.querySelectorAll('[data-view]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.view===view));b.querySelector('span').textContent=counts[b.dataset.view];});
    $('crm-add').textContent=view==='channels'?'＋ 新渠道':'＋ 新客户';
    $('crm-channel-filter').hidden=view==='channels';
    const q=$('crm-search').value.trim().toLowerCase();
    const visible=rows.filter(r=>view==='trash'?isTrash(r):view==='channels'?r.kind!=='customer'&&!r.deletedAt:r.kind==='customer'&&!isTrash(r)&&(view==='won'?r.stage==='成交':r.stage!=='成交')).filter(r=>!filter||view==='channels'||r.unit_id===filter).filter(r=>[r.name,r.source,r.phone,r.address,r.community,r.control_points,r.note,labelRef(r)].some(v=>String(v||'').toLowerCase().includes(q)));
    $('crm-list').innerHTML=visible.length?visible.map(r=>r.kind==='customer'?`<button type="button" class="crm-customer" data-open="${esc(r.id)}"><div class="crm-customer-title"><strong>${esc(r.name)}</strong><span class="crm-stage ${r.stage==='成交'?'won':''}">${r.deletedAt?'已移除':esc(r.stage)}</span></div><p class="crm-source">${esc(labelRef(r))}</p><div class="crm-customer-meta"><span>${esc(r.community||r.address||'小区待补充')}</span><span>${esc(r.home_type||'')}${r.area?' · '+esc(r.area):''}</span></div>${r.control_points?`<p class="crm-points">${esc(r.control_points)}</p>`:''}<footer><span>${r.contact_on?'首触 '+esc(r.contact_on):'首触日期待补充'}</span><span>${r.budget_cents==null?'预算待判断':money(r.budget_cents)}</span></footer>${r.next_follow_on?`<span class="crm-next">下次跟进 ${esc(r.next_follow_on)}</span>`:''}</button>`:`<button type="button" class="crm-channel" data-open="${esc(r.id)}"><span class="crm-channel-type">${r.kind==='unit'?'渠道单位':'渠道成员'}</span><strong>${esc(r.name)}</strong><span>${esc(r.kind==='member'?byID(r.unit_id)?.name||'尚未关联单位':r.contact||'')}</span><small>${customers.filter(c=>r.kind==='unit'?c.unit_id===r.id:c.member_id===r.id).length} 位关联客户${r.phone?' · '+esc(r.phone):''}</small></button>`).join(''):'<div class="crm-empty">这里暂时没有记录。<br>新增客户后，从首触开始留住每一步。</div>';
    $('crm-list').querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>open(byID(b.dataset.open)));
  }
  function signedRows(missing=false){
    const month=$('receipt-month').value;
    return rows.filter(r=>r.kind==='customer'&&!isTrash(r)&&r.stage==='成交'&&(missing?!r.signed_on:r.signed_on?.slice(0,7)===month)).sort((a,b)=>(b.signed_on||'').localeCompare(a.signed_on||'')||a.name.localeCompare(b.name));
  }
  function renderSigned(){
    const month=$('receipt-month').value,missing=signedRows(true);
    $('crm-signed-month').textContent=month+' · 按签约日期';
    $('crm-signed-total').textContent=loaded?signedRows().length+' 户':'—';$('crm-signed-total').disabled=!loaded;
    $('crm-signed-missing').hidden=!missing.length;
    $('crm-signed-missing').textContent=missing.length+' 位成交客户待补签约日期';
    if(signedDialog.open)openSigned(signedView);
  }
  function openSigned(mode='month'){
    signedView=mode;const missing=mode==='missing',list=signedRows(missing);
    signedDialog.innerHTML=`<div class="t45-dialog-heading"><div><p class="crm-eyebrow">客户业务 · 签约记录</p><h2 id="crm-signed-title">${missing?'待补签约日期':esc($('receipt-month').value)+' · 当月签约'}</h2></div><button type="button" id="crm-signed-close" aria-label="关闭签约记录">×</button></div><p class="crm-muted">${missing?'打开客户档案，补充实际签约日期后计入对应月份。':'按已成交客户统计，每位客户计一户。'}</p><div class="t45-ledger-list">${list.map(r=>`<button type="button" class="crm-customer" data-signed-open="${esc(r.id)}"><div class="crm-customer-title"><strong>${esc(r.name)}</strong><time>${esc(r.signed_on||'日期待补充')}</time></div><p>${esc(r.community||r.address||'地址未填写')}</p><small>${esc((r.business_types||[]).join(' · ')||'业务类型待补充')}</small></button>`).join('')||'<p class="crm-empty">这个月还没有签约记录。</p>'}</div>`;
    $('crm-signed-close').onclick=()=>signedDialog.close();signedDialog.querySelectorAll('[data-signed-open]').forEach(b=>b.onclick=()=>{signedDialog.close();open(byID(b.dataset.signedOpen));});
    if(!signedDialog.open)signedDialog.showModal();
  }
  $('crm-signed-total').onclick=()=>openSigned();$('crm-signed-missing').onclick=()=>openSigned('missing');
  window.addEventListener('tiangong:receipt-month',renderSigned);
  function field(label,key,value='',type='text',wide=false){return `<label class="${wide?'crm-wide':''}">${label}<input name="${key}" type="${type}" value="${esc(value)}" ${key==='name'?'required maxlength="200"':'maxlength="1000"'}></label>`;}
  function area(label,key,value='',wide=true){return `<label class="${wide?'crm-wide':''}">${label}<textarea name="${key}" rows="3" maxlength="8000">${esc(value)}</textarea></label>`;}
  function select(label,key,html){return `<label>${label}<select name="${key}">${html}</select></label>`;}
  async function open(row=null,kind='customer'){
    if(busy)return;
    current=row?structuredClone(row):{id:uid(),revision:0,kind,stage:'首触',contact_on:today(),attachments:[]};
    attachments=structuredClone(current.attachments||[]);attempt=null;dirty=false;
    const r=current,customer=r.kind==='customer',trash=isTrash(r);
    d.innerHTML=`<header class="crm-dialog-head"><div><p class="crm-eyebrow">${customer?'客户档案':r.kind==='unit'?'渠道单位':'渠道成员'}</p><h2 id="crm-dialog-title">${esc(r.name||(customer?'新客户':'新增渠道'))}</h2></div><button type="button" id="crm-close" aria-label="关闭客户档案">×</button></header><div class="crm-dialog-body"><form id="crm-form"><fieldset ${trash?'disabled':''} id="crm-fields"><div class="crm-form-grid">
      ${field(customer?'客户姓名 / 项目':'名称','name',r.name)}
      ${customer?select('进度','stage',stages.map(s=>`<option ${s===r.stage?'selected':''}>${s}</option>`).join('')):field('联系人','contact',r.contact)}
      ${customer?`<div class="crm-wide crm-business-types"><span>业务类型（可多选）</span><div>${businessTypes.map(t=>`<label><input type="checkbox" name="business_types" value="${t}" ${(r.business_types||[]).includes(t)?'checked':''}>${t}</label>`).join('')}</div></div>`:''}
      ${field('电话','phone',r.phone)}
      ${customer?field('首触时间','contact_on',r.contact_on,'date'):''}
      ${r.kind!=='unit'?select('渠道单位','unit_id',options('unit',r.unit_id)):''}
      ${customer?select('渠道成员','member_id',options('member',r.member_id)):''}
      ${customer?field('来源补充','source',r.source)+field('判断客户预算（元，可留空）','budget',r.budget_cents==null?'':(r.budget_cents/100).toFixed(2))+field('户型','home_type',r.home_type)+field('面积','area',r.area)+field('小区','community',r.community)+field('下次跟进日期','next_follow_on',r.next_follow_on,'date'):''}
      ${field('完整地址','address',r.address,'text',true)}
      ${customer?area('把控要点 · 客观事实','control_points',r.control_points):''}
      ${area('备注','note',r.note)}
      ${customer?field('签约日期（成交后填写）','signed_on',r.signed_on,'date')+field('成交后的交付进度','delivery_stage',r.delivery_stage,'text',true):''}
      </div>${customer?`<section class="crm-files"><h3>成交资料与凭证</h3><p>价格表、方案、施工图、付款截图与合同，集中放在这位客户下面。</p><div class="crm-file-tools"><select id="crm-file-category" aria-label="文件分类">${Object.entries(fileKinds).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><label class="crm-file-pick">＋ 上传文件<input id="crm-file-input" type="file" multiple accept=".xlsx,.xls,.pdf,.png,.jpg,.jpeg,.webp,.doc,.docx"></label></div><small>每份最多12MB；上传后点击保存资料。付款截图也可直接粘贴。</small><div id="crm-files-list"></div></section>`:''}</fieldset>
      <p id="crm-error" role="alert"></p><footer class="crm-form-actions">${trash?'<button type="button" class="primary" id="crm-restore">恢复记录</button>':'<button type="submit" class="primary" id="crm-save">保存资料</button>'}${r.revision&&!trash?'<button type="button" class="text-button" id="crm-delete">移入回收站</button>':''}</footer></form>
      ${customer?`<aside class="crm-record-side"><section><div class="crm-section-head"><h3>跟进记录</h3></div>${!trash?`<label>跟进日期<input type="date" id="crm-follow-date" value="${today()}"></label><label>这次有什么进展<textarea id="crm-follow-content" rows="3" maxlength="5000" placeholder="写事实、约定与下一步"></textarea></label><button type="button" id="crm-follow-add" class="crm-secondary" ${r.revision?'':'disabled'}>记录这次跟进</button>${!r.revision?'<small>先保存客户，再添加跟进。</small>':''}`:''}<div id="crm-timeline">${[...(r.followups||[])].reverse().map(f=>`<article><time>${esc(f.date)}</time><p>${esc(f.content)}</p></article>`).join('')||'<p class="crm-muted">还没有跟进记录。</p>'}</div></section><section><div class="crm-section-head"><h3>关联收款</h3>${r.revision&&!trash?'<button type="button" class="text-button" id="crm-receive">＋ 记收款</button>':''}</div><div id="crm-payments">${r.revision?'正在读取…':'保存客户后可以记录收款。'}</div></section></aside>`:''}
      </div>${r.revision?'<details class="crm-provenance"><summary>原始来源与修改记录</summary><div id="crm-source-detail">正在读取…</div></details>':''}`;
    $('crm-close').onclick=close;
    if(!d.open)d.showModal();
    $('crm-form').addEventListener('input',()=>dirty=true);
    $('crm-form').onsubmit=saveForm;
    if(customer){renderFiles();$('crm-file-input').onchange=e=>uploadFiles([...e.target.files]);
      $('crm-follow-add')?.addEventListener('click',async()=>{
        if(dirty){error('请先保存资料修改，再记录跟进。');return;}
        const content=$('crm-follow-content').value.trim();if(!content){error('请先写下这次跟进内容。');return;}
        await mutate({action:'followup',content,date:$('crm-follow-date').value},false);
      });
      $('crm-receive')?.addEventListener('click',async()=>{const customer=current;if(!await close())return;window.dispatchEvent(new CustomEvent('tiangong:receipt-for-customer',{detail:customer}));});
      const member=d.querySelector('[name=member_id]');member.onchange=()=>{const unit=byID(member.value)?.unit_id;if(unit)d.querySelector('[name=unit_id]').value=unit;};
      d.querySelector('[name=unit_id]').onchange=e=>{if(byID(member.value)?.unit_id!==e.target.value)member.value='';};
    }
    $('crm-delete')?.addEventListener('click',async()=>{if(await window.tiangongConfirm('移入回收站？客户资料、文件和跟进记录都会保留，可以恢复。'))mutate({action:'delete'});});
    $('crm-restore')?.addEventListener('click',()=>mutate({action:'restore'}));
    if(r.revision){
      try{const result=await request('/api/crm/detail?id='+encodeURIComponent(r.id));if(current?.id!==r.id||!d.open)return;
        const legacy=result.record.legacy;
        $('crm-source-detail').innerHTML=(legacy?`<p>来源：飞书原表 · ${esc(result.record.source_sheet)} · 第 ${result.record.source_row} 行</p><dl>${Object.entries(legacy).filter(([,v])=>v!==null&&v!=='').map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`:'<p>在天工录创建</p>')+`<h4>修改记录</h4>${result.history.map(h=>`<p>版本 ${h.revision} · ${esc(h.updatedAt.slice(0,10))} · ${esc(h.name)}${h.stage?' · '+esc(h.stage):''}</p>`).join('')||'暂无修改'} `;
        if(customer)$('crm-payments').innerHTML=result.receipts.length?result.receipts.map(p=>`<p><time>${esc(p.received_on)}</time> <strong>${money(p.amount_cents)}</strong><br>${esc(p.project)} · ${esc(p.note)}</p>`).join(''):'<p class="crm-muted">暂无已关联收款。旧表到款信息在原始来源中保留。</p>';
      }catch(e){if(current?.id===r.id&&d.open){$('crm-source-detail').textContent=e.message;if(customer)$('crm-payments').textContent=e.message;}}
    }
  }
  async function close(){if(busy)return false;if(dirty&&!await window.tiangongConfirm('当前资料尚未保存，确定关闭吗？'))return false;d.close();current=null;return true;}
  d.addEventListener('cancel',e=>{e.preventDefault();close();});
  d.addEventListener('paste',e=>{if(!current||current.kind!=='customer'||isTrash(current)||busy)return;const files=[...(e.clipboardData?.items||[])].filter(x=>x.kind==='file'&&x.type.startsWith('image/')).map(x=>x.getAsFile()).filter(Boolean);if(files.length){e.preventDefault();$('crm-file-category').value='payment';uploadFiles(files);}});
  function error(s){$('crm-error').textContent=s;}
  function setBusy(on){busy=on;d.querySelectorAll('button,input,select,textarea').forEach(e=>e.disabled=on||Boolean(e.closest('fieldset[disabled]'))||(e.id==='crm-follow-add'&&!current?.revision));}
  function renderFiles(){const list=$('crm-files-list');if(!list)return;list.innerHTML=attachments.map((a,i)=>`<div class="crm-file"><span><small>${esc(fileKinds[a.category])}</small><a href="/crm-assets/${esc(a.id)}" download>${esc(a.name)}</a><small>${(a.size/1024).toFixed(0)} KB</small></span>${!isTrash(current)?`<button type="button" data-remove="${i}" aria-label="移除 ${esc(a.name)}">×</button>`:''}</div>`).join('')||'<p class="crm-muted">尚未添加文件</p>';list.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{attachments.splice(Number(b.dataset.remove),1);dirty=true;renderFiles();});}
  const fileBase64=file=>new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(new Error('文件读取失败'));r.readAsDataURL(file);});
  async function uploadFiles(files){if(busy||!files.length)return;const category=$('crm-file-category').value;setBusy(true);error('正在上传文件…');
    try{for(const f of files){if(f.size>12*1024*1024)throw new Error(`${f.name} 超过12MB，请压缩后上传`);const {asset}=await request('/api/crm/upload',{name:f.name,data:await fileBase64(f)});if(!attachments.some(a=>a.id===asset.id))attachments.push({...asset,category});dirty=true;}error('上传完成，保存资料后会挂到这位客户下面。');}
    catch(e){error(e.message);}finally{renderFiles();setBusy(false);$('crm-file-input').value='';}
  }
  async function saveForm(e){e.preventDefault();if(busy)return;const form=Object.fromEntries(new FormData(e.target));const data={...form,kind:current.kind,action:'save'};
    if(current.kind==='customer'){data.business_types=new FormData(e.target).getAll('business_types');if(form.budget&&!/^\d+(\.\d{1,2})?$/.test(form.budget.trim())){error('预算请填写正数，最多两位小数；也可以留空。');return;}data.budget_cents=form.budget?Math.round(Number(form.budget)*100):null;delete data.budget;data.attachments=attachments;}
    await mutate(data);
  }
  async function mutate(payload,finish=true){if(busy)return;setBusy(true);error('');const body={...payload,id:current.id,revision:current.revision};const fp=JSON.stringify(body);if(attempt?.fp!==fp)attempt={fp,body:{...body,request_id:uid()}};
    try{const result=await request('/api/crm',attempt.body);dirty=false;const row=result.record;setBusy(false);if(finish){d.close();current=null;}await load();if(!finish)await open(row);window.toast?.('客户资料已保存');}
    catch(e){error(e.message);}finally{setBusy(false);}
  }
  root.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;$('crm-channel-filter').value='';render();});
  $('crm-add').onclick=()=>{if(view==='channels'){openChannelChoice();}else open();};
  function openChannelChoice(){d.innerHTML='<header class="crm-dialog-head"><h2 id="crm-dialog-title">新增渠道</h2><button id="crm-close" aria-label="关闭">×</button></header><div class="crm-choice"><button class="primary" id="crm-add-unit">渠道单位</button><button class="primary" id="crm-add-member">渠道成员</button><p>先添加单位，再将渠道成员关联到单位；客户可以关联具体介绍人。</p></div>';current=null;dirty=false;d.showModal();$('crm-close').onclick=close;$('crm-add-unit').onclick=()=>open(null,'unit');$('crm-add-member').onclick=()=>open(null,'member');}
  $('crm-search').oninput=render;$('crm-channel-filter').onchange=render;$('crm-refresh').onclick=load;
  window.crmFillReceipt=(selected='')=>{const select=$('receipt-customer');if(select)select.innerHTML='<option value="">暂不关联客户</option>'+rows.filter(r=>r.kind==='customer'&&(!isTrash(r)||r.id===selected)).map(r=>`<option value="${esc(r.id)}" ${r.id===selected?'selected':''}>${esc(r.name)}</option>`).join('');};
  function selectBusiness(b){active=b==='客户业务';shell.hidden=!active;work.classList.toggle('legrand-active',active);if(active)load();else signedDialog.close();}
  window.addEventListener('tiangong:business-change',e=>selectBusiness(e.detail?.business));
  selectBusiness(document.querySelector('[data-business][aria-selected="true"]')?.dataset.business);
})();
