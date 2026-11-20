'use strict';
((global) => {
  const MAX_CENTS = 99999999999;
  function parseAmount(value) {
    const input = String(value).trim();
    if (!/^\d{1,9}(?:\.\d{1,2})?$/.test(input)) throw new Error('金额请填写大于 0 的数字，最多两位小数。');
    const [whole, fraction = ''] = input.split('.');
    const cents = Number(whole)*100+Number(fraction.padEnd(2,'0'));
    if (!Number.isSafeInteger(cents) || cents < 1 || cents > MAX_CENTS) throw new Error('金额请填写 0.01 至 999999999.99 元。');
    return cents;
  }
  const money = cents => Number.isSafeInteger(cents) && cents >= 0
    ? '¥'+Math.floor(cents/100).toLocaleString('zh-CN')+'.'+String(cents%100).padStart(2,'0') : '—';
  const amountInput = cents => Math.floor(cents/100)+'.'+String(cents%100).padStart(2,'0');
  const today = () => {
    const parts = new Intl.DateTimeFormat('en',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    return ['year','month','day'].map(type=>parts.find(part=>part.type===type).value).join('-');
  };
  const validMonth = value => typeof value==='string' && /^[0-9]{4}-(0[1-9]|1[0-2])$/.test(value) && Number(value.slice(0,4))>0;
  const photoKey = value => typeof value==='string' && /^[a-f0-9]{64}\.webp$/.test(value);
  const photoURL = key => photoKey(key) ? '/receipt-assets/'+key : '';
  if (typeof module !== 'undefined' && module.exports) module.exports={parseAmount,money,amountInput,validMonth};
  if (!global.document) return;
  const root = document.getElementById('receipts');
  if (!root) return;
  const el = name => document.getElementById('receipt-'+name);
  const safe = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const newID = () => global.crypto?.randomUUID?.() || 'receipt-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
  let rows=[], month=today().slice(0,7), view='active', ready=false, busy=false, activeBusiness='';
  let sequence=0, loading=false, snapshot=null, photos=[], dirty=false, saveAttempt=null, mutationAttempts=new Map();
  const ledger=document.createElement('dialog');ledger.id='receipt-ledger';ledger.className='t45-ledger';ledger.setAttribute('aria-labelledby','receipt-ledger-title');
  ledger.innerHTML='<div class="t45-dialog-heading"><div><p class="receipt-eyebrow">客户业务</p><h2 id="receipt-ledger-title">当月收款记录</h2></div><button type="button" id="receipt-ledger-close" aria-label="关闭收款记录">×</button></div>';
  ledger.append(root.querySelector('.receipt-list-tools'),el('list'));root.append(ledger);
  const totalButton=document.createElement('button');totalButton.id='receipt-show-ledger';totalButton.type='button';totalButton.className='t45-summary-link';totalButton.setAttribute('aria-label','查看当月收款记录');
  el('total').before(totalButton);totalButton.append(el('total'));totalButton.onclick=()=>ledger.showModal();
  el('ledger-close').onclick=()=>ledger.close();
  el('month').value=month;
  el('photo-hint').textContent='可直接粘贴截图，或点击添加。最多 8 张，单张不超过 8 MB，保存收款时一起上传。';

  function status(message, error=false) { el('status').textContent=message; el('status').classList.toggle('error',error); }
  function validRow(row) {
    return row && typeof row.id==='string' && /^[A-Za-z0-9_-]{1,100}$/.test(row.id) && row.business==='客户业务' &&
      Number.isSafeInteger(row.revision) && row.revision>0 && Number.isSafeInteger(row.amount_cents) && row.amount_cents>0 &&
      ['received_on','project','owner','address','note','deletedAt'].every(key=>typeof row[key]==='string') &&
      Array.isArray(row.photos) && row.photos.every(photoKey);
  }
  async function request(path, data) {
    let response;
    try {
      response=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),signal:AbortSignal.timeout(30000)}:{cache:'no-store',signal:AbortSignal.timeout(15000)});
    } catch { throw new Error('暂时没有收到服务回复，内容仍保留在这里。请稍后重试。'); }
    let result;
    try { result=await response.json(); } catch { throw new Error('暂时未收到有效回复，请保留内容后重试。'); }
    if(!response.ok) throw Object.assign(new Error(result.error||'暂时无法保存，请稍后重试。'),{code:response.status});
    return result;
  }
  function accept(result) {
    if(!validRow(result.record)) throw new Error('保存回复未能确认，请保留内容后重试。');
    return result.record;
  }
  function thumbnails(keys, label) {
    return keys.length?`<div class="receipt-photos">${keys.map((key,index)=>`<div class="receipt-photo"><a href="${photoURL(key)}" target="_blank" rel="noopener" aria-label="查看${safe(label)}的第 ${index+1} 张截图"><img src="${photoURL(key)}" alt="${safe(label)}收款截图 ${index+1}" loading="lazy"></a></div>`).join('')}</div>`:'';
  }
  function render() {
    const active=rows.filter(row=>!row.deletedAt), removed=rows.filter(row=>row.deletedAt);
    const total=active.reduce((sum,row)=>sum+row.amount_cents,0);
    el('total').textContent=ready?money(total):'—';
    el('total-label').textContent=`${month.slice(0,4)} 年 ${Number(month.slice(5))} 月已收款`;
    el('count').textContent=ready?`${active.length} 笔收款 · 点击金额查看`:'暂未读取';
    totalButton.disabled=!ready;
    el('ledger-title').textContent=`${month} · 收款记录`;
    global.dispatchEvent(new CustomEvent('tiangong:receipt-month',{detail:{month}}));
    el('trash').textContent='回收站'+(removed.length?' · '+removed.length:'');
    el('active').setAttribute('aria-pressed',String(view==='active'));
    el('trash').setAttribute('aria-pressed',String(view==='trash'));
    el('add').disabled=!ready||busy||loading;
    const visible=view==='trash'?removed:active;
    if(!ready) { el('list').innerHTML=''; return; }
    el('list').innerHTML=visible.length?visible.map(row=>`<article class="receipt-card"><div class="receipt-card-top"><div><h3>${safe(row.project)}</h3><p>业主 · ${safe(row.owner)}</p></div><strong class="receipt-card-amount">${money(row.amount_cents)}</strong></div>${row.address?`<p class="receipt-card-address">${safe(row.address)}</p>`:''}${row.note?`<p class="receipt-card-note">${safe(row.note)}</p>`:''}${thumbnails(row.photos,row.project)}<div class="receipt-card-bottom"><time datetime="${safe(row.received_on)}">${safe(row.received_on)}${row.deletedAt?' · 已移入回收站':''}</time><button type="button" ${busy?'disabled':''} data-receipt-${view==='trash'?'restore':'edit'}="${safe(row.id)}" aria-label="${view==='trash'?'恢复':'编辑'} ${safe(row.project)} 收款">${view==='trash'?'恢复':'查看 / 编辑'}</button></div></article>`).join(''):`<div class="receipt-empty"><strong>${view==='trash'?'这个月的回收站是空的':'这个月还没有收款记录'}</strong>${view==='trash'?'移除的收款会保留在这里，可以随时恢复。':'记下项目、业主和到账金额，也可以附上收款截图。'}</div>`;
    root.querySelectorAll('[data-receipt-edit]').forEach(button=>button.onclick=()=>{ledger.close();openEditor(rows.find(row=>row.id===button.dataset.receiptEdit));});
    root.querySelectorAll('[data-receipt-restore]').forEach(button=>button.onclick=()=>mutate(rows.find(row=>row.id===button.dataset.receiptRestore),'restore'));
  }
  async function load() {
    if(activeBusiness!=='客户业务'||!validMonth(month)) return false;
    const req=++sequence, selected=month;
    loading=true; el('add').disabled=true; status('正在读取收款…');
    try {
      const result=await request('/api/receipts?month='+encodeURIComponent(selected));
      if(req!==sequence||selected!==month||activeBusiness!=='客户业务') return false;
      if(result.month!==selected||!Array.isArray(result.records)||!result.records.every(validRow)) throw new Error('收款回复格式不完整，请刷新后重试。');
      rows=result.records.sort((a,b)=>b.received_on.localeCompare(a.received_on)||b.createdAt.localeCompare(a.createdAt));
      ready=true;status('');return true;
    } catch(error) {
      if(req===sequence) { ready=false; rows=[]; status(error.message,true); }
      return false;
    } finally { if(req===sequence){loading=false;render();} }
  }
  function setBusy(value) {
    busy=value;el('fields').disabled=value;el('save').disabled=value;el('delete').disabled=value;el('close').disabled=value;el('reload').disabled=value;
    el('save').textContent=value?'正在保存…':'保存收款';renderPhotos();render();
  }
  function releasePreviews() { photos.forEach(photo=>{if(photo.preview)URL.revokeObjectURL(photo.preview);}); }
  function renderPhotos() {
    el('photos').innerHTML=photos.map((photo,index)=>`<div class="receipt-photo"><img src="${safe(photo.preview||photoURL(photo.id))}" alt="待保存的收款截图 ${index+1}"><button type="button" data-receipt-remove-photo="${index}" aria-label="移除第 ${index+1} 张截图" ${busy?'disabled':''}>×</button></div>`).join('');
    el('photo-add').disabled=busy||photos.length>=8;
    el('photos').querySelectorAll('[data-receipt-remove-photo]').forEach(button=>button.onclick=()=>{
      if(busy)return;const [removed]=photos.splice(Number(button.dataset.receiptRemovePhoto),1);if(removed.preview)URL.revokeObjectURL(removed.preview);dirty=true;renderPhotos();
    });
  }
  function addPhotoFiles(files) {
    if(busy||!snapshot||!el('editor').open||!files.length)return;
    el('error').textContent='';
    if(photos.length+files.length>8){el('error').textContent='一笔收款最多添加 8 张截图。';return;}
    if(files.some(file=>!['image/jpeg','image/png','image/webp','image/gif'].includes(file.type)||file.size>8*1024*1024||!file.size)){el('error').textContent='请选择不超过 8 MB 的 JPG、PNG、WebP 或 GIF 截图。';return;}
    photos.push(...files.map(file=>({file,preview:URL.createObjectURL(file)})));dirty=true;renderPhotos();
  }
  function pastePhotos(event) {
    const target=event.target instanceof Element?event.target:null;
    if(event.defaultPrevented||busy||!snapshot||!el('editor').open||activeBusiness!=='客户业务'||root.closest('[hidden]')||target?.closest('dialog')!==el('editor'))return;
    const clipboard=event.clipboardData;if(!clipboard)return;
    // A copied text/URL keeps its normal meaning inside project, owner or notes.
    if(target.closest('input,textarea,[contenteditable]')&&clipboard.getData('text/plain'))return;
    const items=Array.from(clipboard.items||[]);
    let files=Array.from(clipboard.files||[]);
    // The same image normally appears in both lists; use items only as fallback.
    if(!files.length)files=items.filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);
    if(!files.length||(!files.some(file=>file.type.startsWith('image/'))&&!items.some(item=>item.kind==='file'&&item.type.startsWith('image/'))))return;
    event.preventDefault();addPhotoFiles(files);
  }
  function openEditor(row, customer=null) {
    if(busy||loading||!ready)return;
    releasePreviews();photos=(row?.photos||[]).map(id=>({id}));
    snapshot=row?JSON.parse(JSON.stringify(row)):{id:newID(),revision:0};
    dirty=false;saveAttempt=null;
    el('form').reset();el('editor-title').textContent=row?'编辑收款':'记录收款';
    el('date').value=row?.received_on||today();el('date').max=today();
    el('amount').value=row?amountInput(row.amount_cents):'';
    ['project','owner','address','note'].forEach(key=>el(key).value=row?.[key]||'');
    global.crmFillReceipt?.(customer?.id||row?.customer_id||'');
    if(customer){el('project').value=customer.name;el('owner').value=customer.name;el('address').value=customer.address||customer.community||'';}
    el('error').textContent='';el('reload').hidden=true;el('delete').hidden=!row;
    el('history').hidden=!row;el('history').open=false;el('history-list').textContent='展开后查看修改前的内容。';
    renderPhotos();if(!el('editor').open)el('editor').showModal();
  }
  async function closeEditor(force=false) {
    if(busy)return;
    if(!force&&dirty&&!await window.tiangongConfirm('还没有保存这笔收款，确定放弃本次修改吗？'))return;
    el('editor').close();releasePreviews();photos=[];snapshot=null;dirty=false;saveAttempt=null;
  }
  function retainAttempt(previous, payload) {
    const fingerprint=JSON.stringify(payload);
    return previous?.fingerprint===fingerprint?previous:{fingerprint,payload:{...payload,request_id:newID()}};
  }
  const fileData=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('这张截图暂时无法读取，请重新选择。'));reader.readAsDataURL(file);});
  async function uploadPhotos() {
    for(const photo of photos) {
      if(photo.id)continue;
      const result=await request('/api/receipts/upload',{data_url:await fileData(photo.file)});
      if(!photoKey(result.asset?.id))throw new Error('截图上传回复未能确认，请保留内容后重试。');
      photo.id=result.asset.id;delete photo.file;
    }
    return [...new Set(photos.map(photo=>photo.id))];
  }
  async function save(event) {
    event.preventDefault();if(busy||!snapshot)return;
    el('error').textContent='';el('reload').hidden=true;
    let data;
    try {
      data={id:snapshot.id,revision:snapshot.revision,action:'save',business:'客户业务',received_on:el('date').value,
        project:el('project').value.trim(),owner:el('owner').value.trim(),amount_cents:parseAmount(el('amount').value),
        address:el('address').value.trim(),note:el('note').value.trim(),customer_id:el('customer')?el('customer').value:(snapshot.customer_id||'')};
      if(!data.project||!data.owner)throw new Error('请填写项目名称和业主。');
      setBusy(true);data.photos=await uploadPhotos();saveAttempt=retainAttempt(saveAttempt,data);
      const record=accept(await request('/api/receipts',saveAttempt.payload));
      setBusy(false);closeEditor(true);
      month=record.received_on.slice(0,7);el('month').value=month;view='active';
      const refreshed=await load();if(refreshed)status('这笔收款已保存。');
      global.toast?.('收款已保存');
    } catch(error) {
      el('error').textContent=error.message;el('reload').hidden=error.code!==409;
    } finally { setBusy(false); }
  }
  async function mutate(row,action) {
    if(!row||busy)return;
    if(action==='delete'&&!await window.tiangongConfirm('将这笔收款移入回收站？之后可以恢复，截图和修改记录都会保留。'))return;
    const key=action+':'+row.id, payload={id:row.id,revision:row.revision,action,business:'客户业务'};
    const attempt=retainAttempt(mutationAttempts.get(key),payload);mutationAttempts.set(key,attempt);
    setBusy(true);el('error').textContent='';
    try {
      accept(await request('/api/receipts',attempt.payload));mutationAttempts.delete(key);
      setBusy(false);if(el('editor').open)closeEditor(true);
      if(await load())status(action==='delete'?'已移入回收站，可以随时恢复。':'这笔收款已恢复，并重新计入合计。');
    } catch(error) {
      if(el('editor').open){el('error').textContent=error.message;el('reload').hidden=error.code!==409;}
      else status(error.code===409?'这笔收款已更新，请刷新后再恢复。':error.message,true);
    } finally {setBusy(false);}
  }
  function changeMonth(value) {
    if(busy||!validMonth(value)){el('month').value=month;return;}
    month=value;el('month').value=month;ready=false;rows=[];render();load();
  }
  function stepMonth(delta) {
    const [year,part]=month.split('-').map(Number), index=year*12+part-1+delta;
    const nextYear=Math.floor(index/12);if(nextYear<1||nextYear>9999)return;
    changeMonth(String(nextYear).padStart(4,'0')+'-'+String(index%12+1).padStart(2,'0'));
  }
  async function loadHistory() {
    if(!el('history').open||!snapshot?.revision)return;
    const id=snapshot.id, revision=snapshot.revision;
    el('history-list').textContent='正在读取修改记录…';
    try {
      const result=await request('/api/receipts/history?id='+encodeURIComponent(id));
      if(snapshot?.id!==id||snapshot?.revision!==revision)return;
      if(!Array.isArray(result.history)||!result.history.every(item=>validRow(item.record)))throw new Error('修改记录暂时未能读取。');
      el('history-list').innerHTML=result.history.length?result.history.map(item=>{
        const row=item.record;
        return `<div class="receipt-history-entry"><strong>版本 ${row.revision} · ${safe(row.received_on)} · ${money(row.amount_cents)}</strong><p>${safe(row.project)} · ${safe(row.owner)}</p>${row.address?`<p>${safe(row.address)}</p>`:''}${row.note?`<p>${safe(row.note)}</p>`:''}<p>${row.deletedAt?'当时在回收站':'当时为有效收款'} · ${safe(row.author||'本人')}</p>${thumbnails(row.photos,row.project)}</div>`;
      }).join(''):'还没有修改记录。';
    } catch(error){if(snapshot?.id===id)el('history-list').textContent=error.message;}
  }
  el('form').onsubmit=save;
  el('form').addEventListener('input',()=>{dirty=true;});
  el('close').onclick=()=>closeEditor();
  el('editor').addEventListener('cancel',event=>{event.preventDefault();closeEditor();});
  el('editor').addEventListener('paste',pastePhotos);
  el('add').onclick=()=>openEditor();
  el('delete').onclick=()=>mutate(snapshot,'delete');
  el('history').ontoggle=loadHistory;
  el('reload').onclick=async()=>{
    if(busy||!snapshot)return;
    if(dirty&&!await window.tiangongConfirm('读取最新内容会替换当前未保存的修改，是否继续？'))return;
    const id=snapshot.id;setBusy(true);
    try {
      const result=await request('/api/receipts/history?id='+encodeURIComponent(id));
      const row=accept(result);setBusy(false);
      if(row.deletedAt){closeEditor(true);view='trash';month=row.received_on.slice(0,7);el('month').value=month;await load();status('这笔收款已在回收站，可以先恢复。');}
      else openEditor(row);
    } catch(error){el('error').textContent=error.message;}finally{setBusy(false);}
  };
  el('photo-add').onclick=()=>el('photo-input').click();
  el('photo-input').onchange=()=>{
    const files=Array.from(el('photo-input').files||[]);el('photo-input').value='';addPhotoFiles(files);
  };
  el('month').onchange=()=>changeMonth(el('month').value);
  el('prev').onclick=()=>stepMonth(-1);el('next').onclick=()=>stepMonth(1);el('current').onclick=()=>changeMonth(today().slice(0,7));
  el('active').onclick=()=>{view='active';render();};el('trash').onclick=()=>{view='trash';render();};el('refresh').onclick=()=>{if(!busy)load();};
  function selectBusiness(value) {
    activeBusiness=value;root.hidden=value!=='客户业务';
    if(root.hidden){sequence++;loading=false;ledger.close();return;}
    load();
  }
  global.addEventListener('tiangong:business-change',event=>selectBusiness(event.detail?.business||''));
  global.addEventListener('tiangong:receipt-for-customer',async event=>{if(!ready||loading)await load();openEditor(null,event.detail);});
  global.loadReceipts=load;
  selectBusiness(document.querySelector('[data-business][aria-selected="true"]')?.dataset.business||'');
})(typeof window!=='undefined'?window:globalThis);
