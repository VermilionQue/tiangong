'use strict';
(() => {
  const statusNode=document.getElementById('todo-sync-status');
  if(!statusNode)return;
  const button=document.getElementById('todo-sync-now'),issuesNode=document.getElementById('todo-sync-issues');
  let busy=false,lastSuccess='',lastState=null,requestSequence=0;
  const labels={title:'事项',note:'备注',due:'日期',due_time:'时间',schedule:'日期与时间',done:'完成',priority:'重要程度'};
  function snapshot(value){
    if(!value)return '';
    return Object.entries(labels).filter(([key])=>key in value).map(([key,label])=>{
      let shown=value[key];
      if(key==='done')shown=shown?'已完成':'未完成';
      if(key==='priority')shown=shown==='important'?'重要':'普通';
      return `<span>${escape(label)}：${escape(Array.isArray(shown)?shown.join(' '):shown||'未填写')}</span>`;
    }).join('');
  }
  function render(state){
    lastState=state;
    statusNode.textContent=state.message||(!state.enabled?'尚未连接提醒事项':state.state==='ok'?'已同步':'正在同步');
    statusNode.dataset.state=state.state||'disabled';
    if(state.state==='ok'&&state.last_success){
      const at=new Date(state.last_success);
      if(Number.isFinite(at.getTime()))statusNode.textContent+=' · '+new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit'}).format(at);
    }
    button.disabled=busy||!state.enabled;
    issuesNode.innerHTML=(state.issues||[]).map((issue,index)=>`<article class="todo-sync-issue"><strong>${escape(issue.title||'待办同步')}</strong><p>${escape(issue.message||'两边内容不同，请选择保留哪一份。')}</p>${issue.kind==='conflict'?`<div class="todo-sync-choices"><div><b>天工录</b>${snapshot(issue.local)}</div><div><b>提醒事项</b>${snapshot(issue.apple)}</div></div><div class="todo-sync-actions"><button type="button" data-sync-choice="local" data-sync-index="${index}">保留天工录</button><button type="button" data-sync-choice="apple" data-sync-index="${index}">保留提醒事项</button></div>`:''}</article>`).join('');
    issuesNode.querySelectorAll('[data-sync-choice]').forEach(b=>{b.disabled=busy;b.onclick=()=>{
      const issue=lastState.issues[Number(b.dataset.syncIndex)];
      request({action:'resolve',todo_id:issue.todo_id,choice:b.dataset.syncChoice,token:issue.token});
    };});
    const checked=state.last_checked||state.last_success;
    if(checked&&lastSuccess!==checked&&page==='todos'&&!document.hidden&&typeof loadTodos==='function'){
      lastSuccess=checked;
      loadTodos();
    }
  }
  async function refresh(){
    if(busy||document.hidden||page!=='todos')return;
    const sequence=++requestSequence;
    try{const r=await fetch('/api/todos/sync',{cache:'no-store'});if(!r.ok)throw Error('暂时无法读取提醒事项同步状态');const state=await r.json();if(sequence===requestSequence)render(state);}
    catch(e){if(sequence===requestSequence){statusNode.textContent=e.message;statusNode.dataset.state='error';}}
  }
  async function request(data){
    if(busy)return;busy=true;++requestSequence;button.disabled=true;
    issuesNode.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{
      const r=await fetch('/api/todos/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),body=await r.json();
      if(!r.ok)throw Error(body.error||'同步未完成，请重试');
      render(body);setTimeout(refresh,2000);
    }catch(e){statusNode.textContent=e.message;statusNode.dataset.state='error';}
    finally{busy=false;button.disabled=!lastState?.enabled;issuesNode.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  button.onclick=()=>request({action:'sync'});
  document.addEventListener('visibilitychange',refresh);
  document.querySelectorAll('[data-page="todos"]').forEach(b=>b.addEventListener('click',refresh));
  window.addEventListener('hashchange',refresh);
  setInterval(refresh,30000);
  refresh();
})();
