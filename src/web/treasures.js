'use strict';
// Prices remain integer cents. Ratios are display values, never stored prices.
((global) => {
  const dayNumber = value => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const [y,m,d] = value.split('-').map(Number), date = new Date(0);
    date.setUTCHours(0,0,0,0); date.setUTCFullYear(y,m-1,d);
    return y > 0 && date.getUTCFullYear() === y && date.getUTCMonth() === m-1 && date.getUTCDate() === d ? date.getTime()/86400000 : null;
  };
  const today = () => {
    const parts = new Intl.DateTimeFormat('en', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    return ['year','month','day'].map(type => parts.find(p => p.type === type).value).join('-');
  };
  function metrics(row, date = today()) {
    const from = dayNumber(row.purchased_on), to = dayNumber(row.status === 'sold' ? row.sold_on : date);
    const days = from !== null && to !== null && to >= from ? to-from+1 : null;
    const purchase = Number.isSafeInteger(row.purchase_cents) && row.purchase_cents >= 0 ? row.purchase_cents : null;
    const sale = Number.isSafeInteger(row.sale_cents) && row.sale_cents >= 0 ? row.sale_cents : null;
    const net = purchase !== null && (row.status !== 'sold' || sale !== null) ? purchase-(row.status === 'sold' ? sale : 0) : null;
    return {owned_days:days,net_cents:net,daily_cost_cents:net !== null && days ? net/days : null,per_use_cost_cents:net !== null && Number.isSafeInteger(row.uses) && row.uses > 0 ? net/row.uses : null};
  }
  function parsePrice(value) {
    const input = String(value).trim();
    if (!input) return null;
    if (!/^\d{1,9}(?:\.\d{1,2})?$/.test(input)) throw new Error('金额请填写 0 或正数，最多两位小数。');
    const [whole, fraction = ''] = input.split('.');
    const cents = Number(whole)*100+Number(fraction.padEnd(2,'0'));
    if (!Number.isSafeInteger(cents) || cents > 99999999999) throw new Error('金额超出可记录范围。');
    return cents;
  }
  const currency = value => value === null || !Number.isFinite(value) ? '未记录' : '¥'+new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(value/100);
  const compareNullable = (a,b,desc = false) => a === null ? (b === null ? 0 : 1) : b === null ? -1 : (a-b)*(desc?-1:1);
  const helpers = {dayNumber,today,metrics,parsePrice,currency};
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  global.TreasureMath = helpers;
  if (!global.document) return;
  const root = document.getElementById('treasures');
  if (!root) return;
  const el = id => document.getElementById('treasure-'+id);
  const safe = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icons = {
    sparkle:['珍藏','<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>'],
    laptop:['电脑','<rect x="4" y="4" width="16" height="12" rx="2"/><path d="M2 20h20l-2-4H4l-2 4Z"/>'],
    phone:['手机','<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 5h4m-3 14h2"/>'],
    camera:['相机','<path d="M4 7h3l2-3h6l2 3h3a2 2 0 0 1 2 2v10H2V9a2 2 0 0 1 2-2Z"/><circle cx="12" cy="13" r="4"/>'],
    headphones:['耳机','<path d="M4 13V10a8 8 0 0 1 16 0v3"/><rect x="3" y="11" width="5" height="9" rx="2"/><rect x="16" y="11" width="5" height="9" rx="2"/>'],
    watch:['手表','<rect x="6" y="6" width="12" height="12" rx="4"/><path d="m8 6 1-4h6l1 4m-8 12 1 4h6l1-4m-4-9v3l2 1"/>'],
    bag:['包袋','<rect x="4" y="8" width="16" height="13" rx="3"/><path d="M8 9V6a4 4 0 0 1 8 0v3"/>'],
    shoes:['鞋履','<path d="m3 6 5 2 2 6 8 2a4 4 0 0 1 4 4H2V8l1-2Zm-1 12h19m-11-6 3-2m0 4 3-2"/>'],
    home:['家居','<path d="m3 10 9-7 9 7v11H3V10Zm6 11v-8h6v8"/>'],
    book:['书与兴趣','<path d="M12 5C8 2 5 3 2 4v16c4-2 7-1 10 1 3-2 6-3 10-1V4c-3-1-6-2-10 1Zm0 0v16"/>'],
    bike:['运动','<circle cx="5" cy="16" r="4"/><circle cx="19" cy="16" r="4"/><path d="m5 16 5-8 4 8H5m5-8h7l2 8m-3-12h3m-9 4-1-3H6"/>'],
    other:['其他','<rect x="4" y="4" width="16" height="16" rx="5"/><path d="M8 12h8m-4-4v8"/>']
  };
  const icon = key => `<svg class="treasure-icon" viewBox="0 0 24 24" aria-hidden="true">${(icons[key]||icons.sparkle)[1]}</svg>`;
  const validPhoto = value => typeof value === 'string' && /^[a-f0-9]{64}\.(?:jpg|jpeg|png|webp)$/.test(value);
  const photoURL = value => validPhoto(value) ? '/treasure-assets/'+encodeURIComponent(value) : '';
  let records = [], view = 'active', ready = false, loading = null, pending = false, editSnapshot = null, dirty = false;
  let selectedIcon = 'sparkle', photo = '', pendingPhoto = null, photoPreview = '', photoReading = false, photoVersion = 0;
  let saveAttempt = null;
  const useAttempts = new Map(), otherAttempts = new Map();
  const newID = () => global.crypto?.randomUUID?.() || 't'+Date.now().toString(36)+Math.random().toString(36).slice(2)+Math.random().toString(36).slice(2);
  function status(message, error = false) { el('status').textContent = message; el('status').classList.toggle('error',error); }
  async function request(path, data) {
    const response = await fetch(path, data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {cache:'no-store'});
    let body;
    try { body = await response.json(); } catch { throw new Error('暂时未收到有效回复，请保留内容并稍后重试。'); }
    if (!response.ok) throw Object.assign(new Error(body.error || '暂时无法保存，请稍后重试。'),{code:response.status});
    return body;
  }
  function validRecord(row) {
    return row && typeof row.id === 'string' && typeof row.name === 'string' && ['active','sold'].includes(row.status)
      && Number.isSafeInteger(row.revision) && row.revision > 0 && Number.isSafeInteger(row.uses) && row.uses >= 0
      && (row.purchase_cents === null || Number.isSafeInteger(row.purchase_cents) && row.purchase_cents >= 0)
      && (row.sale_cents === null || Number.isSafeInteger(row.sale_cents) && row.sale_cents >= 0)
      && (!row.photo || validPhoto(row.photo));
  }
  function accept(body) {
    if (!validRecord(body.record)) throw new Error('保存回复暂时无法确认；请保留内容并重试。');
    const row = body.record;
    const newer = records.find(r => r.id === row.id && r.revision > row.revision);
    if (newer) return newer;
    records = records.filter(r => r.id !== row.id).concat(row); return row;
  }
  global.loadTreasures = async () => {
    if (pending) return false;
    if (loading) return loading;
    status('正在打开宝物柜…'); el('refresh').disabled = true;
    loading = (async () => {
      try {
        const body = await request('/api/treasures');
        if (!Array.isArray(body.records) || !body.records.every(validRecord)) throw new Error('物品资料格式异常，请稍后重试。');
        records = body.records; ready = true; options(); render(); status(''); return true;
      } catch (error) { status((ready?'暂未刷新，仍显示上次读取的物品。':'宝物柜尚未读取成功。')+error.message,true); return false; }
      finally { loading = null; el('refresh').disabled = false; }
    })();
    return loading;
  };
  function options() {
    const categories = [...new Set(records.filter(r=>!r.deletedAt).map(r=>r.category).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
    const old = el('category-filter').value;
    el('category-filter').innerHTML = '<option value="">全部分类</option>'+categories.map(c=>`<option value="${safe(c)}">${safe(c)}</option>`).join('');
    el('category-filter').value = categories.includes(old) ? old : '';
    el('category-options').innerHTML = [...new Set(['数码','家居','穿戴','兴趣','运动','出行',...categories])].map(c=>`<option value="${safe(c)}"></option>`).join('');
  }
  function filtered() {
    const query = el('search').value.trim().toLocaleLowerCase(), category = el('category-filter').value;
    const result = records.filter(r => (view === 'trash' ? !!r.deletedAt : !r.deletedAt && (view === 'all' || r.status === view))
      && (!category || r.category === category) && [r.name,r.brand,r.category,r.note].join(' ').toLocaleLowerCase().includes(query));
    const sort = el('sort').value;
    return result.sort((a,b) => {
      let difference = 0;
      if (sort === 'price-desc') difference = compareNullable(a.purchase_cents,b.purchase_cents,true);
      else if (sort === 'daily-desc') difference = compareNullable(metrics(a).daily_cost_cents,metrics(b).daily_cost_cents,true);
      else if (sort === 'uses-desc') difference = b.uses-a.uses;
      else if (sort === 'name') difference = a.name.localeCompare(b.name,'zh-CN');
      else if (sort === 'oldest') difference = compareNullable(dayNumber(a.purchased_on),dayNumber(b.purchased_on));
      else difference = (b.createdAt || '').localeCompare(a.createdAt || '');
      return difference || a.id.localeCompare(b.id);
    });
  }
  function renderSummary(rows) {
    const list = rows.map(row=>({row,m:metrics(row)}));
    const sum = (filter, value) => { const known = list.filter(filter); return {count:known.length,value:known.reduce((s,r)=>s+value(r),0)}; };
    const purchase = sum(r=>r.row.purchase_cents !== null,r=>r.row.purchase_cents), net = sum(r=>r.m.net_cents !== null,r=>r.m.net_cents), daily = sum(r=>r.m.daily_cost_cents !== null,r=>r.m.daily_cost_cents);
    const label = item => item.count ? currency(item.value) : '—';
    el('summary').innerHTML = [
      ['这一柜宝物',String(rows.length),view === 'trash'?'回收站内，可恢复':'当前筛选 · '+rows.filter(r=>r.status==='active').length+' 件陪伴中'],
      ['购入金额',label(purchase),purchase.count+' 件已记价格'+(rows.length>purchase.count?' · '+(rows.length-purchase.count)+' 件未记':'')],
      ['净花费',label(net),net.count+' 件可计算 · 已扣已知售出回款'],
      ['日均成本合计',label(daily),daily.count+' 件可计算 · 已售按售出日冻结']
    ].map(([title,value,note])=>`<article class="treasure-stat"><span>${title}</span><strong>${safe(value)}</strong><small>${safe(note)}</small></article>`).join('');
  }
  const dateLabel = date => date ? date.replace(/-/g,'.') : '日期未记';
  function card(row) {
    const m = metrics(row), deleted = !!row.deletedAt, sold = row.status === 'sold', url = photoURL(row.photo);
    const shortCost = amount => amount === null ? '—' : safe(currency(amount));
    const media = url ? `<img src="${safe(url)}" alt="${safe(row.name)}的照片" loading="lazy">` : icon(row.icon);
    const detail = `<div class="treasure-card-media">${media}${sold||deleted?`<span class="treasure-badge">${deleted?'已收起':'已售出'}</span>`:''}</div><div class="treasure-card-copy"><h3>${safe(row.name)}</h3><p>${safe([row.brand,row.category].filter(Boolean).join(' · ')||'未分类')}</p><dl class="treasure-card-metrics"><div><dt>${sold?'曾陪伴':'已拥有'}</dt><dd>${m.owned_days===null?'—':m.owned_days+'<small>天</small>'}</dd></div><div><dt>使用次数</dt><dd>${row.uses}<small>次</small></dd></div><div><dt>日均成本</dt><dd>${shortCost(m.daily_cost_cents)}</dd></div><div><dt>每次成本</dt><dd>${shortCost(m.per_use_cost_cents)}</dd></div></dl></div>`;
    return `<article class="treasure-card" data-treasure-id="${safe(row.id)}">${deleted?`<div class="treasure-card-open">${detail}</div>`:`<button type="button" class="treasure-card-open" data-treasure-edit="${safe(row.id)}" aria-label="查看或编辑${safe(row.name)}">${detail}</button>`}<div class="treasure-card-footer"><div><p>${sold?'净花费':'购入价'} <strong>${safe(currency(sold?m.net_cents:row.purchase_cents))}</strong></p><p>${sold?'售于 '+dateLabel(row.sold_on):row.warranty_until?'保修至 '+dateLabel(row.warranty_until):row.purchased_on?'购于 '+dateLabel(row.purchased_on):'购入日期未记'}</p></div>${deleted?`<button type="button" class="treasure-restore" data-treasure-restore="${safe(row.id)}"${pending?' disabled':''}>恢复</button>`:sold?'<span class="treasure-sold-caption">陪伴已留存</span>':`<button type="button" class="treasure-use" data-treasure-use="${safe(row.id)}" aria-label="${safe(row.name)}用了一次"${pending?' disabled':''}>＋ 用了一次</button>`}</div></article>`;
  }
  function render() {
    el('add').disabled = !ready || pending;
    root.querySelectorAll('[data-treasure-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.treasureView===view)));
    if (!ready) return;
    const rows = filtered(); renderSummary(rows); el('count').textContent = `共 ${rows.length} 件${view==='trash'?' · 回收站物品不计入其他视图':''}`;
    const filteredOut = !!el('search').value.trim() || !!el('category-filter').value;
    el('grid').innerHTML = rows.length ? rows.map(card).join('') : `<div class="treasure-empty">${icon('sparkle')}<h3>${filteredOut?'没有找到这件宝物':view==='trash'?'回收站是空的':view==='sold'?'还没有售出的物品':'给珍爱的物品，留一个位置'}</h3><p>${filteredOut?'换一个关键词或分类试试。':view==='trash'?'收起的记录会留在这里，随时可以恢复。':view==='sold'?'以后售出的物品，会保留它曾陪伴你的日子。':'从身边的一件物品开始。价格和日期可以慢慢补上，不必一次记全。'}</p>${!filteredOut&&['active','all'].includes(view)?'<button type="button" class="treasure-primary" data-treasure-new>＋ 记录第一件</button>':''}</div>`;
  }
  function resetPhotoPreview() { if (photoPreview) URL.revokeObjectURL(photoPreview); photoPreview=''; }
  function renderPhoto() {
    const url = photoPreview || photoURL(photo);
    el('image-preview').innerHTML = url ? `<img src="${safe(url)}" alt="所选物品照片">` : icon(selectedIcon);
    el('photo-remove').hidden = !url;
    el('icon-options').innerHTML = Object.entries(icons).map(([key,[name]])=>`<button type="button" data-treasure-icon="${key}" aria-label="${name}图标" title="${name}" aria-pressed="${selectedIcon===key}">${icon(key)}</button>`).join('');
  }
  const formMap = {name:'name',brand:'brand',category:'category',purchased_on:'purchased-on',warranty_until:'warranty-until',uses:'uses',status:'item-status',sold_on:'sold-on',note:'note'};
  async function openEditor(id) {
    if (!ready || pending || loading || photoReading) return;
    if (dirty && !await window.tiangongConfirm('还有未保存的内容。放弃这次修改？')) return;
    const row = id ? records.find(r=>r.id===id) : null;
    if (id && (!row || row.deletedAt)) return;
    editSnapshot = row ? {...row} : {id:newID(),revision:0,name:'',brand:'',category:'',purchased_on:'',purchase_cents:null,warranty_until:'',uses:0,status:'active',sold_on:'',sale_cents:null,note:'',icon:'sparkle',photo:''};
    for (const [key,node] of Object.entries(formMap)) el(node).value = editSnapshot[key] ?? '';
    el('purchase-price').value = editSnapshot.purchase_cents === null ? '' : (editSnapshot.purchase_cents/100).toFixed(2);
    el('sale-price').value = editSnapshot.sale_cents === null ? '' : (editSnapshot.sale_cents/100).toFixed(2);
    el('editor-title').textContent = row?'这一件宝物':'收入一件宝物';
    el('delete').hidden = !row; el('form-error').textContent=''; el('reload-entry').hidden=true;
    el('purchased-on').max = el('sold-on').max = today();
    selectedIcon = editSnapshot.icon || 'sparkle'; photo=editSnapshot.photo || ''; pendingPhoto=null; resetPhotoPreview(); photoVersion++;
    saveAttempt=null; dirty=false; saleFields(); renderPhoto();
    if (!el('editor').open) el('editor').showModal();
    el('name').focus();
  }
  async function closeEditor() {
    if (pending || photoReading) return;
    if (dirty && !await window.tiangongConfirm('这件物品还有未保存的修改。先放弃修改？')) return;
    el('editor').close(); editSnapshot=null; dirty=false; saveAttempt=null; pendingPhoto=null; photoVersion++; resetPhotoPreview();
  }
  function saleFields() {
    const sold = el('item-status').value==='sold';
    el('sale-fields').hidden=!sold; el('sold-on').required=sold;
  }
  function busy(value) {
    pending=value;
    el('form').querySelectorAll('input,select,textarea,button').forEach(node=>{node.disabled=value;});
    el('refresh').disabled=value; render();
  }
  function buildPayload() {
    const uses = Number(el('uses').value);
    if (!/^\d+$/.test(el('uses').value) || !Number.isSafeInteger(uses) || uses>1000000000) throw new Error('使用次数请填写 0 或正整数。');
    const value={id:editSnapshot.id,revision:editSnapshot.revision,action:'save',icon:selectedIcon,photo,uses};
    for (const [key,node] of Object.entries(formMap)) if (key!=='uses') value[key]=el(node).value.trim();
    if (!value.name) throw new Error('先给这件物品起个名字。');
    value.purchase_cents=parsePrice(el('purchase-price').value);
    value.sale_cents=value.status==='sold'?parsePrice(el('sale-price').value):null;
    if (value.status!=='sold') value.sold_on='';
    if (value.purchased_on && (dayNumber(value.purchased_on)===null || value.purchased_on>today())) throw new Error('购入日期不能晚于今天；记不清可以留空。');
    if (value.status==='sold' && (!value.sold_on || dayNumber(value.sold_on)===null || value.sold_on>today() || value.purchased_on && value.sold_on<value.purchased_on)) throw new Error('请填写有效的售出日期，不能早于购入或晚于今天。');
    return value;
  }
  function attemptFor(data, previous) {
    const fingerprint = JSON.stringify(data);
    return previous?.fingerprint===fingerprint ? previous : {fingerprint,payload:{...data,request_id:newID()}};
  }
  el('form').addEventListener('submit',async event=>{
    event.preventDefault();
    if (!editSnapshot || pending || loading || photoReading) return;
    el('form-error').textContent=''; el('reload-entry').hidden=true;
    let data;
    try { data=buildPayload(); } catch(error) { el('form-error').textContent=error.message; return; }
    busy(true);
    try {
      if (pendingPhoto) {
        const uploaded=await request('/api/treasures/upload',{data_url:pendingPhoto.dataURL});
        const uploadedID=uploaded.asset?.id;
        if (!validPhoto(uploadedID)) throw new Error('照片上传回复未能确认，请保留草稿后重试。');
        photo=uploadedID; pendingPhoto=null; data.photo=photo;
      }
      saveAttempt=attemptFor(data,saveAttempt);
      const row=accept(await request('/api/treasures',saveAttempt.payload));
      dirty=false; editSnapshot=null; saveAttempt=null; resetPhotoPreview(); el('editor').close();
      options(); status('已保存「'+row.name+'」。');
    } catch(error) { el('form-error').textContent=error.message; el('reload-entry').hidden=error.code!==409; }
    finally { busy(false); }
  });
  async function changeRecord(id, action) {
    if (!ready || pending || loading) return;
    const row=records.find(r=>r.id===id); if (!row) return;
    if (action==='delete' && !await window.tiangongConfirm('把「'+row.name+'」移入回收站？以后可以恢复。')) return;
    const map=action==='use'?useAttempts:otherAttempts;
    const data={id,revision:action==='delete'&&editSnapshot?.id===id?editSnapshot.revision:row.revision};
    if (action==='use') {
      // A retry after midnight still belongs to the original use event.
      const previous=map.get(id)?.payload;
      data.date=previous?.revision===row.revision?previous.date:today();
    } else data.action=action;
    const attempt=attemptFor(data,map.get(id)); map.set(id,attempt); busy(true);
    try {
      const saved=accept(await request(action==='use'?'/api/treasures/use':'/api/treasures',attempt.payload));
      map.delete(id);
      if (action==='delete') { dirty=false; editSnapshot=null; resetPhotoPreview(); el('editor').close(); }
      options(); status(action==='use'?'已记下「'+saved.name+'」的一次使用。':action==='restore'?'已恢复「'+saved.name+'」。':'已移入回收站，可以随时恢复。');
    } catch(error) { if(action==='delete') { el('form-error').textContent=error.message;el('reload-entry').hidden=error.code!==409; } else status(error.message+(error.code===409?' 请刷新后再试。':''),true); }
    finally { busy(false); }
  }
  async function addPhoto(file) {
    if (!file || pending || !editSnapshot) return;
    if (!/\.(?:jpe?g|png|webp)$/i.test(file.name) || !['image/jpeg','image/png','image/webp',''].includes(file.type)) { el('form-error').textContent='请选择 JPG、PNG 或 WebP 图片。'; return; }
    if (!file.size || file.size>8*1024*1024) { el('form-error').textContent='照片不能超过 8 MB，请选一张较小的照片。'; return; }
    const version=++photoVersion; photoReading=true; el('save').disabled=true; el('form-error').textContent='';
    try {
      const dataURL=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('这张照片暂时无法读取，请重新选择。'));reader.readAsDataURL(file);});
      if (version!==photoVersion || !editSnapshot) return;
      resetPhotoPreview(); photoPreview=URL.createObjectURL(file); pendingPhoto={dataURL}; photo=''; dirty=true; renderPhoto();
    } catch(error) { el('form-error').textContent=error.message; }
    finally { if(version===photoVersion){photoReading=false;el('save').disabled=false;} }
  }
  el('reload-entry').onclick=async()=>{
    if (!editSnapshot || pending || loading) return;
    if (dirty && !await window.tiangongConfirm('重新读取会替换未保存的修改。继续？')) return;
    const id=editSnapshot.id;
    if (await global.loadTreasures()) {
      if (!records.some(r=>r.id===id&&!r.deletedAt)) { el('form-error').textContent='这件物品已被移入回收站，当前草稿仍保留。'; return; }
      dirty=false; openEditor(id);
    }
  };
  el('form').addEventListener('input',()=>{dirty=true;});
  el('form').addEventListener('change',()=>{dirty=true;});
  el('item-status').onchange=()=>{dirty=true;saleFields();};
  el('editor').addEventListener('cancel',event=>{event.preventDefault();closeEditor();});
  el('close').onclick=closeEditor; el('add').onclick=()=>openEditor(); el('refresh').onclick=()=>global.loadTreasures();
  el('delete').onclick=()=>editSnapshot&&changeRecord(editSnapshot.id,'delete');
  el('photo-add').onclick=()=>el('photo-input').click();
  el('photo-input').onchange=event=>{addPhoto(event.target.files?.[0]);event.target.value='';};
  el('photo-remove').onclick=()=>{if(pending)return;photoVersion++;photoReading=false;el('save').disabled=false;pendingPhoto=null;photo='';resetPhotoPreview();dirty=true;renderPhoto();};
  el('icon-options').onclick=event=>{const button=event.target.closest('[data-treasure-icon]');if(button&&!pending){selectedIcon=button.dataset.treasureIcon;dirty=true;renderPhoto();}};
  el('grid').onclick=event=>{
    const button=event.target.closest('button'); if(!button)return;
    if(button.hasAttribute('data-treasure-new'))openEditor();
    else if(button.dataset.treasureEdit)openEditor(button.dataset.treasureEdit);
    else if(button.dataset.treasureUse)changeRecord(button.dataset.treasureUse,'use');
    else if(button.dataset.treasureRestore)changeRecord(button.dataset.treasureRestore,'restore');
  };
  root.querySelectorAll('[data-treasure-view]').forEach(button=>button.onclick=()=>{view=button.dataset.treasureView;render();});
  el('search').oninput=render; el('category-filter').onchange=render; el('sort').onchange=render;
  global.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  renderPhoto();
})(typeof window!=='undefined'?window:globalThis);
