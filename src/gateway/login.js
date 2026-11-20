(() => {
  'use strict';
  const guestForm = document.getElementById('gateway-guest-form');
  if (guestForm) {
    const button = guestForm.querySelector('button'), label = button.textContent;
    guestForm.addEventListener('submit', () => {
      button.disabled = true;
      button.textContent = '正在准备你的独立体验空间…';
    });
    window.addEventListener('pageshow', () => { button.disabled = false; button.textContent = label; });
    if (!document.getElementById('gateway-login-form')) return;
  }
  const form = document.getElementById('gateway-login-form');
  if (form) {
    const password = document.getElementById('gateway-password');
    const submit = document.getElementById('gateway-submit');
    document.getElementById('gateway-show').addEventListener('click', event => {
      const show = password.type === 'password';
      password.type = show ? 'text' : 'password';
      event.currentTarget.textContent = show ? '隐藏' : '显示';
      event.currentTarget.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
    });
    // Submit named fields as a normal form so browsers can offer to save them.
    form.addEventListener('submit', () => {
      submit.disabled = true;
    });
    window.addEventListener('pageshow', () => { submit.disabled = false; });
    return;
  }
  const nativeFetch = window.fetch.bind(window);
  const showExpired = () => {
    if (!document.body) {
      document.addEventListener('DOMContentLoaded', showExpired, {once:true});
      return;
    }
    if (document.getElementById('gateway-session-expired')) return;
    const notice = document.createElement('div');
    notice.className = 'gateway-session-expired';
    notice.id = 'gateway-session-expired';
    notice.setAttribute('role', 'alert');
    notice.textContent = '登录已到期。重新登录后，可回来继续保存。';
    const link = document.createElement('a');
    link.href = '/login'; link.target = '_blank'; link.rel = 'noopener'; link.textContent = '重新登录';
    notice.append(link); document.body.append(notice);
  };
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (response.status === 401) showExpired();
    return response;
  };
  const mountLogout = () => {
    const host = document.querySelector('.header-right');
    if (host) {
      const logout = document.createElement('button');
      logout.type = 'button'; logout.className = 'gateway-logout'; logout.textContent = '退出登录';
      logout.addEventListener('click', async () => {
        logout.disabled = true;
        try {
          const response = await nativeFetch('/auth/logout', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}', credentials:'same-origin'});
          if (!response.ok && response.status !== 401) throw new Error('退出未完成，请重试');
          location.replace('/login');
        } catch (_) {
          logout.textContent = '重试退出'; logout.disabled = false;
        }
      });
      host.append(logout);
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountLogout, {once:true});
  else mountLogout();
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
})();
