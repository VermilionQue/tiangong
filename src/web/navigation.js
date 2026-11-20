'use strict';
// Fresh sessions start Today; in-session navigation and tag links remain usable.
const routePages=new Set(['today','todos','finance','work','notes','sport','health','travel','treasures','weather','people','recipes']);
function routeFromLocation(){
  let p=location.hash.slice(1);
  if(!routePages.has(p)&&p!=='games') p='today';
  // Retired page links and saved preferences land on an available home page.
  if(p==='games'){
    p='today';
    const url=new URL(location.href);url.hash=p;history.replaceState(null,'',url);
  }
  showPage(routePages.has(p)?p:'today');
}
document.querySelectorAll('[data-page]').forEach(button=>button.addEventListener('click',()=>{const p=button.dataset.page;const url=new URL(location.href);url.hash=p;history.replaceState(null,'',url);try{localStorage.setItem((window.tiangongAccount?.storageKey('tiangong-last-page')||'tiangong-last-page'),p);}catch{}window.tiangongAccount?.saveSettings({last_page:p}).catch(()=>{});if(window.matchMedia('(max-width:700px)').matches){setNavigation(false);document.getElementById('nav-toggle').focus({preventScroll:true});}}));
window.addEventListener('hashchange',routeFromLocation);
function startAtToday(){
  const url=new URL(location.href);url.hash='today';history.replaceState(null,'',url);showPage('today');
}
startAtToday();
let hiddenAt=0;
const returnKey=window.tiangongAccount?.storageKey('tiangong-away-at')||'tiangong-away-at';
const hasDraft=()=>[...document.querySelectorAll('form input:not([type=hidden]):not([type=search]),form textarea,[contenteditable=true]')].some(n=>n.offsetParent!==null&&(n.isContentEditable?n.textContent.trim():n.value&&n.value!==n.defaultValue));
function returnHomeIfAway(){
  let left=hiddenAt;try{left=left||Number(sessionStorage.getItem(returnKey)||0);}catch{}
  if(left&&Date.now()-left>=30*60*1000&&!document.querySelector('dialog[open]')&&!hasDraft())startAtToday();
  hiddenAt=0;try{sessionStorage.removeItem(returnKey);}catch{}
}
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){hiddenAt=Date.now();try{sessionStorage.setItem(returnKey,String(hiddenAt));}catch{}}
  else returnHomeIfAway();
});
window.addEventListener('pageshow',event=>{if(event.persisted)returnHomeIfAway();});
