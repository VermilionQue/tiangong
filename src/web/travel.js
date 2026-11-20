'use strict';
// One shared travel journal. No invented visits or browser-only fallback records.
(() => {
  const root = document.getElementById('travel');
  const el = id => document.getElementById(id);
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let catalog, visits = [], scope = 'domestic', selected = null, snapshot = null;
  let ready = false, pending = false, dirty = false, loading = null, onlyVisited = false, refreshingEditor = false;
  let bounds = [0, 0, 1000, 650], view = [...bounds], drag = null, dragged = false;
  let photos = [], photoSequence = 0;
  const uncertainSaves = new Map();
  const photoId = value => typeof value === 'string' && /^[0-9a-f]{64}\.(jpg|png|webp)$/.test(value);
  const photoUrl = id => '/travel-assets/' + encodeURIComponent(id);
  const svg = el('travel-map');
  const record = id => visits.find(v => v.scope === scope && v.place_id === id);
  const places = () => (catalog?.places || []).filter(p => p.scope === scope);
  function visitDateLabel(value) {
    if (!value) return '日期未记';
    const parts = /^([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?$/.exec(value);
    if (!parts) return value;
    return parts[1] + '年' + (parts[2] ? Number(parts[2]) + '月' : '') + (parts[3] ? Number(parts[3]) + '日' : '');
  }
  function status(message, error = false) { el('travel-status').textContent = message; el('travel-status').classList.toggle('error', error); }
  async function request(path, data) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30000);
    let response;
    try {
      response = await fetch(path, {...(data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {cache:'no-store'}), signal:controller.signal});
      const body = await response.json();
      if (!response.ok) throw Object.assign(new Error(body.error || '暂时无法读取，请稍后重试。'), {code:response.status, uncertain:response.status >= 500});
      return body;
    } catch (error) {
      if (!error.code) error.uncertain = !response || response.ok || response.status >= 500;
      if (error.name === 'AbortError') error = Object.assign(new Error('连接超时，照片和小记仍留在这里，可以再次保存。'), {uncertain:true});
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function resetPhotos(ids = []) {
    for (const photo of photos) if (photo.localUrl) URL.revokeObjectURL(photo.localUrl);
    photos = ids.filter(photoId).map(id => ({key:++photoSequence, id, url:photoUrl(id)}));
    renderPhotos();
  }
  function renderPhotos() {
    if (!el('travel-photo-list')) return;
    el('travel-photo-count').textContent = `${photos.length} / 8`;
    el('travel-photo-add').disabled = pending || refreshingEditor || photos.length >= 8;
    el('travel-photo-list').innerHTML = photos.map((photo, index) => `<figure class="travel-photo-item"><a href="${safe(photo.url)}" target="_blank" rel="noopener noreferrer" aria-label="查看第 ${index+1} 张旅行照片"><img src="${safe(photo.url)}" alt="旅行照片 ${index+1}"></a><button type="button" class="travel-photo-remove" data-remove-photo="${photo.key}" aria-label="移除第 ${index+1} 张照片" ${pending || refreshingEditor?'disabled':''}>×</button>${photo.id?'':'<figcaption>待保存</figcaption>'}</figure>`).join('');
  }
  function addPhotos(files) {
    if (pending || refreshingEditor || !selected) return;
    const added = Array.from(files);
    el('travel-photo-file').value = '';
    if (!added.length) return;
    if (photos.length + added.length > 8) {el('travel-error').textContent = '每处旅行回忆最多添加 8 张照片。'; return;}
    if (added.some(file => !['image/jpeg','image/png','image/webp'].includes(file.type) || file.size > 8*1024*1024 || !file.size)) {
      el('travel-error').textContent = '请选择 JPG、PNG 或 WebP 照片，每张不超过 8 MB。'; return;
    }
    for (const file of added) {
      const url = URL.createObjectURL(file);
      photos.push({key:++photoSequence, file, url, localUrl:url});
    }
    dirty = true; el('travel-error').textContent = ''; renderPhotos();
  }
  const fileData = file => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('照片读取失败，请重新选择这张照片。'));
    reader.readAsDataURL(file);
  });
  async function uploadPhotos() {
    for (let i = 0; i < photos.length; i++) {
      const photo = photos[i];
      if (photo.id) continue;
      el('travel-error').textContent = `正在留存照片 ${i+1} / ${photos.length}…`;
      const {asset} = await request('/api/travel/upload', {data_url:await fileData(photo.file)});
      if (!photoId(asset?.id)) throw new Error('照片上传结果不完整，请重新保存。');
      photo.id = asset.id; photo.url = photoUrl(asset.id); photo.file = null;
      if (photo.localUrl) URL.revokeObjectURL(photo.localUrl);
      photo.localUrl = null; renderPhotos();
    }
    // Identical uploaded images share a content hash, even if filenames differ.
    photos = photos.filter((photo, index, all) => all.findIndex(other => other.id === photo.id) === index);
    renderPhotos();
    return photos.map(photo => photo.id);
  }
  window.loadTravel = async function({discardDraft = false} = {}) {
    document.querySelector('.demo-dot').textContent = '我的足迹';
    if (pending) return;
    if (loading) return loading;
    refreshingEditor = discardDraft;
    if (discardDraft) el('travel-form').querySelectorAll('input,textarea,button').forEach(input => {input.disabled = true;});
    loading = (async () => {
      try {
        const [meta, state] = await Promise.all([catalog || request('/assets/travel-places.json'), request('/api/travel')]);
        catalog = meta; visits = state.visits; ready = true;
        if (discardDraft) {dirty = false; if (selected) uncertainSaves.delete(scope+':'+selected.id);}
        render(!dirty); status('');
      } catch (e) { status('旅行记录暂时无法读取。' + e.message, true); }
      finally {
        loading = null; refreshingEditor = false;
        if (discardDraft) el('travel-form').querySelectorAll('input,textarea,button').forEach(input => {input.disabled = false;});
        renderPhotos();
      }
    })();
    return loading;
  };
  function counts() {
    for (const s of ['domestic','overseas']) el('travel-count-'+s).textContent = visits.filter(v => !v.deleted && v.scope === s).length;
  }
  function render(reset = false) {
    counts();
    root.querySelectorAll('[data-travel-scope]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.travelScope === scope)));
    el('travel-map-title').textContent = scope === 'domestic' ? '把走过的山河，慢慢点亮。' : '世界很大，一站一站地看。';
    el('travel-map-subtitle').textContent = scope === 'domestic' ? '按省级地区留下足迹，城市与回忆记在旁边。' : '点选国家或地区，把那些难忘的旅行收藏起来。';
    el('travel-search').placeholder = scope === 'domestic' ? '搜索省份或地区' : '搜索国家或地区 · 支持英文';
    el('travel-search').setAttribute('aria-label', el('travel-search').placeholder);
    const map = catalog.maps[scope];
    if (reset) { bounds = Array.isArray(map.viewBox) ? map.viewBox.map(Number) : map.viewBox.split(/\s+/).map(Number); view = [...bounds]; }
    svg.setAttribute('aria-label', scope === 'domestic' ? '国内旅行足迹地图' : '国外旅行足迹地图');
    svg.innerHTML = `<title>${scope === 'domestic' ? '国内' : '国外'}旅行足迹</title><g class="travel-context">${(map.context || []).map(p => `<path d="${safe(p.path)}"><title>${safe(p.name)}</title></path>`).join('')}</g><g>${places().map(p => {
      const visited = record(p.id); const on = visited && !visited.deleted;
      return `<path class="travel-region${on?' visited':''}${selected?.id===p.id?' selected':''}" d="${safe(p.path)}" data-place="${safe(p.id)}" role="button" tabindex="0" aria-label="${safe(p.name)}，${on?'已点亮':'未点亮'}" aria-pressed="${!!on}"><title>${safe(p.name)}${on?' · 已点亮':''}</title></path>`;
    }).join('')}</g>${scope === 'domestic' ? `<g class="travel-map-labels" aria-hidden="true">${places().map(p => `<text x="${Number(p.labelX)}" y="${Number(p.labelY)}">${safe(p.shortName || p.name.replace(/维吾尔自治区|壮族自治区|回族自治区|特别行政区|自治区|省|市/g,''))}</text>`).join('')}</g>` : ''}`;
    applyView(); renderList(); renderMemories();
    if (selected && !dirty) renderEditor();
  }
  function renderList() {
    const query = el('travel-search').value.trim().toLocaleLowerCase();
    const list = places().filter(p => (!onlyVisited || (record(p.id) && !record(p.id).deleted)) && [p.name,p.nameEn,p.continent,...(p.aliases || [])].join(' ').toLocaleLowerCase().includes(query));
    el('travel-place-count').textContent = `${list.length} 个地点`;
    el('travel-place-list').innerHTML = list.length ? list.map(p => {
      const on = record(p.id) && !record(p.id).deleted;
      return `<button type="button" data-place="${safe(p.id)}" class="travel-place${on?' visited':''}${selected?.id===p.id?' selected':''}" aria-pressed="${selected?.id===p.id}"><span class="travel-place-dot" aria-hidden="true">${on?'✓':'+'}</span>${safe(p.name)}</button>`;
    }).join('') : '<p class="travel-no-result">没有匹配的地点，试试别的名称。</p>';
  }
  function renderMemories() {
    const rows = visits.filter(v => v.scope === scope && !v.deleted).sort((a,b) => (b.visited_on || '').localeCompare(a.visited_on || '') || b.updated_at.localeCompare(a.updated_at));
    el('travel-memories-count').textContent = `${rows.length} 个地点`;
    el('travel-memories').innerHTML = rows.length ? rows.map(v => {
      const images = (v.photos || []).filter(photoId);
      return `<button class="travel-memory" data-place="${safe(v.place_id)}"><span class="travel-memory-pin" aria-hidden="true">✧</span><strong>${safe(v.place_name)}</strong><small>${safe(visitDateLabel(v.visited_on))}${v.city?' · '+safe(v.city):''}</small>${v.note?`<p>${safe(v.note)}</p>`:''}${images.length?`<span class="travel-memory-photos">${images.slice(0,3).map((id,i) => `<img src="${photoUrl(id)}" alt="${safe(v.place_name)}旅行照片 ${i+1}" loading="lazy">`).join('')}${images.length>3?`<span>+${images.length-3}</span>`:''}</span>`:''}<span class="travel-memory-edit">查看 / 编辑 ↗</span></button>`;
    }).join('') : '<div class="travel-empty-memory"><span aria-hidden="true">✧</span><p>第一枚旅行印记，等你来点亮。</p><small>从地图或地点列表中选择一个去过的地方。</small></div>';
  }
  async function selectPlace(id) {
    if (!ready || pending || refreshingEditor) return;
    if (dirty && selected?.id !== id && !await window.tiangongConfirm('这一处还有未保存的小记。放弃修改，切换地点？')) return;
    if (selected?.id === id && dirty) return;
    selected = places().find(p => p.id === id); if (!selected) return;
    dirty = false; renderEditor(); render(false);
    if (matchMedia('(max-width: 760px)').matches) el('travel-postcard').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth', block:'start'});
  }
  function renderEditor() {
    el('travel-select-hint').hidden = !!selected;
    el('travel-form').hidden = !selected;
    if (!selected) {resetPhotos(); return;}
    snapshot = record(selected.id) ? {...record(selected.id)} : null;
    el('travel-place-name').textContent = selected.name;
    el('travel-place-english').textContent = selected.nameEn || 'TRAVEL JOURNAL';
    el('travel-visited-on').value = snapshot?.visited_on || '';
    el('travel-city').value = snapshot?.city || '';
    el('travel-note').value = snapshot?.note || '';
    resetPhotos(snapshot?.photos || []);
    el('travel-stamp').textContent = snapshot && !snapshot.deleted ? '已经去过 ✓' : snapshot?.deleted ? '暂未点亮 · 回忆仍在' : '下一枚旅行印记';
    el('travel-save').textContent = snapshot && !snapshot.deleted ? '保存这段回忆' : snapshot?.deleted ? '重新点亮这里' : '我去过，点亮这里';
    el('travel-remove').hidden = !snapshot || !!snapshot.deleted;
    el('travel-error').textContent = '';
    el('travel-reload').hidden = true;
  }
  async function saveVisit(action) {
    if (!selected || pending || !ready) return;
    if (loading) {el('travel-error').textContent = '正在刷新记录，请稍后再保存。'; return;}
    if (action === 'delete' && dirty && !await window.tiangongConfirm('取消点亮会放弃这次尚未保存的修改，已保存的回忆会保留。继续？')) return;
    pending = true; el('travel-form').querySelectorAll('input,textarea,button').forEach(input => {input.disabled = true;});
    el('travel-error').textContent = '';
    const key = scope+':'+selected.id;
    try {
      if (uncertainSaves.has(key)) {
        const {entry} = await request('/api/travel', uncertainSaves.get(key));
        visits = visits.filter(v => v.id !== entry.id).concat(entry); snapshot = {...entry};
        uncertainSaves.delete(key);
      }
      const payload = {scope,place_id:selected.id,action:action === 'delete' ? 'delete' : snapshot?.deleted ? 'restore' : 'save',revision:snapshot?.revision || 0};
      if (action !== 'delete') Object.assign(payload, {visited_on:el('travel-visited-on').value,city:el('travel-city').value,note:el('travel-note').value,photos:await uploadPhotos()});
      el('travel-error').textContent = '';
      let entry;
      try {({entry} = await request('/api/travel', payload));}
      catch (error) {if (error.uncertain) uncertainSaves.set(key, payload); throw error;}
      visits = visits.filter(v => v.id !== entry.id).concat(entry); dirty = false;
      render(false); status(action === 'delete' ? '已取消点亮，原有城市、日期、小记和照片仍保留，可随时重新点亮。' : '已保存，其他设备打开旅行页即可看到。');
    } catch(e) {
      dirty = true;
      el('travel-error').textContent = e.message;
      el('travel-reload').hidden = e.code !== 409;
    } finally { pending = false; el('travel-form').querySelectorAll('input,textarea,button').forEach(input => {input.disabled = false;}); renderPhotos(); }
  }
  function applyView() { svg.setAttribute('viewBox', view.join(' ')); }
  function zoom(factor) {
    const w = Math.max(bounds[2]/6, Math.min(bounds[2],view[2]*factor));
    const h = w*bounds[3]/bounds[2]; view = [view[0]+(view[2]-w)/2,view[1]+(view[3]-h)/2,w,h]; clampView();
  }
  function clampView() {
    view[0] = Math.max(bounds[0],Math.min(bounds[0]+bounds[2]-view[2],view[0]));
    view[1] = Math.max(bounds[1],Math.min(bounds[1]+bounds[3]-view[3],view[1])); applyView();
  }
  root.addEventListener('click', event => {
    const place = event.target.closest('[data-place]');
    if (place) { if (dragged) { dragged = false; return; } selectPlace(place.dataset.place); }
  });
  svg.addEventListener('keydown', event => { if (event.target.matches('[data-place]') && ['Enter',' '].includes(event.key)) {event.preventDefault(); selectPlace(event.target.dataset.place);} });
  svg.addEventListener('pointerdown', event => {
    if (view[2] >= bounds[2] || event.button !== 0) return;
    drag = {x:event.clientX,y:event.clientY,view:[...view]}; dragged = false;
  });
  svg.addEventListener('pointermove', event => {
    if (!drag) return;
    const dx = event.clientX-drag.x,dy = event.clientY-drag.y;
    if (Math.abs(dx)+Math.abs(dy)>6) {dragged = true; svg.setPointerCapture(event.pointerId);}
    const rect = svg.getBoundingClientRect();
    view[0] = drag.view[0]-dx/rect.width*view[2]; view[1] = drag.view[1]-dy/rect.height*view[3]; clampView();
  });
  svg.addEventListener('pointerup', () => {drag = null;}); svg.addEventListener('pointercancel', () => {drag = null;dragged = false;});
  root.querySelectorAll('[data-travel-scope]').forEach(button => button.addEventListener('click', async () => {
    if (scope === button.dataset.travelScope || pending || refreshingEditor) return;
    if (dirty && !await window.tiangongConfirm('还有未保存的小记。放弃修改并切换地图？')) return;
    scope = button.dataset.travelScope; selected = null; snapshot = null; dirty = false;
    el('travel-search').value = ''; onlyVisited = false; el('travel-only-visited').checked = false;
    renderEditor(); if (catalog) render(true);
  }));
  el('travel-search').addEventListener('input', renderList);
  el('travel-only-visited').addEventListener('change', event => {onlyVisited = event.target.checked; renderList();});
  el('travel-form').addEventListener('input', () => {dirty = true;});
  el('travel-photo-add')?.addEventListener('click', () => el('travel-photo-file').click());
  el('travel-photo-file')?.addEventListener('change', event => addPhotos(event.target.files));
  el('travel-photo-list')?.addEventListener('click', event => {
    const button = event.target.closest('[data-remove-photo]');
    if (!button || pending || refreshingEditor) return;
    const photo = photos.find(item => item.key === Number(button.dataset.removePhoto));
    if (photo?.localUrl) URL.revokeObjectURL(photo.localUrl);
    photos = photos.filter(item => item !== photo); dirty = true; renderPhotos();
  });
  el('travel-form').addEventListener('submit', event => {event.preventDefault(); saveVisit(snapshot?.deleted?'restore':'save');});
  el('travel-remove').addEventListener('click', () => saveVisit('delete'));
  el('travel-reload').addEventListener('click', async () => {if (await window.tiangongConfirm('重新读取会替换当前未保存的小记，继续？')) await window.loadTravel({discardDraft:true});});
  el('travel-refresh').addEventListener('click', () => window.loadTravel());
  el('travel-zoom-in').addEventListener('click', () => zoom(.7));
  el('travel-zoom-out').addEventListener('click', () => zoom(1/.7));
  el('travel-reset').addEventListener('click', () => {view = [...bounds]; applyView();});
  window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
})();
