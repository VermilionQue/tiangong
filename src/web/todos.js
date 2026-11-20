'use strict';
let tasks=[],todoFilter='open',editingTodo=null,quickId=null,somedayId=null,todoLoadSequence=0,todoSelectedDay='';
const pendingTodos=new Set();
const todoCardErrors=new Map();
const todoCalendarDayScroll=new Map();
const todoDay=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
let todoMonth=todoDay().slice(0,7);
let todoSomedayExpanded=false,todoCalendarFocus='today',todoCalendarFocusQueued=false,todoCardDay='';
const todoViews={open:'日程安排',today:'今天的安排',week:'未来 7 天',important:'重要安排',done:'已完成',trash:'回收站'};
function todoCompletionInstant(t){const stamp=Date.parse(t.completed_at||'');return Number.isFinite(stamp)?stamp:null;}
function todoCompletionDay(t){const stamp=todoCompletionInstant(t);return stamp===null?'':new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(stamp));}
function compareTodoCompletion(a,b){
  const first=todoCompletionInstant(a),second=todoCompletionInstant(b);
  if(first!==second){if(first===null)return 1;if(second===null)return -1;return second-first;}
  return String(a.id).localeCompare(String(b.id));
}
function todoWeekEnd(today){const d=new Date(today+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+6);return d.toISOString().slice(0,10);}
function todoMatches(t,filter,today){
  if(filter==='trash')return !!t.deleted;
  if(t.deleted)return false;
  if(filter==='done')return !!t.done;
  if(t.done)return false;
  if(filter==='today')return !!t.due&&t.due<=today;
  if(filter==='week')return !!t.due&&t.due>=today&&t.due<=todoWeekEnd(today);
  if(filter==='important')return t.priority==='important';
  return true;
}
function todoDraftValues(){return {title:$('todo-edit-title').value,due:$('todo-edit-due').value,due_time:$('todo-edit-time').value,priority:$('todo-edit-priority').value,note:$('todo-edit-note').value,done:$('todo-edit-done').checked?1:0};}
function todoHasDraft(){return !!editingTodo&&Object.entries(todoDraftValues()).some(([key,value])=>value!==editingTodo[key]);}
async function confirmTodoDraft(){return !todoHasDraft()||await window.tiangongConfirm('这条待办有尚未保存的修改。放弃修改并继续？');}
function todoSetDetail(t){
  editingTodo={...t,due_time:t.due_time||''};
  $('todo-edit-title').value=t.title;$('todo-edit-due').value=t.due||'';$('todo-edit-time').value=t.due_time||'';
  $('todo-edit-time').disabled=!t.due;$('todo-edit-priority').value=t.priority;$('todo-edit-note').value=t.note||'';
  $('todo-edit-done').checked=!!t.done;$('todo-edit-done').disabled=!!t.deleted;$('todo-edit-error').textContent='';$('todo-edit-refresh').hidden=true;
  $('todo-detail-state').textContent=t.deleted?'回收站中的待办':t.done?'已完成':'待办详情';
  $('todo-detail-trash').textContent=t.deleted?'恢复待办':'移至回收站';
  $('todo-detail-updated').textContent=t.updated?'更新于 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(t.updated)):'';
}
// Keep only fields the user actually changed while a save was in flight.
function mergeTodoDetail(saved,before){
  const current=todoDraftValues(),changed=Object.entries(current).filter(([key,value])=>value!==(before[key]??''));
  todoSetDetail(saved);
  const fields={title:'todo-edit-title',due:'todo-edit-due',due_time:'todo-edit-time',priority:'todo-edit-priority',note:'todo-edit-note',done:'todo-edit-done'};
  changed.forEach(([key,value])=>{if(key==='done')$(fields[key]).checked=!!value;else $(fields[key]).value=value;});
  $('todo-edit-time').disabled=!$('todo-edit-due').value;
  if(!$('todo-edit-due').value)$('todo-edit-time').value='';
}
async function closeTodoDetail(force=false){if(!force&&todoHasDraft()&&!await confirmTodoDraft())return false;editingTodo=null;$('todo-editor').close();renderTodos();return true;}
async function openTodo(id){if(editingTodo?.id===id)return;if(todoHasDraft()&&!await confirmTodoDraft())return;const t=tasks.find(t=>t.id===id);if(!t)return;closeTodoDayCard();todoSetDetail(t);if(!$('todo-editor').open)$('todo-editor').showModal();renderTodos();}
async function loadTodos(){
  const sequence=++todoLoadSequence;
  try{
    const r=await fetch('/api/todos',{cache:'no-store'});if(!r.ok)throw Error('待办暂时无法加载，请稍后重试');
    const body=await r.json();if(sequence!==todoLoadSequence)return;tasks=body.tasks;$('todo-error').textContent='';
    if(editingTodo){const latest=tasks.find(t=>t.id===editingTodo.id);if(latest){if(!todoHasDraft())todoSetDetail(latest);else if(latest.revision!==editingTodo.revision){$('todo-edit-error').textContent='这条待办已在另一处更新。当前修改已保留，可先复制内容，再载入最新版本。';$('todo-edit-refresh').hidden=false;}}}
    renderTodos();
  }catch(e){if(sequence===todoLoadSequence)$('todo-error').textContent=e.message;}
  if(page==='todos')document.querySelector('.demo-dot').textContent='共享待办';
}
const todoCheckIcon='<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m5 10 3 3 7-7"/></svg>';
function todoDisplayParts(t){
  const parts=TodoCalendar.displayParts(t);
  const allDay=!parts.time&&/^\s*全天(?:\s+|$)/.exec(parts.title);
  return allDay?{time:'全天',title:parts.title.slice(allDay[0].length)}:parts;
}
function todoTitleText(title){
  const parts=todoDisplayParts({title});
  return parts.time==='全天'?`<strong class="todo-event-time">全天</strong>${parts.title?' '+escape(parts.title):''}`:escape(title);
}
function todoRow(t,compact=false){
  const parts=todoDisplayParts(t),time=parts.time,busy=pendingTodos.has(t.id);
  const completedView=todoFilter==='done'&&!compact,finished=todoCompletionInstant(t);
  const completionClock=finished===null?'':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(finished));
  return `<article class="todo-row ${compact?'someday-row':''} ${t.done?'completed':''} ${t.deleted?'deleted':''}" data-todo-id="${escape(t.id)}">
    <button type="button" role="checkbox" aria-checked="${!!t.done}" class="todo-check" data-complete="${escape(t.id)}" ${t.deleted||busy?'disabled':''} aria-label="${t.done?'重新打开':'完成'}：${escape(t.title)}">${todoCheckIcon}</button>
    ${completedView?`<span class="todo-row-time">${completionClock?`<strong>${completionClock}</strong><small>完成</small>`:'时间未知'}</span>`:!compact&&t.due?`<span class="todo-row-time">${time?`<strong>${escape(time)}</strong>`:'当天<small>时间待定</small>'}</span>`:''}
    <button type="button" class="todo-row-main" data-edit-todo="${escape(t.id)}" aria-label="查看待办：${escape(t.title)}"><span class="todo-name">${todoTitleText(!completedView&&!compact&&t.due?parts.title:t.title)}</span>${!compact&&t.note?`<span class="todo-note-preview">${escape(t.note)}</span>`:''}</button>
    <div class="todo-row-actions">${!t.deleted?`<button type="button" class="todo-star ${t.priority==='important'?'is-important':''}" data-priority-todo="${escape(t.id)}" aria-label="${t.priority==='important'?'取消重要':'标为重要'}：${escape(t.title)}" ${busy?'disabled':''}>${t.priority==='important'?'★':'☆'}</button>`:`<button type="button" class="todo-restore" data-trash-todo="${escape(t.id)}" ${busy?'disabled':''}>恢复</button>`}</div>
  </article>`;
}
function renderTodos(){
  const today=todoDay(),q=$('todo-search').value.trim().toLowerCase(),matchesSearch=t=>!q||(t.title+' '+t.note).toLowerCase().includes(q);
  const allOpen=tasks.filter(t=>!t.done&&!t.deleted),archive=['done','trash'].includes(todoFilter);
  const list=tasks.filter(t=>todoMatches(t,todoFilter,today)&&(archive||t.due)&&(!todoSelectedDay||t.due===todoSelectedDay)&&matchesSearch(t)).sort(todoFilter==='done'?compareTodoCompletion:TodoCalendar.compare);
  const someday=allOpen.filter(t=>!t.due&&matchesSearch(t)).sort(TodoCalendar.compare);
  $('todo-count').textContent=list.length+' 件';$('todo-list-title').textContent=todoSelectedDay?TodoCalendar.dayLabel(todoSelectedDay,today):todoViews[todoFilter];
  $('todo-list-description').textContent=todoFilter==='done'?'最近完成的在前 · 今天、昨天、更早':todoFilter==='today'?'今天与之前未完成的安排 · 重要优先':archive?'按原定日期排列 · 可恢复':'按日期分组 · 重要优先，再按时间';
  $('todo-visible-count').textContent=list.length+' 件';$('todo-clear-day').hidden=!todoSelectedDay;
  document.querySelectorAll('[data-todo-filter]').forEach(b=>{const selected=b.dataset.todoFilter===todoFilter&&!todoSelectedDay;b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected));});
  let lastDate=null;
  $('todo-list').innerHTML=list.map(t=>{const group=todoFilter==='done'?todoCompletionDay(t):t.due||'',heading=group!==lastDate?`<h3 class="todo-day-heading ${group&&group<today&&!archive?'past':''}">${group?escape(TodoCalendar.dayLabel(group,today)):todoFilter==='done'?'完成日期未知':'未定日期'}${group&&group<today&&!archive?'<small>待完成</small>':''}</h3>`:'';lastDate=group;return heading+todoRow(t);}).join('')||`<div class="todo-empty"><span aria-hidden="true">✓</span><h3>${q?'没有找到匹配的待办':archive?(todoFilter==='done'?'完成的事，会留在这里':'回收站是空的'):'这段时间还没有安排'}</h3><p>${q?'试试其他关键词。':archive?'记录会保留，可以随时恢复。':'在上方添加一件事；还没定日期的，记到「有空再做」。'}</p></div>`;
  $('todo-someday-count').textContent=someday.length+' 件';
  const visibleSomeday=someday;
  $('todo-someday-list').innerHTML=visibleSomeday.map(t=>todoRow(t,true)).join('')||`<p class="todo-someday-empty">${q?'没有匹配的便签':'想做的事，随时添一笔。'}</p>`;
  const toggle=$('todo-someday-toggle'),toggleLabel=todoSomedayExpanded?'收起便签，仅显示前 3 项':`展开其余 ${Math.max(0,someday.length-3)} 项便签`;
  toggle.hidden=true;toggle.setAttribute('aria-expanded',String(todoSomedayExpanded));toggle.setAttribute('aria-label',toggleLabel);toggle.title=toggleLabel;
  [$('todo-list'),$('todo-someday-list')].forEach(container=>{
    container.querySelectorAll('[data-complete]').forEach(el=>el.onclick=()=>{const t=tasks.find(t=>t.id===el.dataset.complete);changeTodo(t.id,{done:t.done?0:1});});
    container.querySelectorAll('[data-priority-todo]').forEach(el=>el.onclick=()=>{const t=tasks.find(t=>t.id===el.dataset.priorityTodo);changeTodo(t.id,{priority:t.priority==='important'?'normal':'important'});});
    container.querySelectorAll('[data-trash-todo]').forEach(el=>el.onclick=()=>{const t=tasks.find(t=>t.id===el.dataset.trashTodo);changeTodo(t.id,{deleted:t.deleted?0:1});});
    container.querySelectorAll('[data-edit-todo]').forEach(el=>el.onclick=()=>openTodo(el.dataset.editTodo));
  });
  renderTodoCalendar();
  renderTodoDayCard();
}
function todoEventText(t){
  const parts=todoDisplayParts(t);
  return `${parts.time?`<strong class="todo-event-time">${escape(parts.time)}</strong> `:''}<span class="todo-event-title">${escape(parts.title)}</span>`;
}
function compareTodoCalendarStatus(a,b){return Number(!!a.done)-Number(!!b.done)||TodoCalendar.compare(a,b);}
function closeTodoDayCard(){todoCardDay='';if($('todo-day-card').open)$('todo-day-card').close();}
function openTodoDayCard(date){
  todoCardDay=date;todoCardErrors.clear();renderTodoDayCard();
  if(!$('todo-day-card').open)$('todo-day-card').showModal();
}
function renderTodoDayCard(){
  if(!todoCardDay)return;
  const card=$('todo-day-card'),active=document.activeElement;
  const focusId=active?.dataset?.complete||active?.dataset?.editTodo;
  const focusAction=active?.dataset?.complete?'complete':'editTodo';
  const entries=tasks.filter(t=>t.due===todoCardDay&&!t.deleted).sort(compareTodoCalendarStatus);
  $('todo-day-card-title').textContent=TodoCalendar.dayLabel(todoCardDay,todoDay());
  $('todo-day-card-error').textContent=[...todoCardErrors.values()].filter(error=>error.due===todoCardDay).map(error=>error.message).join('\n');
  $('todo-day-card-list').innerHTML=entries.map(t=>`<article class="todo-day-card-row ${t.priority==='important'?'important':''} ${t.done?'is-completed':''}" data-todo-id="${escape(t.id)}">
    <button type="button" role="checkbox" aria-checked="${!!t.done}" class="todo-check" data-complete="${escape(t.id)}" ${pendingTodos.has(t.id)?'disabled':''} aria-label="${t.done?'重新打开':'完成'}：${escape(t.title)}">${todoCheckIcon}</button>
    <button type="button" class="todo-day-card-task" data-edit-todo="${escape(t.id)}" aria-label="${t.done?'已完成，':''}${t.priority==='important'?'重要，':''}查看待办：${escape(t.title)}">${todoEventText(t)}</button>
  </article>`).join('')||'<p class="todo-day-card-empty">这一天还没有安排。</p>';
  $('todo-day-card-list').querySelectorAll('[data-complete]').forEach(b=>b.onclick=()=>{const t=tasks.find(t=>t.id===b.dataset.complete);if(t)changeTodo(t.id,{done:t.done?0:1});});
  $('todo-day-card-list').querySelectorAll('[data-edit-todo]').forEach(b=>b.onclick=()=>openTodo(b.dataset.editTodo));
  if(card.open&&focusId){
    const candidates=[...$('todo-day-card-list').querySelectorAll(focusAction==='complete'?'[data-complete]':'[data-edit-todo]')];
    const next=candidates.find(b=>b.dataset[focusAction]===focusId&&!b.disabled)||candidates.find(b=>!b.disabled)||$('todo-day-card-close');
    next.focus({preventScroll:true});
  }
}
function renderTodoCalendar(){
  const today=todoDay(),[year,month]=todoMonth.split('-'),currentWeek=TodoCalendar.weekStart(today);
  const grid=$('todo-calendar-grid');
  // Remember each day's own position before replacing the event buttons.
  // Hidden pages have no layout; do not overwrite saved positions with zeroes.
  if(!$('todos').hidden)grid.querySelectorAll('[data-todo-scroll-date]').forEach(stack=>{
    if(stack.clientHeight&&!stack.dataset.todoScrollPending)todoCalendarDayScroll.set(stack.dataset.todoScrollDate,Math.max(0,stack.scrollTop));
  });
  $('todo-month-title').textContent=`${year} 年 ${Number(month)} 月`;
  const entries=tasks.filter(t=>t.due&&!t.deleted).sort(compareTodoCalendarStatus);
  const byDate=new Map();entries.forEach(t=>{if(!byDate.has(t.due))byDate.set(t.due,[]);byDate.get(t.due).push(t);});
  const days=TodoCalendar.continuousDays(todoMonth);
  $('todo-calendar-grid').innerHTML=days.map((d,index)=>{
    const rows=byDate.get(d.date)||[],selected=todoSelectedDay===d.date;
    const monthLabel=d.day===1||index===0?`${d.date.slice(0,4)!==year?Number(d.date.slice(0,4))+'年':''}${Number(d.date.slice(5,7))}月`:'';
    const weekStart=index%7===0?`<div class="todo-calendar-week-row ${d.date===currentWeek?'is-current-week':''}" data-week-start="${d.date}">`:'';
    return `${weekStart}<div class="todo-calendar-day ${d.inMonth?'':'outside'} ${d.date===today?'is-today':''} ${selected?'is-selected':''} ${d.day===1?'is-month-start':''}" data-date="${d.date}">
      <button type="button" class="todo-calendar-date" data-todo-day="${d.date}" aria-pressed="${selected}" ${d.date===today?'aria-current="date"':''} aria-label="${d.date}${d.date===today?'，今天':''}，${rows.length} 件安排"><span class="todo-calendar-day-number">${d.day}</span>${monthLabel?`<small class="todo-calendar-month-label">${monthLabel}</small>`:''}${d.date===today?'<em class="todo-calendar-today-label">今天</em>':''}</button>
      <div class="todo-calendar-events" data-todo-scroll-date="${d.date}" role="group" aria-label="${d.date} 的事项">${rows.map(t=>`<button type="button" class="todo-calendar-event ${t.priority==='important'?'important':''} ${t.done?'is-completed':''}" data-calendar-task="${escape(t.id)}" aria-label="${t.done?'已完成，':''}${escape((TodoCalendar.timeOf(t)||'时间待定')+' '+t.title)}" title="${t.done?'已完成，':''}${escape((TodoCalendar.timeOf(t)||'时间待定')+' '+t.title)}"><span>${todoEventText(t)}</span></button>`).join('')}</div>
    </div>${index%7===6?'</div>':''}`;
  }).join('');
  $('todo-calendar-grid').querySelectorAll('[data-todo-day]').forEach(b=>b.onclick=()=>{todoSelectedDay=b.dataset.todoDay;todoFilter='open';$('todo-due').value=todoSelectedDay;$('todo-search').value='';renderTodos();if(window.matchMedia('(max-width: 900px)').matches)$('todo-list-title').scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});});
  $('todo-calendar-grid').querySelectorAll('[data-calendar-task]').forEach(b=>b.onclick=()=>openTodo(b.dataset.calendarTask));
  grid.querySelectorAll('[data-todo-scroll-date]').forEach(stack=>{
    if(stack.clientHeight)stack.scrollTop=todoCalendarDayScroll.get(stack.dataset.todoScrollDate)||0;
    else stack.dataset.todoScrollPending='1';
    stack.addEventListener('scroll',()=>{if(stack.clientHeight&&!stack.dataset.todoScrollPending)todoCalendarDayScroll.set(stack.dataset.todoScrollDate,Math.max(0,stack.scrollTop));},{passive:true});
  });
  // Bound view state to the rendered range; no task data is copied into storage.
  const renderedDates=new Set(days.map(day=>day.date));
  for(const date of todoCalendarDayScroll.keys())if(!renderedDates.has(date))todoCalendarDayScroll.delete(date);
  if(typeof window.requestAnimationFrame==='function')window.requestAnimationFrame(updateTodoCalendarScrollability);
  focusTodoCalendar();
}
function updateTodoCalendarScrollability(){
  const grid=$('todo-calendar-grid');
  if(!grid||$('todos').hidden||!grid.clientWidth)return;
  grid.querySelectorAll('[data-todo-scroll-date]').forEach(stack=>{
    if(stack.dataset.todoScrollPending){stack.scrollTop=todoCalendarDayScroll.get(stack.dataset.todoScrollDate)||0;delete stack.dataset.todoScrollPending;}
    const overflow=stack.scrollHeight>stack.clientHeight+1;
    stack.tabIndex=overflow?0:-1;
    stack.setAttribute('aria-label',`${stack.dataset.todoScrollDate} 的事项${overflow?'，可上下滚动':''}`);
  });
}
if(typeof ResizeObserver!=='undefined'){
  const todoFitObserver=new ResizeObserver(()=>{if(typeof window.requestAnimationFrame==='function')window.requestAnimationFrame(updateTodoCalendarScrollability);});
  todoFitObserver.observe($('todo-calendar-scroll'));
}
if(document.fonts?.ready)document.fonts.ready.then(updateTodoCalendarScrollability);
document.addEventListener('tiangong:themechange',()=>{if(typeof window.requestAnimationFrame==='function')window.requestAnimationFrame(updateTodoCalendarScrollability);});
function focusTodoCalendar(){
  if(!todoCalendarFocus||todoCalendarFocusQueued||page!=='todos'||$('todos').hidden||typeof window.requestAnimationFrame!=='function')return;
  todoCalendarFocusQueued=true;
  window.requestAnimationFrame(()=>{
    todoCalendarFocusQueued=false;
    const container=$('todo-calendar-scroll');
    if(!todoCalendarFocus||page!=='todos'||$('todos').hidden||container.clientHeight<=0)return;
    const date=todoCalendarFocus==='today'?todoDay():todoMonth+'-01';
    const week=$('todo-calendar-grid').querySelector(`[data-week-start="${TodoCalendar.weekStart(date)}"]`);
    if(!week)return;
    const viewport=container.getBoundingClientRect(),row=week.getBoundingClientRect();
    // Only move the calendar's own scroll area. Subsequent searches, saves and
    // date selections preserve the position where the user left it.
    const headerHeight=$('todo-calendar-week').getBoundingClientRect().height;
    const top=container.scrollTop+row.top-viewport.top-container.clientTop-headerHeight-(container.clientHeight-headerHeight-row.height)/2;
    container.scrollTo({top:Math.max(0,top),behavior:'auto'});
    todoCalendarFocus='';
  });
}
async function saveTodo(data,render=true){
  const r=await fetch('/api/todos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const body=await r.json();if(!r.ok)throw Error(body.error||'保存失败');
  const t=body.entry;++todoLoadSequence;tasks=tasks.filter(x=>x.id!==t.id).concat(t);if(render)renderTodos();return t;
}
async function animateTodoCompletion(id){
  const rows=[...document.querySelectorAll('[data-todo-id]')].filter(row=>row.dataset.todoId===id);
  rows.forEach(row=>{row.classList.add('is-completing');row.querySelector('.todo-check').setAttribute('aria-checked','true');});
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  await Promise.all(rows.map(async row=>{
    const height=row.getBoundingClientRect().height;
    try{await row.animate([{opacity:1,height:height+'px'},{opacity:0,height:height+'px',offset:.35},{opacity:0,height:0,paddingTop:0,paddingBottom:0,borderBottomWidth:0}],{duration:430,easing:'cubic-bezier(.2,.7,.2,1)',fill:'forwards'}).finished;}catch(_){/* A page change may remove the animated row. */}
  }));
}
async function changeTodo(id,patch){
  if(pendingTodos.has(id))return;
  if(editingTodo?.id===id&&todoHasDraft()&&!await confirmTodoDraft()){renderTodos();return;}
  const original=tasks.find(t=>t.id===id);if(!original)return;
  const detailBefore=editingTodo?.id===id?todoDraftValues():{...original,due_time:original.due_time||''};
  pendingTodos.add(id);renderTodos();
  try{
    const saved=await saveTodo({...original,...patch},false);
    if(patch.done===1&&!original.done)await animateTodoCompletion(id);
    if(editingTodo?.id===id)mergeTodoDetail(saved,detailBefore);
    $('todo-error').textContent='';todoCardErrors.delete(id);
    if('deleted' in patch){toast(patch.deleted?'已移至回收站，可随时恢复':'待办已恢复');if(editingTodo?.id===id&&!todoHasDraft())closeTodoDetail(true);}
    if(patch.done===1)toast('已完成，可在「已完成」中找回');
  }catch(e){$('todo-error').textContent=e.message;if(todoCardDay===original.due)todoCardErrors.set(id,{due:original.due,message:original.title+'：'+e.message});if(editingTodo?.id===id){$('todo-edit-error').textContent=e.message;if(e.message.includes('另一处'))$('todo-edit-refresh').hidden=false;}}
  finally{pendingTodos.delete(id);renderTodos();}
}
$('todo-due').value=todoDay();
$('todo-quick').onsubmit=async e=>{
  e.preventDefault();$('todo-add').disabled=true;
  const submitted={title:$('todo-title').value,due:$('todo-due').value,due_time:$('todo-time').value,priority:$('todo-important').checked?'important':'normal'};
  try{quickId=quickId||uid();await saveTodo({id:quickId,...submitted});quickId=null;
    if($('todo-title').value===submitted.title)$('todo-title').value='';
    todoFilter='open';todoSelectedDay='';
    if(submitted.due&&!TodoCalendar.continuousDays(todoMonth).some(d=>d.date===submitted.due)){todoMonth=submitted.due.slice(0,7);todoCalendarFocus='month';}
    renderTodos();$('todo-error').textContent='';$('todo-schedule-entry').close();toast('已加入日程');
  }catch(e){$('todo-error').textContent=e.message;$('todo-schedule-entry').querySelector('[role=alert]').textContent=e.message;}finally{$('todo-add').disabled=false;}
};
$('todo-someday-form').onsubmit=async e=>{
  e.preventDefault();$('todo-someday-add').disabled=true;const title=$('todo-someday-input').value;
  try{somedayId=somedayId||uid();await saveTodo({id:somedayId,title,due:'',due_time:''});somedayId=null;if($('todo-someday-input').value===title)$('todo-someday-input').value='';$('todo-error').textContent='';$('todo-someday-entry').close();toast('已记到「有空再做」');}
  catch(e){$('todo-error').textContent=e.message;$('todo-someday-entry').querySelector('[role=alert]').textContent=e.message;}finally{$('todo-someday-add').disabled=false;}
};
$('todo-edit-form').onsubmit=async e=>{
  e.preventDefault();if(!editingTodo||pendingTodos.has(editingTodo.id))return;
  const id=editingTodo.id,submitted=todoDraftValues(),original={...editingTodo};pendingTodos.add(id);$('todo-edit-save').disabled=true;
  try{const saved=await saveTodo({...original,...submitted},false);
    if(editingTodo?.id===id){if(JSON.stringify(todoDraftValues())===JSON.stringify(submitted)){editingTodo=null;$('todo-editor').close();}else mergeTodoDetail(saved,submitted);}
    if(submitted.done===1&&!original.done)await animateTodoCompletion(id);
    toast('待办已保存');
  }catch(e){if(editingTodo?.id===id){$('todo-edit-error').textContent=e.message;if(e.message.includes('另一处'))$('todo-edit-refresh').hidden=false;}else $('todo-error').textContent=e.message;}
  finally{pendingTodos.delete(id);$('todo-edit-save').disabled=false;renderTodos();}
};
$('todo-day-card-close').onclick=closeTodoDayCard;
$('todo-day-card').addEventListener('cancel',e=>{e.preventDefault();closeTodoDayCard();});
$('todo-day-card').addEventListener('close',()=>{todoCardDay='';});
$('todo-day-card').addEventListener('click',e=>{if(e.target===$('todo-day-card')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeTodoDayCard();}});
$('todo-close').onclick=()=>closeTodoDetail();
$('todo-editor').addEventListener('cancel',e=>{e.preventDefault();closeTodoDetail();});
$('todo-editor').addEventListener('click',e=>{if(e.target===$('todo-editor')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeTodoDetail();}});
$('todo-detail-trash').onclick=()=>{if(editingTodo)changeTodo(editingTodo.id,{deleted:editingTodo.deleted?0:1});};
$('todo-edit-refresh').onclick=async()=>{if(!await confirmTodoDraft())return;const id=editingTodo?.id;closeTodoDetail(true);await loadTodos();if(id)openTodo(id);};
$('todo-edit-due').onchange=()=>{const hasDate=!!$('todo-edit-due').value;$('todo-edit-time').disabled=!hasDate;if(!hasDate)$('todo-edit-time').value='';};
$('todo-unschedule').onclick=()=>{$('todo-edit-due').value='';$('todo-edit-time').value='';$('todo-edit-time').disabled=true;};
$('todo-search').oninput=renderTodos;
$('todo-someday-toggle').onclick=()=>{todoSomedayExpanded=!todoSomedayExpanded;renderTodos();};
$('todo-clear-day').onclick=()=>{todoSelectedDay='';renderTodos();};
$('todo-month-prev').onclick=()=>{todoMonth=TodoCalendar.shiftMonth(todoMonth,-1);todoCalendarFocus='month';renderTodoCalendar();};
$('todo-month-next').onclick=()=>{todoMonth=TodoCalendar.shiftMonth(todoMonth,1);todoCalendarFocus='month';renderTodoCalendar();};
$('todo-month-now').onclick=()=>{todoMonth=todoDay().slice(0,7);todoSelectedDay=todoDay();todoFilter='open';todoCalendarFocus='today';$('todo-due').value=todoSelectedDay;$('todo-search').value='';renderTodos();};
document.querySelectorAll('[data-todo-filter]').forEach(b=>b.onclick=()=>{todoFilter=b.dataset.todoFilter;todoSelectedDay='';renderTodos();});
window.addEventListener('beforeunload',e=>{if(todoHasDraft()||$('todo-title').value.trim()||$('todo-someday-input').value.trim()){e.preventDefault();e.returnValue='';}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&page==='todos')loadTodos();});

// Compact rail cards keep creation in a dedicated editor, leaving filters visible.
for (const [buttonId, dialogId, inputId] of [
 ['todo-someday-new','todo-someday-entry','todo-someday-input'],
 ['todo-schedule-new','todo-schedule-entry','todo-title']]) {
 const entry=$(dialogId), input=$(inputId);
 $(buttonId).onclick=()=>{entry.querySelector('[role=alert]').textContent='';entry.showModal();input.focus();};
 const closeEntry=async()=>{if(input.value.trim()&&!await tiangongConfirm({title:'放下这次编辑？',message:'这条待办还没有保存。',confirmLabel:'放弃编辑'}))return;input.value='';entry.close();};
 entry.querySelector('[data-close-entry]').onclick=closeEntry;
 entry.addEventListener('cancel',event=>{event.preventDefault();closeEntry();});
}
