'use strict';
const noteParams = new URLSearchParams(location.search);
let noteRecords = [], noteTag = noteParams.get('tag') || '', noteLimit = 20;
let noteEditing = null, noteRequestId = null, noteRandom = null;
let noteTrash = noteParams.get('notes_view') === 'trash', noteDeleteTarget = null;
let noteDraftAttachments = [], noteSaving = false, noteLoaded = false, noteEditorEpoch = 0, noteLoadSequence = 0;
let noteTagOptions = [], noteTagIndex = 0, noteTagContext = null, noteComposing = false, noteDropDepth = 0;
let noteURLSeeded = false;
let noteSavingId = null;
const noteUnconfirmedSaves = new Map();
const NOTE_MAX_IMAGE_BYTES = 12 * 1024 * 1024, NOTE_MAX_NEW_IMAGES = 8, NOTE_MAX_ASSETS = 24;
const noteImageExtensions = /\.(jpe?g|png|gif|webp)$/i;

function noteAssetURL(asset) {
  return '/api/notes/asset/' + asset.key.split('/').map(encodeURIComponent).join('/');
}
function noteDraftSignature() {
  return JSON.stringify({html: $('note-input').innerHTML,
    attachments: noteDraftAttachments.map(asset => asset.key ? {key: asset.key} : {id: asset.draftId, data: asset.data, state: asset.state})});
}
function noteHasDraft() {
  const original = noteEditing?.html || '';
  const originalKeys = (noteEditing?.attachments || []).map(asset => asset.key);
  if (!noteEditing && !noteDraftAttachments.length && ['', noteTag ? '#' + noteTag : ''].includes($('note-input').textContent.trim())) return false;
  return $('note-input').innerHTML !== original || JSON.stringify(noteDraftAttachments.map(asset => asset.key || asset.draftId)) !== JSON.stringify(originalKeys);
}
async function confirmNoteDraft() {
  return !noteHasDraft() || await window.tiangongConfirm('这条笔记还有未保存的内容或图片。放弃修改并继续？');
}
function updateNoteSaveState() {
  $('note-save').disabled = !noteLoaded || noteSaving || noteDraftAttachments.some(asset => asset.state === 'reading');
}
function releaseNoteAssets(assets = noteDraftAttachments) {
  assets.forEach(asset => { if (asset.previewURL?.startsWith('blob:')) URL.revokeObjectURL(asset.previewURL); });
}
function renderNoteDraftAssets() {
  const container = $('note-attachments');
  container.replaceChildren();
  noteDraftAttachments.forEach(asset => {
    const card = document.createElement('div'); card.className = 'note-draft-asset';
    if (noteImageExtensions.test(asset.name || asset.key || '') || ['.jpg', '.jpeg', '.png', '.gif', '.webp'].includes(asset.kind)) {
      const image = document.createElement('img'); image.src = asset.previewURL || noteAssetURL(asset); image.alt = asset.name || '笔记图片'; card.append(image);
    } else {
      const name = document.createElement('span'); name.textContent = asset.name || '笔记附件'; card.append(name);
    }
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'note-draft-remove';
    remove.textContent = '×'; remove.setAttribute('aria-label', '移除附件：' + (asset.name || '图片'));
    remove.onclick = () => { noteDraftAttachments = noteDraftAttachments.filter(item => item.draftId !== asset.draftId); releaseNoteAssets([asset]); renderNoteDraftAssets(); };
    card.append(remove);
    if (asset.state === 'reading' || asset.state === 'error') {
      const status = document.createElement('small'); status.textContent = asset.state === 'reading' ? '读取中…' : '读取失败，请移除后重试'; card.append(status);
    }
    container.append(card);
  });
  container.hidden = noteDraftAttachments.length === 0;
  updateNoteSaveState();
}
async function addNoteImages(files) {
  const incoming = Array.from(files || []);
  if (!incoming.length) return;
  const currentNew = noteDraftAttachments.filter(asset => !asset.key);
  if (incoming.some(file => !noteImageExtensions.test(file.name) || !file.size)) {
    $('note-error').textContent = '请添加 JPG、PNG、GIF 或 WebP 图片。'; return;
  }
  if (currentNew.length + incoming.length > NOTE_MAX_NEW_IMAGES || noteDraftAttachments.length + incoming.length > NOTE_MAX_ASSETS) {
    $('note-error').textContent = '一次最多添加 8 张新图片，每条笔记最多保留 24 份附件。'; return;
  }
  if (currentNew.reduce((total, asset) => total + asset.size, 0) + incoming.reduce((total, file) => total + file.size, 0) > NOTE_MAX_IMAGE_BYTES) {
    $('note-error').textContent = '本次添加的图片合计不能超过 12 MB。'; return;
  }
  $('note-error').textContent = '';
  const additions = incoming.map(file => ({draftId: uid(), name: file.name, size: file.size,
    previewURL: URL.createObjectURL(file), state: 'reading', data: null}));
  noteDraftAttachments.push(...additions); renderNoteDraftAssets();
  await Promise.all(additions.map((asset, index) => new Promise(resolve => {
    const reader = new FileReader();
    reader.onload = () => {
      if (noteDraftAttachments.includes(asset)) { asset.data = String(reader.result).split(',', 2)[1]; asset.state = 'ready'; renderNoteDraftAssets(); }
      resolve();
    };
    reader.onerror = () => {
      if (noteDraftAttachments.includes(asset)) { asset.state = 'error'; $('note-error').textContent = '图片读取失败，文字和其他图片已保留。'; renderNoteDraftAssets(); }
      resolve();
    };
    reader.readAsDataURL(incoming[index]);
  })));
}

