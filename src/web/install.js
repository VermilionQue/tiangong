'use strict';
// Home Screen support needs no Service Worker on iOS/iPadOS. In particular,
// this script never stores responses, queues writes or reloads an open draft.
(() => {
  const root = document.documentElement;
  const display = window.matchMedia('(display-mode: standalone)');
  const isAppleMobile = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isSafari = /Safari/.test(navigator.userAgent) &&
    !/CriOS|FxiOS|EdgiOS|OPiOS|DuckDuckGo|MicroMessenger/.test(navigator.userAgent);
  let appInstalled = false;

  function updateDisplay() {
    const standalone = display.matches || navigator.standalone === true || appInstalled;
    root.classList.toggle('is-ios', isAppleMobile);
    root.classList.toggle('is-standalone', standalone);
    const card = document.getElementById('install-card');
    if (card) card.hidden = standalone;
    const hint = document.getElementById('install-device-hint');
    if (hint) hint.textContent = isAppleMobile
      ? (isSafari ? '点 Safari 的分享按钮，把天工录添加到主屏幕。' : '请用 Safari 打开天工录，再添加到主屏幕。')
      : '在 iPhone 或 iPad 上，用 Safari 打开天工录。';
  }

  function updateConnection() {
    if (!document.body) return;
    let notice = document.getElementById('connection-notice');
    if (!notice && navigator.onLine === false) {
      notice = document.createElement('div');
      notice.id = 'connection-notice';
      notice.className = 'connection-notice';
      notice.setAttribute('role', 'status');
      notice.setAttribute('aria-live', 'polite');
      notice.textContent = '当前设备离线。新增或修改的内容需联网后手动保存，请先保留当前页面。';
      document.body.append(notice);
    }
    if (notice) notice.hidden = navigator.onLine !== false;
    root.classList.toggle('device-offline', navigator.onLine === false);
  }

  function mount() { updateDisplay(); updateConnection(); }
  updateDisplay();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, {once: true});
  else mount();
  if (display.addEventListener) display.addEventListener('change', updateDisplay);
  else if (display.addListener) display.addListener(updateDisplay);
  window.addEventListener('appinstalled', () => { appInstalled = true; updateDisplay(); });
  window.addEventListener('online', updateConnection);
  window.addEventListener('offline', updateConnection);
  window.addEventListener('pageshow', mount);
})();
