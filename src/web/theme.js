// Every new document starts at Today; later navigation is handled by navigation.js.
try { const entry = new URL(location.href); entry.hash = 'today'; history.replaceState(null, '', entry); } catch {}
'use strict';
// Parser-loaded styles can fail independently on a busy/unstable connection.
// Retry only same-origin CSS; keep drafts and the current page untouched.
(() => {
  const failures = new Set(), attempts = new WeakMap();
  function notice() {
    if (!document.body) return;
    let bar = document.getElementById('style-recovery');
    if (!bar && failures.size) {
      bar = document.createElement('div'); bar.id = 'style-recovery';
      bar.setAttribute('role', 'status');
      bar.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;z-index:10000;padding:12px;background:#fff;color:#233650;border:1px solid #b4c5d8;border-radius:12px;font:14px Microsoft YaHei,sans-serif';
      document.body.append(bar);
    }
    if (!bar) return;
    const host = document.querySelector('dialog[open]') || document.body;
    if (bar.parentElement !== host) host.append(bar);
    bar.hidden = !failures.size;
    bar.replaceChildren(document.createTextNode('页面样式正在重新加载，已填写的内容会保留。 '));
    if ([...failures].some(link => attempts.get(link) >= 3)) {
      bar.firstChild.textContent = '部分样式尚未加载，已填写的内容会保留。 ';
      const button = document.createElement('button'); button.type = 'button'; button.textContent = '重试';
      button.onclick = () => { for (const link of failures) { attempts.set(link, 0); retry(link); } };
      bar.append(button);
    }
  }
  function retry(link) {
    const count = attempts.get(link) || 0;
    if (count >= 3) return notice();
    attempts.set(link, count + 1);
    setTimeout(() => {
      if (!failures.has(link) || !link.isConnected) return;
      const url = new URL(link.href); url.searchParams.set('style-retry', String(Date.now())); link.href = url.href;
    }, [300, 1000, 2500][count]);
  }
  window.addEventListener('error', event => {
    const link = event.target;
    if (link?.tagName !== 'LINK' || link.rel !== 'stylesheet' || new URL(link.href).origin !== location.origin) return;
    failures.add(link); retry(link); notice();
  }, true);
  // Element load events reach document capture but do not propagate to window.
  document.addEventListener('load', event => {
    if (failures.delete(event.target)) notice();
  }, true);
  document.addEventListener('DOMContentLoaded', notice);
  new MutationObserver(notice).observe(document.documentElement, {subtree: true, attributes: true, attributeFilter: ['open']});
})();
// A visual preference only: changing it never reloads or edits the user's records.
(() => {
  const key = window.tiangongAccount?.storageKey('tiangong-theme') || 'tiangong-theme';
  const names = {classic:'经典风格', journal:'手帐风格'};
  const colors = {classic:'#f7f8fb', journal:'#f7f5ed'};
  const valid = value => Object.hasOwn(names, value);
  const accountTheme = window.tiangongAccount?.user?.settings?.theme;
  let current = valid(accountTheme) ? accountTheme : 'journal';
  try { const saved = localStorage.getItem(key); if (!valid(accountTheme) && valid(saved)) current = saved; } catch {}
  function apply(value, persist = false) {
    if (!valid(value)) return;
    current = value;
    document.documentElement.dataset.theme = value;
    const browserColor = document.querySelector('meta[name="theme-color"]');
    if (browserColor) browserColor.content = colors[value];
    document.querySelectorAll('link[data-journal-style]').forEach(link => { link.disabled = value !== 'journal'; });
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === value));
    });
    if (persist) {
      let saved = true;
      try { localStorage.setItem(key, value); } catch { saved = false; }
      window.tiangongAccount?.saveSettings?.({theme:value});
      const status = document.getElementById('theme-status');
      if (status) status.textContent = `已切换为${names[value]}${saved ? '' : '，本次打开期间有效'}`;
    }
    document.dispatchEvent(new CustomEvent('tiangong:themechange', { detail: { theme: value } }));
  }
  function setupCarousel() {
    const options = document.getElementById('theme-options');
    const slider = document.getElementById('theme-scroll');
    if (!options || !slider) return;
    const today = document.getElementById('today');
    let frame = 0;
    let revealSelection = true;
    function reveal(button) {
      if (!button || !options.clientWidth) return;
      const viewport = options.getBoundingClientRect();
      const bounds = button.getBoundingClientRect();
      const padding = 6;
      if (bounds.left < viewport.left + padding) options.scrollLeft += bounds.left - viewport.left - padding;
      else if (bounds.right > viewport.right - padding) options.scrollLeft += bounds.right - viewport.right + padding;
    }
    function update() {
      frame = 0;
      // A hidden page has no usable width; retain its scroll position until shown.
      if (!options.clientWidth) return;
      if (revealSelection) {
        reveal(options.querySelector('[aria-pressed="true"]'));
        revealSelection = false;
      }
      const overflow = Math.max(0, options.scrollWidth - options.clientWidth);
      const fraction = overflow > 1 ? Math.min(1, Math.max(0, options.scrollLeft / overflow)) : 0;
      slider.disabled = overflow <= 1;
      slider.value = String(Math.round(fraction * 1000));
      slider.setAttribute('aria-valuetext', overflow <= 1 ? '全部主题已显示' : `浏览位置 ${Math.round(fraction * 100)}%`);
    }
    function schedule() {
      if (!frame) frame = requestAnimationFrame(update);
    }
    slider.addEventListener('input', () => {
      revealSelection = false;
      options.scrollLeft = (Number(slider.value) / 1000) * Math.max(0, options.scrollWidth - options.clientWidth);
      schedule();
    });
    options.addEventListener('scroll', schedule, {passive:true});
    options.addEventListener('focusin', event => {
      reveal(event.target.closest('[data-theme-choice]'));
      schedule();
    });
    document.addEventListener('tiangong:themechange', () => {
      revealSelection = true;
      schedule();
    });
    window.addEventListener('resize', schedule, {passive:true});
    window.addEventListener('pageshow', schedule);
    if ('ResizeObserver' in window) new ResizeObserver(schedule).observe(options);
    if (today) new MutationObserver(schedule).observe(today, {attributes:true, attributeFilter:['hidden']});
    schedule();
  }
  apply(current);
  document.addEventListener('DOMContentLoaded', () => {
    apply(current);
    setupCarousel();
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => apply(button.dataset.themeChoice, true));
    });
  });
  window.addEventListener('storage', event => {
    if (event.key === key) apply(valid(event.newValue) ? event.newValue : 'journal');
  });
})();

