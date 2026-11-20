'use strict';
// One activity clock for native page/panel scrollbars and the theme range.
// No content wrappers, replacement scroll engine, overlays or subtree observer.
(() => {
  const root=document.documentElement;
  const states=new WeakMap(),active=new Set();
  const WAIT=500,FADE=500;
  let held=null,pointerFrame=0,lastPointer=null,refreshFrame=0,hoverTarget=null,hoverState=null;
  root.classList.add('tg-scrollbars');
  const pageScroller=()=>document.scrollingElement||root;
  const normalize=node=>node===document||node===window?pageScroller():node;
  const motionReduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
  function dimensions(element){
    const page=element===pageScroller();
    const style=page?null:getComputedStyle(element);
    const visible=element.clientWidth>0&&element.clientHeight>0;
    return{
      x:visible&&element.scrollWidth>element.clientWidth+1&&(page||/^(auto|scroll|overlay)$/.test(style.overflowX)),
      y:visible&&element.scrollHeight>element.clientHeight+1&&(page||/^(auto|scroll|overlay)$/.test(style.overflowY))
    };
  }
  function paint(state,opacity){
    state.opacity=opacity;
    state.element.style.setProperty('--tg-scrollbar-opacity',String(opacity));
    const slider=state.element.id==='theme-options'?document.getElementById('theme-scroll'):null;
    if(slider)slider.style.setProperty('--tg-scrollbar-opacity',String(opacity));
  }
  function stop(state){
    clearTimeout(state.timer);cancelAnimationFrame(state.frame);
    state.timer=state.frame=0;paint(state,0);active.delete(state);
  }
  function measure(element){
    if(!(element instanceof Element))return null;
    const axes=dimensions(element);
    // Root overscroll remains native (including browser edge gestures).
    if(element!==pageScroller()){
      element.classList.toggle('tg-scrollbar-x',axes.x);
      element.classList.toggle('tg-scrollbar-y',axes.y);
    }
    let state=states.get(element);
    if(!axes.x&&!axes.y){if(state)stop(state);return null;}
    if(!state){state={element,timer:0,frame:0,opacity:0,axes};states.set(element,state);}
    state.axes=axes;return state;
  }
  function fade(state){
    state.timer=0;
    if(held===state)return;
    if(!state.element.isConnected||!measure(state.element)){stop(state);return;}
    if(motionReduced()){stop(state);return;}
    const start=performance.now();
    const step=now=>{
      if(held===state)return;
      const amount=Math.max(0,1-(now-start)/FADE);
      paint(state,Math.round(amount*1000)/1000);
      if(amount>0)state.frame=requestAnimationFrame(step);else stop(state);
    };
    state.frame=requestAnimationFrame(step);
  }
  function reveal(node){
    const state=measure(normalize(node));if(!state)return null;
    clearTimeout(state.timer);cancelAnimationFrame(state.frame);state.frame=0;
    active.add(state);paint(state,1);
    if(held!==state)state.timer=setTimeout(()=>fade(state),WAIT);
    return state;
  }
  function ancestors(target){
    const found=[];
    for(let node=target instanceof Element?target:target?.parentElement;node&&node!==root;node=node.parentElement){
      if(node.id==='theme-scroll'){
        const options=document.getElementById('theme-options');if(options)found.push(options);
      }else found.push(node);
    }
    found.push(pageScroller());return found;
  }
  function closestScroller(target,axis){
    for(const node of ancestors(target)){
      const state=measure(node);if(state&&(!axis||state.axes[axis]))return state;
    }
    return null;
  }
  function release(){
    if(!held)return;const state=held;held=null;reveal(state.element);
  }
  function refresh(){
    refreshFrame=0;
    for(const state of Array.from(active)){
      if(!state.element.isConnected||!measure(state.element))stop(state);
    }
  }
  function scheduleRefresh(){if(!refreshFrame)refreshFrame=requestAnimationFrame(refresh);}
  function inGutter(element,event){
    if(element.id==='theme-options'&&event.target.id==='theme-scroll')return true;
    const page=element===pageScroller(),rect=page?{left:0,top:0,right:innerWidth,bottom:innerHeight}:element.getBoundingClientRect();
    const right=page?element.clientWidth:rect.left+element.clientLeft+element.clientWidth;
    const bottom=page?element.clientHeight:rect.top+element.clientTop+element.clientHeight;
    const vertical=rect.right-right,horizontal=rect.bottom-bottom;
    return(vertical>2&&event.clientX>=right-2&&event.clientX<=rect.right&&event.clientY>=rect.top&&event.clientY<=rect.bottom)||
      (horizontal>2&&event.clientY>=bottom-2&&event.clientY<=rect.bottom&&event.clientX>=rect.left&&event.clientX<=rect.right);
  }
  document.addEventListener('scroll',event=>{reveal(event.target);scheduleRefresh();},{capture:true,passive:true});
  document.addEventListener('wheel',event=>{
    const axis=Math.abs(event.deltaX)>Math.abs(event.deltaY)?'x':'y';
    const state=closestScroller(event.target,axis);if(state)reveal(state.element);
  },{capture:true,passive:true});
  document.addEventListener('pointerdown',event=>{
    const state=closestScroller(event.target);if(!state)return;
    if(event.pointerType==='touch'||inGutter(state.element,event))held=state;
    reveal(state.element);
  },{capture:true,passive:true});
  document.addEventListener('pointermove',event=>{
    if(event.pointerType==='touch'||held)return;
    lastPointer=event;
    if(!pointerFrame)pointerFrame=requestAnimationFrame(()=>{
      pointerFrame=0;
      if(hoverTarget!==lastPointer.target||!hoverState?.element.isConnected){hoverTarget=lastPointer.target;hoverState=closestScroller(lastPointer.target);}
      const state=hoverState;
      if(state&&inGutter(state.element,lastPointer))reveal(state.element);
    });
  },{passive:true});
  window.addEventListener('pointerup',release,{capture:true,passive:true});
  window.addEventListener('pointercancel',release,{capture:true,passive:true});
  window.addEventListener('blur',release);
  document.addEventListener('keydown',event=>{
    if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','PageUp','PageDown','Home','End',' '].includes(event.key))return;
    const axis=['ArrowLeft','ArrowRight'].includes(event.key)?'x':undefined;
    const state=closestScroller(event.target,axis);if(state)reveal(state.element);
  },{capture:true});
  document.addEventListener('focusin',event=>{
    if(event.target.id==='theme-scroll')reveal(document.getElementById('theme-options'));
  });
  document.addEventListener('input',event=>{
    const state=closestScroller(event.target);if(state)reveal(state.element);scheduleRefresh();
  },{passive:true});
  document.addEventListener('click',scheduleRefresh,{passive:true});
  document.addEventListener('toggle',scheduleRefresh,{capture:true,passive:true});
  document.addEventListener('tiangong:themechange',scheduleRefresh);
  window.addEventListener('resize',scheduleRefresh,{passive:true});
  window.addEventListener('pageshow',scheduleRefresh);
  window.TiangongScrollbars={reveal,refresh:scheduleRefresh};
})();
