'use strict';
// V40 home uses the existing per-user services. It never loads the full ledger.
(() => {
  const root=document.getElementById('today');if(!root)return;
  const el=id=>document.getElementById(id),esc=escape;
  const chinaDate=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const shift=(day,amount)=>{const date=new Date(day+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+amount);return date.toISOString().slice(0,10);};
  let day=chinaDate(),selected=day,loading=null,notes=[],notesGeneration=0,finance=null,financeFailed=false,todosLoaded=false;
  let weatherLoading=null,weatherReadAt=0,noteBusy=false,notePending=null,noteId=null,noteRevision;
  const busyTodos=new Set();
  const amount=cents=>Number.isSafeInteger(cents)?'¥ '+money(cents):'尚未读取';
  function go(target){const url=new URL(location.href);url.hash=target;history.replaceState(null,'',url);showPage(target);if(target==='finance')showTab('overview');}
  function message(id,text,retry){const node=el(id);node.textContent=text;node.classList.toggle('error',!!retry);if(retry){const button=document.createElement('button');button.type='button';button.textContent='重试';button.onclick=retry;node.append(button);}}
  function renderDates(){
    const current=chinaDate();if(day!==current){day=current;selected=current;}
    const stamp=new Date(day+'T12:00:00+08:00');
    el('t40-day').textContent=String(Number(day.slice(-2)));
    el('t40-date-label').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric',weekday:'long'}).format(stamp);
    el('t40-month-weekday').textContent=Number(day.slice(5,7))+'月 · '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',weekday:'short'}).format(stamp);
    const start=shift(day,-((new Date(day+'T12:00:00Z').getUTCDay()+6)%7));
    el('t40-week').innerHTML=Array.from({length:7},(_,index)=>{const date=shift(start,index);return `<button type="button" data-t40-date="${date}" class="${date===day?'is-today':''}" aria-pressed="${date===selected}" aria-label="${date}${date===day?' 今天':''}"><span>${['一','二','三','四','五','六','日'][index]}</span><strong>${Number(date.slice(-2))}</strong></button>`;}).join('');
  }
  function renderTasks(){
    renderDates();if(!todosLoaded)return;
    const open=tasks.filter(task=>!task.deleted&&!task.done),todayCount=open.filter(task=>task.due===day).length;
    el('t40-todo-count').textContent='今天有 '+todayCount+' 件待办';
    const rows=tasks.filter(task=>!task.deleted&&task.due===selected).sort((a,b)=>Number(!!a.done)-Number(!!b.done)||TodoCalendar.compare(a,b));
    el('t40-tasks').innerHTML=rows.length?rows.map(task=>{const parts=TodoCalendar.displayParts(task),busy=busyTodos.has(task.id)||pendingTodos.has(task.id);return `<div class="t40-task${task.done?' is-done':''}" data-t40-task="${esc(task.id)}"><button type="button" class="t40-task-check" role="checkbox" aria-checked="${!!task.done}" aria-label="${task.done?'重新打开':'完成'}：${esc(task.title)}" data-t40-complete="${esc(task.id)}" ${busy?'disabled':''}>${task.done?'✓':''}</button><span class="t40-task-time">${esc(parts.time||'当天')}</span><button type="button" class="t40-task-copy" data-t40-edit="${esc(task.id)}"><span>${task.priority==='important'?'<i class="t40-important" aria-label="重要"></i>':''}${esc(parts.title)}</span>${task.note?`<small>${esc(task.note)}</small>`:''}</button></div>`;}).join(''):`<div class="t40-empty">${selected===day?'今天':'这一天'}还没有安排。<br>留一点空白，也很好。</div>`;
  }
  async function refreshTodos(){
    message('t40-todo-status','正在读取安排…');
    await loadTodos();const error=el('todo-error').textContent;
    if(error){message('t40-todo-status',(todosLoaded?'暂未刷新，仍显示上次读取的安排。':'')+error,refreshTodos);return;}
    todosLoaded=true;message('t40-todo-status','');renderTasks();
  }
  function renderLatest(){
    const recent=notes.filter(note=>!note.deleted).sort((a,b)=>String(b.created).localeCompare(String(a.created))).slice(0,5);
    el('t40-latest-body').innerHTML=recent.length?recent.map(latest=>`<article class="t44-recent-note"><time class="t40-note-time">${esc(String(latest.created).slice(0,16))}</time><p class="t40-latest-text">${esc(latest.text?.trim()||'这条笔记记录了一份附件。')}</p><div class="t40-note-tags">${(latest.tags||[]).map(tag=>`<button type="button" data-t40-tag="${esc(tag)}">#${esc(tag)}</button>`).join('')}</div></article>`).join(''):'<p class="t40-status">还没有笔记。从一两句话开始。</p>';
  }

  async function refreshNotes(){
    const generation=++notesGeneration;
    try{const response=await fetch('/api/notes',{cache:'no-store'});if(!response.ok)throw Error();const body=await response.json();if(!Array.isArray(body.notes))throw Error();if(generation!==notesGeneration)return;notes=body.notes;renderLatest();}
    catch{if(generation===notesGeneration)message('t40-latest-body','笔记暂时无法读取。',refreshNotes);}
  }
  function renderFinance(){
    if(!finance){if(financeFailed)message('t40-finance-body','本月收支暂时无法读取。',refreshFinance);return;}
    const cents=finance.summary?.daily_net_expense_cents,budgets=finance.budgets||[];
    const complete=budgets.length===3&&budgets.every(row=>Number.isSafeInteger(row.cents)&&Number.isSafeInteger(row.consumed_cents));
    const total=complete?budgets.reduce((sum,row)=>sum+row.cents,0):0,spent=complete?budgets.reduce((sum,row)=>sum+row.consumed_cents,0):0;
    const remaining=total-spent,ratio=total>0?remaining/total:0;
    el('t40-finance-body').innerHTML=`<p class="t40-spending-label">${esc(day.slice(0,7))} · 日常净支出</p><div class="t40-spending">${amount(cents)}</div>${complete&&total>0?`<div class="t40-budget-line"><span>餐饮、日用、娱乐</span><span>${hiddenMoney?'余额已隐藏':remaining<0?'已超预算 '+amount(-remaining):'预算剩余 '+amount(remaining)}</span></div><div class="t40-budget-bar${ratio<=.3?' is-low':''}"${hiddenMoney?' hidden':''}><span style="width:${Math.max(0,Math.min(100,ratio*100))}%"></span></div>`:''}<p class="t40-finance-note">${Number.isSafeInteger(cents)?'本月净支出（支出减退款），不含转账和借还本金。':'打开泉库查看本月收支。'}${!complete?' 餐饮、日用、娱乐预算尚未全部设置。':total===0?' 本月生活预算设为 0 元。':''}${financeFailed?' 暂未刷新，显示上次读取的数据。':''}</p>`;
    el('t40-money-toggle').setAttribute('aria-label',hiddenMoney?'显示首页金额':'隐藏首页金额');
    el('t40-money-toggle').title=hiddenMoney?'显示金额':'隐藏金额';
  }
  async function refreshFinance(){
    try{const response=await (window.tiangongFinanceReadFetch||fetch)('/api/finance/hub?month='+day.slice(0,7),{cache:'no-store'});if(!response.ok)throw Error();const body=await response.json();if(!body.summary||!Array.isArray(body.budgets))throw Error();finance=body;financeFailed=false;}
    catch{financeFailed=true;}renderFinance();
  }
  async function refreshWeather(force=false){
    if(weatherLoading)return weatherLoading;
    if(!force&&Date.now()-weatherReadAt<60000)return;
    weatherLoading=(async()=>{try{
      await window.loadWeather();
      if(el('weather-content').hidden)throw Error();
      el('t40-weather-city').textContent=el('weather-city').textContent||'天气';
      el('t40-weather-body').innerHTML=`<div class="t40-weather-now"><div><div class="t40-weather-temp">${esc(el('weather-temperature').textContent)}</div><p class="t40-weather-detail">${esc(el('weather-description').textContent)} · ${esc(el('weather-feels').textContent)}</p></div><span class="t40-weather-symbol" aria-hidden="true">${esc(el('weather-symbol').textContent)}</span></div><p class="t40-weather-rain">${esc(el('weather-rain-summary').textContent)}</p><p class="t40-weather-source">${esc(el('weather-updated').textContent)}</p>`;
      weatherReadAt=Date.now();
    }catch{message('t40-weather-body','天气暂时无法读取，可稍后重试或打开天气详情。',()=>refreshWeather(true));}
    finally{weatherLoading=null;}})();return weatherLoading;
  }
  async function refreshExercise(){
    try{
      const response=await fetch('/api/wellness',{cache:'no-store'});if(!response.ok)throw Error();
      const data=await response.json(),training=data.training||{},month=day.slice(0,7),first=new Date(month+'-01T12:00:00Z'),offset=(first.getUTCDay()+6)%7,count=new Date(first.getUTCFullYear(),first.getUTCMonth()+1,0).getDate();
      let total=0,active=0;
      const cells=Array.from({length:count},(_,i)=>{const date=month+'-'+String(i+1).padStart(2,'0'),items=training[date]?.items||[],known=items.filter(x=>Number.isFinite(x.kcal)&&x.kcal>=0),kcal=known.reduce((n,x)=>n+x.kcal,0),future=date>day;
        if(items.length&&!future)active++;if(!future)total+=kcal;
        const level=future||!kcal?0:kcal<150?1:kcal<350?2:kcal<600?3:4;
        const label=date+'：'+(future?'尚未到来':items.length?(known.length?Math.round(kcal)+' 千卡'+(known.length<items.length?'，部分训练未记录热量':''):'已运动，热量未记录'):'暂无运动记录');
        return `<button type="button" data-t44-exercise="${date}" class="t44-heat-cell level-${level}${future?' is-future':''}" aria-label="${esc(label)}" title="${esc(label)}" ${future?'disabled':''}>${i+1}</button>`;
      }).join('');
      el('t44-exercise-body').innerHTML=`<div class="t44-exercise-summary"><strong>${active}<small> 天</small></strong><span>${month} · 已记录 ${Math.round(total).toLocaleString()} 千卡</span></div><div class="t44-heat-week">${['一','二','三','四','五','六','日'].map(x=>'<span>'+x+'</span>').join('')}</div><div class="t44-heat-grid">${'<span></span>'.repeat(offset)}${cells}</div><div class="t44-heat-legend"><span>少</span>${[0,1,2,3,4].map(x=>`<i class="level-${x}"></i>`).join('')}<span>多</span></div><p id="t44-exercise-detail" class="t40-status">点一天查看热量；浅色表示暂无已记录消耗。</p>`;
    }catch{message('t44-exercise-body','运动记录暂时无法读取。',refreshExercise);}
  }
  async function load(){
    if(root.hidden)return;if(loading)return loading;
    renderDates();renderFinance();
    // Two small reads first; defer weather/summary to avoid a cold-start burst.
    loading=(async()=>{await Promise.allSettled([refreshTodos(),refreshNotes()]);await Promise.allSettled([refreshFinance(),refreshWeather(),refreshExercise()]);})().finally(()=>{loading=null;});
    return loading;
  }
  root.addEventListener('click',async event=>{
    const target=event.target.closest('button');if(!target)return;
    if(target.dataset.t44Exercise){el('t44-exercise-detail').textContent=target.getAttribute('aria-label');return;}
    if(target.dataset.t40Go){go(target.dataset.t40Go);return;}
    if(target.dataset.t40Date){selected=target.dataset.t40Date;renderTasks();return;}
    if(target.dataset.t40Edit){openTodo(target.dataset.t40Edit);return;}
    if(target.dataset.t40Tag){go('notes');selectNoteTag(target.dataset.t40Tag);return;}
    if(target.dataset.t40Complete){
      const id=target.dataset.t40Complete,task=tasks.find(item=>item.id===id);if(!task||busyTodos.has(id))return;
      busyTodos.add(id);renderTasks();try{await changeTodo(id,{done:task.done?0:1});const error=el('todo-error').textContent;message('t40-todo-status',error||'');}finally{busyTodos.delete(id);renderTasks();}return;
    }
  });
  el('t40-add-todo').onclick=()=>{go('todos');el('todo-due').value=day;el('todo-title').focus();};
  el('privacy').addEventListener('click',renderFinance);
  el('t40-money-toggle').onclick=()=>{hiddenMoney=!hiddenMoney;el('privacy').setAttribute('aria-label',hiddenMoney?'显示金额':'隐藏金额');render();renderFinance();};
  el('t40-note-form').onsubmit=async event=>{
    event.preventDefault();if(noteBusy)return;
    const input=el('t40-note-input'),submitted=input.value;if(!submitted.trim()&&!notePending){input.focus();return;}
    noteBusy=true;el('t40-note-save').disabled=true;message('t40-note-status','正在保存…');
    try{
      // Recover the exact uncertain request first. Do not create a second note
      // if the first save reached the server but its response was interrupted.
      let saved=null;
      if(notePending){saved=await postNote(notePending);noteId=saved.id;noteRevision=saved.revision;notePending=null;}
      const html=esc(submitted).replace(/\n/g,'<br>');
      if(submitted.trim()&&(!saved||saved.html!==html)){
        noteId=noteId||uid();const payload={id:noteId,revision:noteRevision,html,attachments:[]};notePending=payload;
        saved=await postNote(payload);noteRevision=saved.revision;notePending=null;
      }
      if(input.value===submitted){input.value='';noteId=null;noteRevision=undefined;}
      // Newly typed text is a new draft, not an invisible replacement of the saved note.
      else{noteId=null;noteRevision=undefined;}
      ++notesGeneration;notes=notes.filter(note=>note.id!==saved.id).concat(saved);renderLatest();
      message('t40-note-status',input.value?'已记下，后来输入的文字仍保留。':'已记入笔记。');
    }catch(error){if(!error.noteUncertain)notePending=null;message('t40-note-status',error.message||'暂未保存，文字已保留。');}
    finally{noteBusy=false;el('t40-note-save').disabled=false;}
  };
  el('todo-editor').addEventListener('close',()=>{if(!root.hidden)renderTasks();});
  window.addEventListener('beforeunload',event=>{if(el('t40-note-input').value.trim()||noteBusy||notePending){event.preventDefault();event.returnValue='';}});
  new MutationObserver(records=>{if(records.some(record=>record.attributeName==='data-view')&&!root.hidden)load();}).observe(document.body,{attributes:true,attributeFilter:['data-view']});
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&!root.hidden&&chinaDate()!==day)load();});
  window.tiangongToday={refresh:load};
  load();
})();