async function loadNotes() {
  const sequence = ++noteLoadSequence;
  document.querySelector('.demo-dot').textContent = '我的笔记';
  $('subtitle').textContent = '把一闪而过的想法，留在这里。';
  try {
    const res = await fetch('/api/notes', {cache: 'no-store'});
    if (!res.ok) throw Error();
    const body = await res.json();
    if (sequence !== noteLoadSequence) return false;
    noteRecords = body.notes; noteLoaded = true;
    $('notes-source').textContent = noteTrash ? '删除的笔记会留在这里，随时可以恢复。' : '记录想法，也让同一个主题慢慢生长。';
    updateNoteSaveState();
    if (!noteURLSeeded) { noteURLSeeded = true; ensureNoteComposerTag(noteTag); }
    renderNotes();
    return true;
  } catch {
    if (sequence !== noteLoadSequence) return false;
    $('notes-source').textContent = '暂时无法读取笔记，请检查连接后重新打开。离线文件不包含私人笔记。';
    noteLoaded = false; updateNoteSaveState();
    return false;
  }
}

function noteHasTag(record, tag) {
  return record.tags.some(value => value === tag || value.startsWith(tag + '/'));
}
function noteTagURL(tag) {
  const url = new URL(location.href);
  url.searchParams.delete('notes_view');
  if (tag) url.searchParams.set('tag', tag); else url.searchParams.delete('tag');
  url.hash = 'notes';
  return url.href;
}
function updateNoteURL() {
  const url = new URL(noteTagURL(noteTag));
  if (noteTrash) url.searchParams.set('notes_view', 'trash');
  try { history.replaceState(null, '', url); } catch { /* File previews may disallow history writes. */ }
}
function selectNoteTag(tag) {
  noteTag = tag;
  noteTrash = false;
  noteRandom = null;
  noteLimit = 20;
  $('notes-search').value = '';
  ensureNoteComposerTag(tag);
  updateNoteURL();
  renderNotes();
  $('notes-list-title').scrollIntoView({block: 'nearest'});
}
function ensureNoteComposerTag(tag) {
  if (!tag || /[\s#<>，。！？,;；：:]/u.test(tag)) return;
  const editor = $('note-input'), existing = Array.from(editor.textContent.matchAll(/(?:^|[^\p{L}\p{N}_])#([^\s#<>，。！？,;；：:]+)/gu), match => match[1]);
  if (existing.includes(tag)) return;
  editor.append(document.createTextNode((editor.textContent && !/\s$/.test(editor.textContent) ? ' ' : '') + '#' + tag + '\u00a0'));
}
function closeNoteTagPicker() {
  $('note-tag-picker').hidden = true; $('note-input').setAttribute('aria-expanded', 'false');
  $('note-input').removeAttribute('aria-activedescendant'); noteTagContext = null; noteTagOptions = [];
}
function noteTextRange(start, end) {
  const editor = $('note-input'), walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT), range = document.createRange();
  let offset = 0, node, foundStart = false;
  while ((node = walker.nextNode())) {
    const next = offset + node.textContent.length;
    if (!foundStart && start <= next) { range.setStart(node, Math.max(0, start - offset)); foundStart = true; }
    if (foundStart && end <= next) { range.setEnd(node, Math.max(0, end - offset)); return range; }
    offset = next;
  }
  return null;
}
function currentNoteTag() {
  const editor = $('note-input'), selection = window.getSelection();
  if (!selection?.rangeCount || !selection.isCollapsed) return null;
  const caret = selection.getRangeAt(0);
  if (!editor.contains(caret.startContainer)) return null;
  const parent = caret.startContainer.nodeType === Node.TEXT_NODE ? caret.startContainer.parentElement : caret.startContainer;
  const block = parent.closest('p,div,li,blockquote,pre') || editor;
  const prefix = caret.cloneRange(); prefix.selectNodeContents(editor.contains(block) ? block : editor); prefix.setEnd(caret.startContainer, caret.startOffset);
  // A paragraph or soft line break is a tag boundary even though textContent
  // omits it. Offsets for replacement still use the unmodified text nodes.
  const fragment = prefix.cloneContents();
  fragment.querySelectorAll('br').forEach(br => br.replaceWith(document.createTextNode('\n')));
  const match = /(?:^|[^\p{L}\p{N}_])#([^\s#<>，。！？,;；：:]*)$/u.exec(fragment.textContent);
  if (!match) return null;
  const fullPrefix = caret.cloneRange(); fullPrefix.selectNodeContents(editor); fullPrefix.setEnd(caret.startContainer, caret.startOffset);
  const end = fullPrefix.toString().length, start = end - match[1].length - 1;
  const suffix = /^[^\s#<>，。！？,;；：:]*/u.exec(editor.textContent.slice(end))[0];
  const range = noteTextRange(start, end + suffix.length);
  return range ? {query: match[1], range} : null;
}
function knownNoteTags() {
  const values = new Set();
  noteRecords.filter(record => !record.deleted).forEach(record => record.tags.forEach(tag => {
    const parts = tag.split('/'); for (let i = 1; i <= parts.length; i++) values.add(parts.slice(0, i).join('/'));
  }));
  return [...values].sort((a, b) => a.localeCompare(b, 'zh-CN'));
}
function renderNoteTagPicker() {
  const picker = $('note-tag-picker'); picker.replaceChildren();
  if (!noteTagOptions.length) {
    const empty = document.createElement('p'); empty.className = 'note-tag-empty'; empty.textContent = '继续输入，写下一个新标签'; picker.append(empty);
    $('note-input').removeAttribute('aria-activedescendant'); return;
  }
  noteTagOptions.forEach((tag, index) => {
    const option = document.createElement('button'); option.type = 'button'; option.className = 'note-tag-option' + (index === noteTagIndex ? ' selected' : '');
    option.id = 'note-tag-option-' + index; option.setAttribute('role', 'option'); option.setAttribute('aria-selected', String(index === noteTagIndex)); option.textContent = '#' + tag;
    option.onmousedown = event => event.preventDefault(); option.onclick = () => chooseNoteTag(tag); picker.append(option);
  });
  $('note-input').setAttribute('aria-activedescendant', 'note-tag-option-' + noteTagIndex);
}
function refreshNoteTagPicker() {
  if (noteComposing) return;
  const context = currentNoteTag();
  if (!context) { closeNoteTagPicker(); return; }
  noteTagContext = context; noteTagIndex = 0;
  noteTagOptions = knownNoteTags().filter(tag => tag.toLocaleLowerCase().includes(context.query.toLocaleLowerCase())).slice(0, 20);
  renderNoteTagPicker(); $('note-tag-picker').hidden = false; $('note-input').setAttribute('aria-expanded', 'true');
}
function insertNoteText(text, range) {
  const editor = $('note-input'), selection = window.getSelection();
  if (!range) {
    range = selection?.rangeCount && editor.contains(selection.getRangeAt(0).commonAncestorContainer) ? selection.getRangeAt(0).cloneRange() : document.createRange();
    if (!editor.contains(range.commonAncestorContainer)) { range.selectNodeContents(editor); range.collapse(false); }
  }
  range.deleteContents(); const node = document.createTextNode(text); range.insertNode(node); range.setStartAfter(node); range.collapse(true);
  editor.focus(); selection.removeAllRanges(); selection.addRange(range);
}
function chooseNoteTag(tag) {
  const context = noteTagContext;
  if (!context || !context.range.startContainer.isConnected || !$('note-input').contains(context.range.startContainer)) { closeNoteTagPicker(); return; }
  // Chrome drops a trailing collapsible space when typing at a DOM boundary.
  // A non-breaking space keeps the chosen hashtag separate from the next word.
  insertNoteText('#' + tag + '\u00a0', context.range); closeNoteTagPicker();
}
function tagLink(tag) {
  const link = document.createElement('a');
  link.className = 'note-inline-tag';
  link.dataset.filterTag = tag;
  link.href = noteTagURL(tag);
  link.textContent = '#' + tag;
  link.title = '查看 #' + tag + ' 下的笔记';
  return link;
}
function linkNoteTags(container, tags) {
  const known = new Set(tags);
  // Exported flomo hashtags can already be anchors. Replace only a whole tag,
  // keeping ordinary external links intact and never altering the stored HTML.
  container.querySelectorAll('a').forEach(anchor => {
    const tag = anchor.textContent.trim().slice(1);
    if (anchor.textContent.trim().startsWith('#') && known.has(tag)) anchor.replaceWith(tagLink(tag));
  });
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement.closest('a,code,pre,button') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
    }
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const pattern = /(^|[^\p{L}\p{N}_])#([^\s#<>，。！？,;；：:]+)/gu;
    const fragment = document.createDocumentFragment();
    let offset = 0, matched = false;
    for (const match of node.textContent.matchAll(pattern)) {
      if (!known.has(match[2])) continue;
      const start = match.index + match[1].length;
      fragment.append(document.createTextNode(node.textContent.slice(offset, start)), tagLink(match[2]));
      offset = start + match[2].length + 1;
      matched = true;
    }
    if (matched) {
      fragment.append(document.createTextNode(node.textContent.slice(offset)));
      node.replaceWith(fragment);
    }
  }
}
function noteAssets(record) {
  if (!record.attachments.length) return '';
  return '<div class="note-assets">' + record.attachments.map(asset => {
    const url = noteAssetURL(asset);
    if (['.jpg', '.jpeg', '.png', '.webp', '.gif'].includes(asset.kind)) return `<a href="${url}" target="_blank" rel="noopener"><img src="${url}" loading="lazy" alt="笔记附件"></a>`;
    if (['.mp4', '.mov'].includes(asset.kind)) return `<div class="note-video"><video controls preload="metadata" src="${url}"></video><a href="${url}" download>下载原视频</a></div>`;
    if (['.m4a', '.mp3', '.wav'].includes(asset.kind)) return `<audio controls preload="none" src="${url}"></audio>`;
    return `<a href="${url}" target="_blank" rel="noopener">${escape(asset.name)}</a>`;
  }).join('') + '</div>';
}
function renderNotes() {
  const active = noteRecords.filter(record => !record.deleted), counts = {};
  for (const record of active) {
    // Each parent includes its descendants, but a note counts only once per tag.
    const allTags = new Set();
    for (const tag of record.tags) {
      const parts = tag.split('/');
      for (let i = 1; i <= parts.length; i++) allTags.add(parts.slice(0, i).join('/'));
    }
    for (const tag of allTags) counts[tag] = (counts[tag] || 0) + 1;
  }
  const days = new Set(active.map(record => record.created.slice(0, 10)));
  $('notes-stats').innerHTML = `<div><b>${active.length}</b><span>笔记</span></div><div><b>${Object.keys(counts).length}</b><span>标签</span></div><div><b>${days.size}</b><span>记录天数</span></div>`;
  $('notes-tags').innerHTML = Object.entries(counts).sort((a, b) => a[0].localeCompare(b[0], 'zh-CN')).map(([tag, count]) => `<button class="note-tag ${tag === noteTag && !noteTrash ? 'selected' : ''}" data-tag="${escape(tag)}"><span># ${escape(tag)}</span><small>${count}</small></button>`).join('');
  $('notes-tags').querySelectorAll('button').forEach(button => button.onclick = () => selectNoteTag(button.dataset.tag));
  $('notes-all').classList.toggle('selected', !noteTag && !noteRandom && !noteTrash);
  $('notes-random').classList.toggle('selected', !!noteRandom && !noteTrash);
  $('notes-trash').classList.toggle('selected', noteTrash);
  $('notes-trash').textContent = '回收站' + (noteRecords.length > active.length ? ` · ${noteRecords.length - active.length}` : '');
  $('notes-list-title').textContent = noteTrash ? '回收站' : noteRandom ? '随机回顾' : noteTag ? '# ' + noteTag : '全部笔记';
  $('note-form').hidden = noteTrash;
  $('notes-source').textContent = noteTrash ? '删除的笔记会留在这里，随时可以恢复。' : '记录想法，也让同一个主题慢慢生长。';
  $('notes-heatmap').innerHTML = Array.from({length: 84}, (_, i) => {
    const day = new Date(Date.parse(todayCN + 'T00:00:00Z') - (83 - i) * 86400000).toISOString().slice(0, 10);
    const count = active.filter(record => record.created.startsWith(day)).length;
    return `<span class="note-heat level-${Math.min(count, 3)}" title="${day} · ${count} 条"></span>`;
  }).join('');
  const query = $('notes-search').value.trim().toLowerCase();
  const list = noteRecords.filter(record => Boolean(record.deleted) === noteTrash && (!noteTag || noteHasTag(record, noteTag)) && (!query || record.text.toLowerCase().includes(query)) && (!noteRandom || record.id === noteRandom));
  $('notes-filter-summary').textContent = (noteTag && !noteTrash ? '全部笔记 / #' + noteTag + ' · ' : '') + list.length + ' 条笔记';
  $('notes-feed').innerHTML = list.slice(0, noteLimit).map(record => `<article class="note-card ${record.deleted ? 'note-card-deleted' : ''}" id="memo-${escape(record.id)}"><div class="note-card-top"><time>${escape(record.created.slice(0, 16))}</time>${record.deleted ? `<button class="text-button note-restore" data-restore-note="${escape(record.id)}">恢复笔记</button>` : `<details class="note-actions"><summary aria-label="笔记操作" title="更多操作">···</summary><div class="note-action-menu"><button type="button" data-edit-note="${escape(record.id)}">编辑笔记</button><button type="button" class="note-delete" data-delete-note="${escape(record.id)}">删除笔记</button></div></details>`}</div><div class="note-content">${record.html}</div>${noteAssets(record)}<div class="note-tags-row">${record.tags.map(tag => `<button data-filter-tag="${escape(tag)}">#${escape(tag)}</button>`).join('')}</div>${record.deleted ? `<p class="note-deleted-date">${escape((record.deleted_at || record.updated).slice(0, 16))} 移入回收站</p>` : ''}</article>`).join('') || `<div class="report-empty">${noteTrash ? '回收站是空的。' : noteTag ? '这个标签下还没有匹配的笔记。' : '没有找到笔记，换个关键词试试。'}</div>`;
  for (const record of list.slice(0, noteLimit)) linkNoteTags($('memo-' + record.id).querySelector('.note-content'), record.tags);
  $('notes-more').hidden = list.length <= noteLimit;
  $('notes-feed').querySelectorAll('[data-edit-note]').forEach(button => button.onclick = () => editNote(button.dataset.editNote));
  $('notes-feed').querySelectorAll('[data-delete-note]').forEach(button => button.onclick = () => askDeleteNote(button.dataset.deleteNote));
  $('notes-feed').querySelectorAll('[data-restore-note]').forEach(button => button.onclick = () => restoreNote(button));
  $('notes-feed').querySelectorAll('[data-filter-tag]').forEach(button => button.onclick = event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    selectNoteTag(button.dataset.filterTag);
  });
  $('notes-feed').querySelectorAll('.note-actions').forEach(menu => menu.addEventListener('toggle', () => {
    if (menu.open) $('notes-feed').querySelectorAll('.note-actions[open]').forEach(other => { if (other !== menu) other.open = false; });
  }));
}
async function editNote(id) {
  if (noteSaving && noteSavingId === id) { toast('这条笔记正在保存，请稍等。'); return; }
  const record = noteRecords.find(item => item.id === id);
  if (!record || record.deleted || !await confirmNoteDraft()) return;
  ++noteEditorEpoch; releaseNoteAssets(); closeNoteTagPicker();
  noteEditing = record;
  noteRequestId = noteEditing.id;
  noteDraftAttachments = noteEditing.attachments.map(asset => ({...asset, draftId: uid()}));
  $('note-input').innerHTML = noteEditing.html;
  renderNoteDraftAssets(); $('note-error').textContent = '';
  $('note-save').textContent = '保存修改';
  $('note-cancel').hidden = false;
  $('note-input').focus();
  $('note-form').scrollIntoView({block: 'center', behavior: 'smooth'});
  document.querySelectorAll('.note-actions[open]').forEach(menu => menu.open = false);
}
function resetNoteEditor() {
  ++noteEditorEpoch; releaseNoteAssets(); noteDraftAttachments = []; closeNoteTagPicker();
  noteEditing = null;
  noteRequestId = null;
  $('note-input').innerHTML = '';
  $('note-save').textContent = '记录';
  $('note-cancel').hidden = true;
  $('note-error').textContent = '';
  ensureNoteComposerTag(noteTag); renderNoteDraftAssets();
}
async function postNote(data) {
  let res, result;
  try {
    res = await fetch('/api/notes', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    result = await res.json();
  } catch {
    const error = Error('连接中断，文字和图片已保留。可以重新点击保存重试。'); error.noteUncertain = true; throw error;
  }
  if (!res.ok) { const error = Error(result.error || '保存失败'); error.noteUncertain = res.status >= 500 || res.status === 408; throw error; }
  if (!result.entry?.id) { const error = Error('暂未确认保存结果，草稿已保留，请重试。'); error.noteUncertain = true; throw error; }
  ++noteLoadSequence;
  noteRecords = noteRecords.filter(record => record.id !== result.entry.id).concat(result.entry).sort((a, b) => b.created.localeCompare(a.created));
  return result.entry;
}
async function saveNoteDraftPayload(payload, epoch) {
  const pending = noteUnconfirmedSaves.get(payload.id);
  if (pending) {
    let confirmed;
    try { confirmed = await postNote(pending); }
    catch (error) { if (!error.noteUncertain) noteUnconfirmedSaves.delete(payload.id); throw error; }
    noteUnconfirmedSaves.delete(payload.id);
    if (epoch === noteEditorEpoch) {
      noteEditing = confirmed; $('note-save').textContent = '保存修改'; $('note-cancel').hidden = false;
    }
    // Recover a lost response with the exact prior request before submitting
    // subsequent edits. This retrieves its revision without guessing whether
    // the earlier request reached the server.
    if (payload.html === pending.html && JSON.stringify(payload.attachments) === JSON.stringify(pending.attachments)) return confirmed;
    payload = {...payload, revision: confirmed.revision};
  }
  try { return await postNote(payload); }
  catch (error) { if (error.noteUncertain) noteUnconfirmedSaves.set(payload.id, payload); throw error; }
}
function askDeleteNote(id) {
  noteDeleteTarget = noteRecords.find(record => record.id === id);
  if (!noteDeleteTarget) return;
  document.querySelectorAll('.note-actions[open]').forEach(menu => menu.open = false);
  $('note-delete-preview').textContent = noteDeleteTarget.text.slice(0, 100);
  $('note-delete-error').textContent = '';
  $('note-delete-confirm').disabled = false;
  $('note-delete-dialog').showModal();
}
async function restoreNote(button) {
  const record = noteRecords.find(item => item.id === button.dataset.restoreNote);
  if (!record) return;
  button.disabled = true;
  try {
    await postNote({id: record.id, revision: record.revision, action: 'restore'});
    await loadNotes();
    toast('笔记已恢复，可以在全部笔记中查看');
  } catch (error) { toast(error.message); button.disabled = false; }
}

const trashButton = document.createElement('button');
trashButton.id = 'notes-trash';
trashButton.className = 'notes-nav';
trashButton.textContent = '回收站';
$('notes-export').before(trashButton);
$('notes-export').textContent = '↓ 导出笔记备份';
const filterSummary = document.createElement('p');
filterSummary.id = 'notes-filter-summary';
filterSummary.className = 'notes-filter-summary';
filterSummary.setAttribute('role', 'status');
$('notes-feed').before(filterSummary);
const deleteDialog = document.createElement('dialog');
deleteDialog.id = 'note-delete-dialog';
deleteDialog.setAttribute('aria-labelledby', 'note-delete-title');
deleteDialog.innerHTML = '<h2 id="note-delete-title">删除这条笔记？</h2><p>笔记会移入回收站，之后可以恢复。</p><blockquote id="note-delete-preview"></blockquote><p id="note-delete-error" role="alert"></p><div class="note-dialog-actions"><button type="button" id="note-delete-cancel">取消</button><button type="button" id="note-delete-confirm" class="note-delete-confirm">移入回收站</button></div>';
document.body.append(deleteDialog);
$('note-delete-cancel').onclick = () => deleteDialog.close();
$('note-delete-confirm').onclick = async () => {
  const record = noteDeleteTarget;
  if (!record) return;
  $('note-delete-confirm').disabled = true;
  try {
    await postNote({id: record.id, revision: record.revision, action: 'delete'});
    if (noteEditing?.id === record.id) resetNoteEditor();
    deleteDialog.close();
    await loadNotes();
    toast('已移入回收站，随时可以恢复');
  } catch (error) { $('note-delete-error').textContent = error.message; $('note-delete-confirm').disabled = false; }
};
$('note-form').onsubmit = async event => {
  event.preventDefault();
  if (noteSaving) return;
  if (noteDraftAttachments.some(asset => ['reading', 'error'].includes(asset.state))) { $('note-error').textContent = '请等待图片读取完成；读取失败的图片可以移除后重新添加。'; return; }
  if (!$('note-input').textContent.trim() && !noteDraftAttachments.length) { toast('写下一点想法，或放入一张图片吧'); return; }
  noteSaving = true; updateNoteSaveState(); closeNoteTagPicker();
  noteRequestId = noteRequestId || uid();
  noteSavingId = noteRequestId;
  const epoch = noteEditorEpoch, signature = noteDraftSignature();
  const payload = {id: noteRequestId, revision: noteEditing?.revision, html: $('note-input').innerHTML,
    attachments: noteDraftAttachments.map(asset => asset.key ? {key: asset.key} : {name: asset.name, data: asset.data})};
  try {
    const saved = await saveNoteDraftPayload(payload, epoch);
    let keptDraft = false;
    if (epoch === noteEditorEpoch) {
      if (signature === noteDraftSignature()) resetNoteEditor();
      else {
        // Keep text/images added or removed while the request was in flight.
        // Re-sending an uploaded image is safe: the backend addresses it by hash.
        noteEditing = saved; noteRequestId = saved.id; keptDraft = true;
        $('note-save').textContent = '保存修改'; $('note-cancel').hidden = false;
      }
    }
    await loadNotes();
    toast(keptDraft ? '已保存提交时的内容，后续修改仍在编辑区。' : '笔记已保存');
  } catch (error) { if (epoch === noteEditorEpoch) $('note-error').textContent = error.message; else toast(error.message); }
  finally { noteSaving = false; noteSavingId = null; updateNoteSaveState(); }
};
$('note-cancel').onclick = async () => { if (await confirmNoteDraft()) resetNoteEditor(); };
$('note-tag-add').onmousedown = event => event.preventDefault();
$('note-tag-add').onclick = () => {
  if (noteComposing) return;
  if (currentNoteTag()) { refreshNoteTagPicker(); return; }
  const editor = $('note-input'), selection = window.getSelection();
  const caret = selection?.rangeCount && editor.contains(selection.getRangeAt(0).commonAncestorContainer) ? selection.getRangeAt(0).cloneRange() : document.createRange();
  if (!editor.contains(caret.commonAncestorContainer)) caret.selectNodeContents(editor);
  caret.collapse(false);
  const prefix = caret.cloneRange(); prefix.selectNodeContents(editor); prefix.setEnd(caret.startContainer, caret.startOffset);
  insertNoteText((/[\p{L}\p{N}_]$/u.test(prefix.toString()) ? ' ' : '') + '#', caret); refreshNoteTagPicker();
};
$('note-input').addEventListener('input', refreshNoteTagPicker);
$('note-input').addEventListener('mouseup', refreshNoteTagPicker);
$('note-input').addEventListener('keyup', event => {
  if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) refreshNoteTagPicker();
});
$('note-input').addEventListener('compositionstart', () => { noteComposing = true; closeNoteTagPicker(); });
$('note-input').addEventListener('compositionend', () => { noteComposing = false; refreshNoteTagPicker(); });
$('note-input').addEventListener('keydown', event => {
  if (noteComposing || event.isComposing || event.keyCode === 229 || $('note-tag-picker').hidden) return;
  if (event.key === 'Escape') { event.preventDefault(); closeNoteTagPicker(); return; }
  if (!noteTagOptions.length) return;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); noteTagIndex = (noteTagIndex + (event.key === 'ArrowDown' ? 1 : -1) + noteTagOptions.length) % noteTagOptions.length; renderNoteTagPicker();
    $('note-tag-option-' + noteTagIndex).scrollIntoView({block: 'nearest'});
  } else if (event.key === 'Enter' || event.key === 'Tab') {
    event.preventDefault(); chooseNoteTag(noteTagOptions[noteTagIndex]);
  }
});
$('note-image-add').onclick = () => $('note-image-input').click();
$('note-image-input').onchange = event => { addNoteImages(event.target.files); event.target.value = ''; };
const noteFileDrag = event => Array.from(event.dataTransfer?.types || []).includes('Files');
$('note-form').addEventListener('dragenter', event => {
  if (!noteFileDrag(event)) return; event.preventDefault(); noteDropDepth++; $('note-form').classList.add('is-dragover');
});
$('note-form').addEventListener('dragover', event => {
  if (!noteFileDrag(event)) return; event.preventDefault(); event.dataTransfer.dropEffect = 'copy';
});
$('note-form').addEventListener('dragleave', event => {
  if (!noteFileDrag(event)) return; noteDropDepth = Math.max(0, noteDropDepth - 1); if (!noteDropDepth) $('note-form').classList.remove('is-dragover');
});
$('note-form').addEventListener('drop', event => {
  if (!event.dataTransfer?.files.length) return; event.preventDefault(); noteDropDepth = 0; $('note-form').classList.remove('is-dragover'); addNoteImages(event.dataTransfer.files);
});
$('note-input').addEventListener('paste', event => {
  const images = Array.from(event.clipboardData?.files || []).filter(file => noteImageExtensions.test(file.name));
  if (images.length) { event.preventDefault(); addNoteImages(images); }
});
document.querySelectorAll('[data-format]').forEach(button => {
  button.onmousedown = event => event.preventDefault();
  button.onclick = () => { $('note-input').focus(); document.execCommand(button.dataset.format); };
});
$('notes-search').oninput = () => { noteRandom = null; noteLimit = 20; renderNotes(); };
$('notes-all').onclick = () => selectNoteTag('');
$('notes-trash').onclick = () => {
  noteTag = ''; noteRandom = null; noteTrash = true; noteLimit = 20;
  $('notes-search').value = '';
  updateNoteURL(); renderNotes();
};
$('notes-random').onclick = () => {
  const active = noteRecords.filter(record => !record.deleted);
  if (!active.length) return;
  noteTag = ''; noteTrash = false; noteLimit = 20;
  $('notes-search').value = '';
  noteRandom = active[Math.floor(Math.random() * active.length)].id;
  updateNoteURL(); renderNotes();
};
$('notes-more').onclick = () => { noteLimit += 20; renderNotes(); };
$('notes-export').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({version: 2, notes: noteRecords}, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = '天工录_笔记_' + Date.now() + '.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('已导出笔记文字、回收记录和附件索引；原附件仍在本机');
};
document.addEventListener('click', event => {
  document.querySelectorAll('.note-actions[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  if (!$('note-tag-picker').contains(event.target) && !$('note-input').contains(event.target) && !$('note-tag-add').contains(event.target)) closeNoteTagPicker();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') document.querySelectorAll('.note-actions[open]').forEach(menu => menu.open = false);
});
window.addEventListener('beforeunload', event => {
  if (noteHasDraft() || noteSaving) { event.preventDefault(); event.returnValue = ''; }
});
// Recover an attachment request interrupted by a brief service restart or network drop.
document.addEventListener('error', event => {
  const img = event.target;
  if (!(img instanceof HTMLImageElement) || !img.closest('.note-assets')) return;
  const attempts = Number(img.dataset.retryCount || 0);
  if (attempts >= 2) { img.alt = '图片暂未加载，点击查看原图'; return; }
  img.dataset.retryCount = String(attempts + 1);
  setTimeout(() => { if (img.isConnected) img.src = img.getAttribute('src'); }, 1000 * (attempts + 1));
}, true);
