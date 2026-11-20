'use strict';
(() => {
  function mount() {
    const account=window.tiangongAccount, user=account?.user;
    if(!user || document.getElementById('account-dialog')) return;
    const header=document.querySelector('.header-right');
    if(!header) return;
    const trigger=document.createElement('button');
    trigger.type='button';trigger.className='account-trigger';trigger.textContent='账户';
    trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-controls','account-dialog');
    header.prepend(trigger);
    const dialog=document.createElement('dialog');dialog.id='account-dialog';dialog.className='account-dialog';
    dialog.setAttribute('aria-labelledby','account-title');
    dialog.innerHTML=`<div class="account-heading"><div><h2 id="account-title">我的账户</h2><p id="account-identity"></p></div><button type="button" class="account-close" aria-label="关闭账户设置">×</button></div>
      <div id="account-demo" hidden><p>体验空间 · 示例资料，不保存修改。</p><button type="button" class="account-primary" id="account-demo-exit">返回登录</button><p id="account-demo-message" class="account-message" role="status"></p></div>
      <div id="account-personal"><form id="account-profile-form"><label for="account-display-name">怎么称呼你</label><div class="account-inline"><input id="account-display-name" name="display_name" maxlength="40" required autocomplete="nickname"><button class="account-primary" type="submit">保存称呼</button></div><p id="account-profile-message" class="account-message" role="status"></p></form>
      <details class="account-password"><summary>修改密码</summary><form id="account-password-form"><label for="account-old-password">原密码</label><input id="account-old-password" name="old_password" type="password" autocomplete="current-password" required maxlength="1024"><label for="account-new-password">新密码</label><input id="account-new-password" name="new_password" type="password" autocomplete="new-password" required minlength="14" maxlength="1024"><p class="account-hint">至少 14 个字符，修改后需要重新登录。</p><label for="account-confirm-password">再输入一次新密码</label><input id="account-confirm-password" type="password" autocomplete="new-password" required minlength="14" maxlength="1024"><p id="account-password-message" class="account-message" role="alert"></p><button class="account-primary" type="submit">保存新密码</button></form></details></div>
      <section id="account-admin" hidden aria-labelledby="account-users-title"><div class="account-section-heading"><h3 id="account-users-title">用户管理</h3><button type="button" id="account-refresh" class="account-text">刷新</button></div><p class="account-hint">每个人都有自己的记录。这里只管理账号与使用状态。</p><p id="account-admin-message" class="account-message" role="status"></p><div id="account-users" class="account-users"></div>
      <details class="account-create"><summary>新增账号</summary><form id="account-create-form"><label for="account-new-username">用户名</label><input id="account-new-username" name="username" required minlength="3" maxlength="40" pattern="[a-zA-Z0-9][a-zA-Z0-9_.\\-]{2,39}" autocapitalize="none" autocomplete="off" spellcheck="false"><p class="account-hint">3—40 位字母、数字、点、横线或下划线。</p><label for="account-new-name">称呼</label><input id="account-new-name" name="display_name" required maxlength="40" autocomplete="off"><button class="account-primary" type="submit">创建并生成邀请</button></form></details>
      <section id="account-invitation" class="account-invitation" hidden aria-labelledby="account-invitation-title"><h4 id="account-invitation-title">邀请已准备好</h4><p id="account-invitation-for"></p><label for="account-invitation-link">邀请链接</label><div class="account-inline"><input id="account-invitation-link" type="text" readonly autocomplete="off"><button class="account-primary" type="button" id="account-copy-invitation">复制链接</button></div><p class="account-hint">24 小时内有效，仅可使用一次。请发给对应的人，由对方自行设置密码。</p><p class="account-hint">关闭此窗口后不再显示链接，需要时可重新邀请。</p><p id="account-copy-message" class="account-message" role="status"></p></section></section>`;
    document.body.append(dialog);
    const $=id=>dialog.querySelector('#'+id);
    const isGuest=user.role==='guest',isDemo=user.role==='demo'||isGuest,isAdmin=user.role==='admin';
    const statuses={active:'使用中',invited:'待加入',disabled:'已停用'};
    const roles={admin:'管理员',member:'成员',demo:'体验账号',guest:'游客'};
    let adminBusy=false;
    function identity(){ $('account-identity').textContent=`${user.display_name || user.username} · ${user.username}`; }
    identity();$('account-display-name').value=user.display_name||'';
    if(isGuest){$('account-identity').textContent='游客演示 · 独立空间';$('account-demo').querySelector('p').textContent='虚构示例，可以自由修改。本次体验独立保存，重新进入游客体验会生成新的示例。';}
    $('account-personal').hidden=isDemo;$('account-demo').hidden=!isDemo;$('account-admin').hidden=!isAdmin;
    if(isDemo){
      const banner=document.createElement('div');banner.className='account-demo-banner';banner.setAttribute('role','status');
      banner.textContent=isGuest?'游客演示 · 虚构示例，可自由修改，与正式账号隔离':'体验空间 · 示例资料，不保存修改';
      const login=document.createElement('button');login.type='button';login.className='account-text';login.textContent='返回登录';
      const message=document.createElement('span');message.id='account-demo-banner-message';message.setAttribute('role','status');banner.append(login,message);
      document.querySelector('main > header')?.after(banner);
      const exits=[login,$('account-demo-exit')],messages=[message,$('account-demo-message')];
      let exiting=false;
      async function exitDemo(){
        if(exiting)return;exiting=true;
        exits.forEach(button=>{button.disabled=true;});messages.forEach(node=>{node.textContent='正在退出体验空间…';});
        try{
          const response=await fetch('/auth/logout',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:'{}'});
          if(!response.ok&&response.status!==401)throw new Error('退出未完成');
          location.replace('/login');
        }catch{
          exiting=false;messages.forEach(node=>{node.textContent='暂时未能退出，当前页面仍保留，请点击重试退出。';});
          exits.forEach(button=>{button.disabled=false;button.textContent='重试退出';});
        }
      }
      exits.forEach(button=>button.addEventListener('click',exitDemo));
    }
    async function request(url,body){
      let response;
      try{response=await fetch(url,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});}
      catch{throw new Error('暂时没有连接上，请稍后重试。');}
      let result={};try{result=await response.json();}catch{}
      if(!response.ok)throw new Error(response.status===401?'登录已到期，请重新登录后继续。':typeof result.error==='string'?result.error:'这次操作没有完成，请稍后重试。');
      return result;
    }
    function clearInvitation(){ $('account-invitation').hidden=true;$('account-invitation-link').value='';$('account-copy-message').textContent=''; }
    function showInvitation(result){
      if(!result.invitation || !dialog.open)return;
      $('account-invitation-for').textContent=`发送给 ${result.user.display_name || result.user.username}（${result.user.username}）`;
      $('account-invitation-link').value=location.origin+'/join#'+encodeURIComponent(result.invitation);
      $('account-invitation').hidden=false;$('account-copy-message').textContent='';
      $('account-invitation').scrollIntoView({block:'nearest'});
    }
    function manageBusy(value){adminBusy=value;$('account-admin').querySelectorAll('button').forEach(button=>{button.disabled=value;});}
    function renderUsers(users){
      $('account-users').replaceChildren();
      for(const entry of users){
        const row=document.createElement('article');row.className='account-user';
        const info=document.createElement('div'),name=document.createElement('strong'),description=document.createElement('p');
        name.textContent=entry.display_name||entry.username;
        description.textContent=`${entry.username} · ${roles[entry.role]||'成员'} · ${statuses[entry.status]||'待确认'}`;
        info.append(name,description);row.append(info);
        if(entry.role==='member' && entry.id!==user.id){
          const actions=document.createElement('div');actions.className='account-user-actions';
          for(const action of [entry.status==='disabled'?'enable':'disable','invite']){
            const button=document.createElement('button');button.type='button';button.className='account-text';
            button.textContent=action==='invite'?'重新邀请':action==='enable'?'启用':'停用';
            button.addEventListener('click',async()=>{
              if(adminBusy)return;
              if(action==='disable'&&!confirm('停用后，此账号将暂时无法登录。之后可以重新启用。确认停用？'))return;
              if(action==='invite'&&entry.status==='active'&&!confirm('重新邀请后，对方需要通过新链接设置密码并再次登录。确认重新邀请？'))return;
              clearInvitation();manageBusy(true);$('account-admin-message').textContent='正在处理…';
              try{const result=await request('/auth/users',{action,id:entry.id});await finishManagement(result,action==='invite'?'新邀请已生成。':action==='enable'?'账号已启用。':'账号已停用。');}
              catch(error){$('account-admin-message').textContent=error.message;}finally{manageBusy(false);}
            });actions.append(button);
          }row.append(actions);
        }$('account-users').append(row);
      }
    }
    async function loadUsers(){const result=await request('/auth/users');renderUsers(result.users||[]);}
    async function finishManagement(result,message){
      showInvitation(result);
      try{await loadUsers();$('account-admin-message').textContent=message;}
      catch{$('account-admin-message').textContent=message+' 列表暂时未更新，可以稍后点击刷新。';}
    }
    async function refreshUsers(){if(adminBusy)return;manageBusy(true);$('account-admin-message').textContent='正在读取账号…';try{await loadUsers();$('account-admin-message').textContent='';}catch(error){$('account-admin-message').textContent=error.message;}finally{manageBusy(false);}}
    trigger.addEventListener('click',()=>{identity();dialog.showModal();if(isAdmin)refreshUsers();});
    dialog.querySelector('.account-close').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{clearInvitation();$('account-password-form').reset();$('account-password-message').textContent='';});
    $('account-refresh').addEventListener('click',refreshUsers);
    $('account-profile-form').addEventListener('submit',async event=>{
      event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;$('account-profile-message').textContent='正在保存…';
      try{const result=await request('/auth/profile',{display_name:$('account-display-name').value.trim()});Object.assign(user,result.user);identity();$('account-profile-message').textContent='称呼已保存。';}
      catch(error){$('account-profile-message').textContent=error.message;}finally{button.disabled=false;}
    });
    $('account-password-form').addEventListener('submit',async event=>{
      event.preventDefault();if($('account-new-password').value!==$('account-confirm-password').value){$('account-password-message').textContent='两次新密码不一致，请再检查一下。';return;}
      const button=event.currentTarget.querySelector('button');button.disabled=true;$('account-password-message').textContent='正在保存…';
      try{await request('/auth/password',{old_password:$('account-old-password').value,new_password:$('account-new-password').value});event.target.reset();location.replace('/login');}
      catch(error){$('account-password-message').textContent=error.message;button.disabled=false;}
    });
    $('account-create-form').addEventListener('submit',async event=>{
      event.preventDefault();if(adminBusy)return;clearInvitation();manageBusy(true);$('account-admin-message').textContent='正在创建账号…';
      try{const result=await request('/auth/users',{action:'create',username:$('account-new-username').value.trim(),display_name:$('account-new-name').value.trim()});event.target.reset();await finishManagement(result,'账号已创建，请把邀请链接发给对方。');}
      catch(error){$('account-admin-message').textContent=error.message;}finally{manageBusy(false);}
    });
    $('account-copy-invitation').addEventListener('click',async()=>{
      const field=$('account-invitation-link');
      try{await navigator.clipboard.writeText(field.value);$('account-copy-message').textContent='邀请链接已复制。';}
      catch{field.focus();field.select();$('account-copy-message').textContent='请复制已选中的邀请链接。';}
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
