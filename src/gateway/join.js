'use strict';
(() => {
  const $=id=>document.getElementById(id);
  let token='',valid=false,busy=false;
  try{token=decodeURIComponent(location.hash.slice(1));}catch{}
  async function request(path,body){
    let response;
    try{response=await fetch(path,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
    catch{throw new Error('暂时没有连接上，请稍后重试。');}
    let result={};try{result=await response.json();}catch{}
    if(!response.ok)throw new Error(typeof result.error==='string'?result.error:'邀请暂时无法使用，请联系邀请你的人重新发送。');
    return result;
  }
  async function inspect(){
    if(busy)return;
    if(!token||token.length>500){$('join-loading').hidden=true;$('join-message').textContent='请通过邀请你的人发来的完整链接打开此页。';return;}
    busy=true;valid=false;$('join-form').hidden=true;$('join-loading').hidden=false;$('join-message').textContent='';$('join-retry').hidden=true;
    try{
      const result=await request('/auth/invitation',{token});
      if(!result.user?.username)throw new Error('这份邀请暂时无法使用，请重新获取邀请链接。');
      $('join-username').value=result.user.username;
      $('join-identity').textContent=`欢迎，${result.user.display_name || result.user.username}。`;
      $('join-form').hidden=false;valid=true;
    }catch(error){$('join-message').textContent=error.message;$('join-retry').hidden=false;}
    finally{busy=false;$('join-loading').hidden=true;}
  }
  $('join-show').addEventListener('click',()=>{
    const show=$('join-password').type==='password';
    $('join-password').type=$('join-confirm').type=show?'text':'password';
    $('join-show').textContent=show?'隐藏':'显示';$('join-show').setAttribute('aria-label',show?'隐藏密码':'显示密码');
  });
  $('join-form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!valid)return;
    if($('join-password').value!==$('join-confirm').value){$('join-message').textContent='两次密码不一致，请再检查一下。';$('join-confirm').focus();return;}
    busy=true;$('join-submit').disabled=true;$('join-message').textContent='正在开启你的空间…';
    try{await request('/auth/activate',{token,password:$('join-password').value});$('join-form').reset();token='';location.replace('/');}
    catch(error){$('join-message').textContent=error.message;busy=false;$('join-submit').disabled=false;}
  });
  $('join-retry').addEventListener('click',inspect);
  inspect();
})();
