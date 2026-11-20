(() => {
  'use strict';
  const meta = document.querySelector('meta[name="tiangong-account"]');
  if (!meta) return;
  const user = JSON.parse(meta.content);
  const nativeFetch = window.fetch.bind(window);
  let switched = false, settingsQueue = Promise.resolve();
  function blockSwitchedAccount() {
    switched = true;
    const show = () => {
      if (document.getElementById('account-switched')) return;
      const overlay = document.createElement('div');
      overlay.id = 'account-switched'; overlay.className = 'gateway-session-expired';
      overlay.setAttribute('role', 'alert');
      overlay.textContent = '此浏览器的登录账号已切换。请刷新后继续，原填写没有提交。';
      const button = document.createElement('button'); button.textContent = '刷新页面';
      button.onclick = () => location.reload(); overlay.append(button); document.body.append(overlay);
      document.querySelector('main')?.setAttribute('inert', '');
    };
    if (document.body) show(); else document.addEventListener('DOMContentLoaded', show, {once:true});
  }
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin === location.origin && /^(\/api\/|\/auth\/)/.test(url.pathname)) {
      if (switched) return new Response(JSON.stringify({error:'账号已切换，请刷新页面'}), {status:409,headers:{'Content-Type':'application/json'}});
      const headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined));
      headers.set('X-Tiangong-Client-Account', user.id);
      init = {...init, headers};
    }
    const response = await nativeFetch(input, init);
    if (response.headers.get('X-Tiangong-Account-Changed') === '1') blockSwitchedAccount();
    return response;
  };
  window.tiangongAccount = {
    user,
    storageKey: key => user.id === 'owner' ? key : `${key}:${user.id}`,
    saveSettings(patch) {
      Object.assign(user.settings, patch);
      if (user.role === 'demo') return Promise.resolve(user);
      // Serialize partial updates; one slow response must not overwrite a newer choice.
      settingsQueue = settingsQueue.catch(() => {}).then(async () => {
        const response = await fetch('/auth/profile', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '设置尚未保存');
        if ('display_name' in patch) user.display_name = result.user.display_name;
        return result.user;
      });
      settingsQueue.catch(error => {
        window.dispatchEvent(new CustomEvent('tiangong:settings-error', {detail:{message:error.message}}));
      });
      return settingsQueue;
    }
  };
  document.addEventListener('DOMContentLoaded', () => {
    if (user.id !== 'owner') ['work-guide','work-help'].forEach(id => { const el=document.getElementById(id); if(el)el.hidden=true; });
  }, {once:true});
  window.addEventListener('pageshow', async event => {
    if (!event.persisted) return;
    try {
      const response = await nativeFetch('/auth/session', {cache:'no-store'});
      const result = await response.json();
      if (!response.ok || result.user?.id !== user.id) blockSwitchedAccount();
    } catch { blockSwitchedAccount(); }
  });
})();
