/* App-owned confirmation; user text is assigned as text, never HTML. */
(()=>{
 let active=false;
 window.tiangongConfirm=(options={})=>{
  if(typeof options==='string')options={message:options,cancelLabel:'取消'};
  const {title='请确认',message='',confirmLabel='确定',cancelLabel='继续编辑',danger=false}=options;
  if(active)return Promise.resolve(false);
  active=true;
  return new Promise(resolve=>{
   const d=document.createElement('dialog');d.className='app-confirm';
   d.setAttribute('aria-labelledby','app-confirm-title');d.setAttribute('aria-describedby','app-confirm-message');
   d.innerHTML='<h2 id="app-confirm-title"></h2><p id="app-confirm-message"></p><div class="app-confirm-actions"><button type="button" data-answer="cancel" autofocus></button><button type="button" data-answer="confirm" class="primary"></button></div>';
   d.querySelector('h2').textContent=title;d.querySelector('p').textContent=message;
   d.querySelector('[data-answer=cancel]').textContent=cancelLabel;
   const yes=d.querySelector('[data-answer=confirm]');yes.textContent=confirmLabel;if(danger)yes.classList.add('is-danger');
   let answer=false;
   d.addEventListener('click',e=>{const b=e.target.closest('[data-answer]');if(b){answer=b.dataset.answer==='confirm';d.close();}});
   d.addEventListener('cancel',()=>{answer=false;});
   d.addEventListener('close',()=>{d.remove();active=false;resolve(answer);},{once:true});
   document.body.append(d);d.showModal();
  });
 };
})();