// Load dependent application code in order, without speculative request bursts.
// Network failures retry the unexecuted resource only; completed code never reruns.
(() => {
  async function start() {
    for (const placeholder of document.querySelectorAll('script[data-app-src]')) {
      await new Promise(resolve => {
        let attempts = 0, notice;
        function placeNotice() {
          if (!notice) return;
          const host = document.querySelector('dialog[open]') || document.body;
          if (notice.parentElement !== host) host.append(notice);
        }
        const observer = new MutationObserver(placeNotice);
        observer.observe(document.documentElement, {subtree:true, attributes:true, attributeFilter:['open']});
        function load() {
          const script = document.createElement('script');
          const url = new URL(placeholder.dataset.appSrc, location.href);
          if (attempts) url.searchParams.set('script-retry', String(Date.now()));
          script.src = url.href; script.async = false;
          script.onload = () => { observer.disconnect(); notice?.remove(); resolve(); };
          script.onerror = () => {
            script.remove();
            if (!notice) {
              notice = document.createElement('div'); notice.id = 'app-recovery'; notice.setAttribute('role','status');
              notice.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;z-index:10000;padding:12px;background:white;color:#233650;border:1px solid #b4c5d8;border-radius:12px;font:14px Microsoft YaHei,sans-serif';
            }
            placeNotice();
            notice.textContent = '功能正在重新加载，页面会自动恢复。';
            if (attempts++ < 3) setTimeout(load, [300,1000,2500][attempts-1]);
            else {
              notice.textContent = '部分功能尚未加载，已填写的内容会保留。 ';
              const button = document.createElement('button'); button.type = 'button'; button.textContent = '重试';
              button.onclick = () => { attempts = 0; button.disabled = true; load(); }; notice.append(button);
            }
          };
          document.body.append(script);
        }
        load();
      });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true}); else start();
})();
