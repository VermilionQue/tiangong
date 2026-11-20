'use strict';
(() => {
 // Start only at the left edge; vertical and multi-touch gestures remain scrolling.
 let gesture=null,openedAt=0;
 document.addEventListener('touchstart',event=>{
  const t=event.touches[0];gesture=null;
  if(event.touches.length!==1||!matchMedia('(max-width:1000px)').matches||!document.body.classList.contains('nav-collapsed')||document.querySelector('dialog[open]')||t.clientX>28||t.clientY<65)return;
  gesture={x:t.clientX,y:t.clientY};
 },{passive:true});
 document.addEventListener('touchmove',event=>{
  if(!gesture||event.touches.length!==1){gesture=null;return;}
  const t=event.touches[0],dx=t.clientX-gesture.x,dy=t.clientY-gesture.y;
  if(Math.abs(dy)>24&&Math.abs(dy)>Math.abs(dx)){gesture=null;return;}
  if(dx>55&&Math.abs(dx)>Math.abs(dy)*1.8){if(event.cancelable)event.preventDefault();setNavigation(true);openedAt=Date.now();gesture=null;}
 },{passive:false});
 document.addEventListener('touchend',()=>{gesture=null;},{passive:true});
 document.addEventListener('touchcancel',()=>{gesture=null;},{passive:true});
 document.addEventListener('click',event=>{if(Date.now()-openedAt<350){event.stopImmediatePropagation();event.preventDefault();}},true);
})();
