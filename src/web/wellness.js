'use strict';
let wellness={reports:[],training:{}},sportMonth=todayCN.slice(0,7),sportView='training',wellnessReady=false;
const dateDiff=(a,b)=>Math.floor((Date.parse(a+'T00:00:00Z')-Date.parse(b+'T00:00:00Z'))/86400000);
const bodyMetricLabels={
  bodyFatPercent:['体脂率','%'],skeletalMuscleKg:['骨骼肌','kg'],bmi:['BMI',''],
  fatMassKg:['脂肪量','kg'],fatFreeMassKg:['去脂体重','kg'],muscleMassKg:['肌肉量','kg'],
  bodyWaterKg:['身体水分','kg'],bodyWaterPercent:['水分率','%'],proteinKg:['蛋白质','kg'],
  proteinPercent:['蛋白质率','%'],mineralsKg:['无机盐','kg'],boneMassKg:['骨量','kg'],
  visceralFatLevel:['内脏脂肪等级',''],waistHipRatio:['腰臀比',''],waistHipFatRatio:['腰臀脂肪比',''],robustnessIndex:['健壮指数',''],basalMetabolismKcal:['基础代谢','kcal'],
  totalEnergyKcal:['报告总能量消耗','kcal'],musclePercent:['肌肉率','%'],subcutaneousFatPercent:['皮下脂肪率','%']
};
const reportAttachments=r=>r.attachments?.length?r.attachments:(r.attachment?[r.attachment]:[]);
// All three charts share the same UTC day domain; each metric keeps its own unit.
const BodyTrends=(()=>{
  const DAY=86400000,plot={width:360,height:214,left:44,right:340,top:20,bottom:170};
  const metrics=[{key:'weight',label:'体重',unit:'kg'},{key:'bodyFatPercent',label:'体脂率',unit:'%'},{key:'skeletalMuscleKg',label:'骨骼肌',unit:'kg'}];
  function dayTime(date){
    if(typeof date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(date))return null;
    const time=Date.parse(date+'T00:00:00Z');
    return Number.isFinite(time)&&new Date(time).toISOString().slice(0,10)===date?time:null;
  }
  function format(value){return Number.isFinite(value)?String(Number(value.toFixed(2))):'—';}
  function build(reports=[]){
    const series=metrics.map(metric=>({...metric,points:[]}));
    for(const row of reports){
      const time=dayTime(row.date);if(time==null)continue;
      const selfReported=row.provenance?.method==='self_report';
      for(const metric of series){
        // Self-reported weight is useful; do not infer body composition from it.
        if(selfReported&&metric.key!=='weight')continue;
        const value=metric.key==='weight'?row.weight:row.metrics?.[metric.key];
        if(typeof value!=='number'||!Number.isFinite(value)||value<0||(metric.key==='weight'&&value===0))continue;
        const measuredAt=row.provenance?.measuredAt,clock=typeof measuredAt==='string'&&measuredAt.startsWith(row.date+'T')&&/^\d{2}:\d{2}/.test(measuredAt.slice(11))?measuredAt.slice(11,16):'';
        metric.points.push({date:row.date,time,value,selfReported,clock,id:String(row.id||''),
          source:selfReported?'本人自报':String(row.source||'已记录'),order:String(measuredAt||row.createdAt||'')});
      }
    }
    series.forEach(metric=>metric.points.sort((a,b)=>a.time-b.time||a.order.localeCompare(b.order)||a.id.localeCompare(b.id)));
    const dates=[...new Set(series.flatMap(metric=>metric.points.map(point=>point.time)))].sort((a,b)=>a-b);
    if(!dates.length)return {series,start:null,end:null,domain:null,ticks:[]};
    const start=dates[0],end=dates.at(-1),domain=start===end?[start-DAY,end+DAY]:[start,end];
    const ticks=start===end?[start]:[start,...(end-start>=4*DAY?[start+Math.floor((end-start)/DAY/2)*DAY]:[]),end];
    return {series,start,end,domain,ticks};
  }
  function scale(points){
    if(!points.length)return null;
    const values=points.map(point=>point.value),low=Math.min(...values),high=Math.max(...values);
    const pad=Math.max((high-low)*.2,Math.abs(high)*.025,.5),min=Math.max(0,low-pad),max=high+pad;
    return {min,max,ticks:[max,(max+min)/2,min]};
  }
  const x=(time,domain)=>plot.left+(time-domain[0])/(domain[1]-domain[0])*(plot.right-plot.left);
  const y=(value,range)=>plot.bottom-(value-range.min)/(range.max-range.min)*(plot.bottom-plot.top);
  const iso=time=>new Date(time).toISOString().slice(0,10);
  return {build,scale,x,y,format,iso,plot};
})();
let bodyTrendSignature='';
function bodyTrendCardMarkup(metric,data){
  const id='body-trend-'+metric.key,{plot}=BodyTrends,range=BodyTrends.scale(metric.points),last=metric.points.at(-1);
  const pointLabel=point=>`${point.date}${point.clock?' '+point.clock:''}，${metric.label} ${BodyTrends.format(point.value)} ${metric.unit}，${point.source}`;
  const rows=metric.points.map(point=>`<tr><th scope="row">${escape(point.date)}${point.clock?` <small>${escape(point.clock)}</small>`:''}</th><td>${escape(BodyTrends.format(point.value))} ${escape(metric.unit)}</td><td>${escape(point.source)}</td></tr>`).join('');
  let chart='<div class="body-trend-empty">还没有'+escape(metric.label)+'记录</div>';
  if(range){
    const grid=range.ticks.map(value=>`<g aria-hidden="true"><line class="body-trend-gridline" x1="${plot.left}" x2="${plot.right}" y1="${BodyTrends.y(value,range)}" y2="${BodyTrends.y(value,range)}"/><text class="body-trend-axis" x="${plot.left-9}" y="${BodyTrends.y(value,range)+4}" text-anchor="end">${BodyTrends.format(value)}</text></g>`).join('');
    const dates=data.ticks.map(time=>`<text class="body-trend-axis body-trend-date" aria-hidden="true" x="${BodyTrends.x(time,data.domain)}" y="199" text-anchor="${time===data.start&&data.start!==data.end?'start':time===data.end&&data.start!==data.end?'end':'middle'}">${BodyTrends.iso(time).slice(5).replace('-','/')}</text>`).join('');
    const line=metric.points.length>1?`<polyline class="body-trend-line" aria-hidden="true" points="${metric.points.map(point=>BodyTrends.x(point.time,data.domain)+','+BodyTrends.y(point.value,range)).join(' ')}"/>`:'';
    const points=metric.points.map((point,index)=>`<g class="body-trend-point${point.selfReported?' is-self-reported':''}" tabindex="0" role="button" aria-label="${escape(pointLabel(point))}" aria-describedby="${id}-tooltip" data-point="${index}" transform="translate(${BodyTrends.x(point.time,data.domain)},${BodyTrends.y(point.value,range)})"><title>${escape(pointLabel(point))}</title><circle class="body-trend-hit" r="12"/><circle class="body-trend-focus" r="8"/><circle class="body-trend-marker" r="4.5"/></g>`).join('');
    chart=`<svg class="body-trend-chart" viewBox="0 0 ${plot.width} ${plot.height}" role="group" aria-labelledby="${id}-title ${id}-description"><desc id="${id}-description">${escape(metric.label)}，单位${escape(metric.unit)}。${metric.points.length}条记录。按 Tab 键或左右方向键查看数值，完整记录在下方列表。</desc>${grid}${dates}${line}${points}</svg><div class="body-trend-tooltip" id="${id}-tooltip" role="tooltip" hidden></div>`;
  }
  return `<article class="body-trend-card" data-trend="${metric.key}"><div class="body-trend-summary"><h3 id="${id}-title">${metric.label}<span>${metric.unit}</span></h3><p class="body-trend-latest">${last?escape(BodyTrends.format(last.value)):'—'}<small>${metric.unit}</small></p><p class="body-trend-latest-date">${last?escape(last.date)+(last.selfReported?' · 本人自报':' · 最近记录'):'等待下一份记录'}</p></div><div class="body-trend-plot">${chart}</div><p class="body-trend-count">${metric.points.length===1?'已有 1 次记录，下一次再连成线。':metric.points.length?'共 '+metric.points.length+' 次记录':'有记录后，变化会出现在这里。'}</p>${rows?`<details class="body-trend-records"><summary>查看${metric.label}记录 <span>${metric.points.length}</span></summary><div class="body-trend-table-wrap"><table><caption>${metric.label}记录，单位${metric.unit}</caption><thead><tr><th scope="col">日期</th><th scope="col">${metric.label}</th><th scope="col">来源</th></tr></thead><tbody>${rows}</tbody></table></div></details>`:''}</article>`;
}
function renderBodyTrends(){
  const signature=JSON.stringify(wellness.reports);
  let section=$('body-trends');
  if(section&&bodyTrendSignature===signature)return;
  if(!section){
    const layout=document.querySelector('#sport .sport-layout');if(!layout)return;
    section=document.createElement('section');section.id='body-trends';section.className='body-trends';section.setAttribute('aria-labelledby','body-trends-title');layout.after(section);
  }
  bodyTrendSignature=signature;
  const data=BodyTrends.build(wellness.reports);
  section.innerHTML=`<div class="body-trends-heading"><div><h2 id="body-trends-title">身体趋势</h2><p>体重、体脂率和骨骼肌，按时间看变化。</p></div>${data.start!=null?`<p class="body-trends-range">${BodyTrends.iso(data.start)}${data.start!==data.end?' — '+BodyTrends.iso(data.end):''}</p>`:''}</div><div class="body-trend-grid">${data.series.map(metric=>bodyTrendCardMarkup(metric,data)).join('')}</div><div class="body-trends-legend"><span><i></i>已记录</span><span><i class="is-self-reported"></i>自报体重</span><p>各图使用相同日期范围；未记录的指标留空。</p></div>`;
  section.querySelectorAll('.body-trend-card').forEach((card,metricIndex)=>{
    const metric=data.series[metricIndex],range=BodyTrends.scale(metric.points),tooltip=card.querySelector('.body-trend-tooltip');
    if(!tooltip)return;
    const elements=[...card.querySelectorAll('[data-point]')];
    const hide=()=>{tooltip.hidden=true;elements.forEach(element=>element.classList.remove('is-active'));};
    function show(element){
      const point=metric.points[Number(element.dataset.point)];
      elements.forEach(other=>other.classList.toggle('is-active',other===element));
      tooltip.textContent=element.getAttribute('aria-label');tooltip.hidden=false;
      tooltip.style.left=Math.max(25,Math.min(75,BodyTrends.x(point.time,data.domain)/BodyTrends.plot.width*100))+'%';
      tooltip.style.top=BodyTrends.y(point.value,range)/BodyTrends.plot.height*100+'%';
    }
    elements.forEach((element,index)=>{
      element.addEventListener('pointerenter',()=>show(element));
      element.addEventListener('pointerleave',()=>{const focused=elements.find(other=>other===document.activeElement);if(focused)show(focused);else hide();});
      element.addEventListener('focus',()=>show(element));
      element.addEventListener('blur',hide);
      element.addEventListener('click',()=>{element.focus();show(element);});
      element.addEventListener('keydown',event=>{
        let next=null;
        if(event.key==='ArrowRight'||event.key==='ArrowDown')next=Math.min(elements.length-1,index+1);
        if(event.key==='ArrowLeft'||event.key==='ArrowUp')next=Math.max(0,index-1);
        if(event.key==='Home')next=0;if(event.key==='End')next=elements.length-1;
        if(next!=null){event.preventDefault();elements[next].focus();}
        if(event.key==='Escape'){event.preventDefault();hide();}
        if(event.key==='Enter'||event.key===' '){event.preventDefault();show(element);}
      });
    });
  });
}
function reportMetric(label,value,unit=''){
  return `<div class="report-metric"><dt>${escape(label)}</dt><dd>${escape(value)}${unit?` <small>${escape(unit)}</small>`:''}</dd></div>`;
}
function bodyMetricsMarkup(r){
  const m=r.metrics||{},main=[];
  if(r.weight!=null)main.push(reportMetric('体重',r.weight,'kg'));
  for(const key of ['bodyFatPercent','skeletalMuscleKg','bmi'])if(m[key]!=null)main.push(reportMetric(bodyMetricLabels[key][0],m[key],bodyMetricLabels[key][1]));
  const extra=[];
  if(r.height!=null)extra.push(reportMetric('身高',r.height,'cm'));
  for(const [key,[label,unit]] of Object.entries(bodyMetricLabels))if(!['bodyFatPercent','skeletalMuscleKg','bmi'].includes(key)&&m[key]!=null)extra.push(reportMetric(label,m[key],unit));
  return (main.length?`<dl class="report-metrics">${main.join('')}</dl>`:'')+(extra.length?`<details class="report-more"><summary>更多身体指标 <span>${extra.length} 项</span></summary><dl class="report-metrics report-metrics-extra">${extra.join('')}</dl></details>`:'');
}
function reportOriginalsMarkup(r){
  const files=reportAttachments(r);
  return files.length?`<div class="report-originals" aria-label="报告原件">${files.map((a,i)=>`<a href="/api/report/${encodeURIComponent(r.id)}/${encodeURIComponent(a.key)}" target="_blank" rel="noopener" class="report-original-link"><span>原件${files.length>1?' '+(i+1):''} ↗</span><small>${escape(a.name)}</small></a>`).join('')}</div>`:'';
}
function reportContentMarkup(r){
  const source=r.source||(r.provenance?.method==='self_report'?'本人记录':''),measured=r.provenance?.measuredAt;
  return `<div class="report-card-content"><div class="report-meta"><span>${escape(r.kind)} · ${escape(r.date)}${measured?' '+escape(measured.slice(11,16)):''}</span>${source?`<span class="report-source">${escape(source)}</span>`:''}</div><h3>${escape(r.title)}</h3>${bodyMetricsMarkup(r)}${r.note?'<p class="report-note">'+escape(r.note)+'</p>':''}${reportOriginalsMarkup(r)}</div>`;
}
async function loadWellness(){
  document.querySelector('.demo-dot').textContent='身体档案';
  $('subtitle').textContent=page==='sport'?'运动过的日子，都在这里。':'把每一次检查，好好留存。';
  try{
    const r=await fetch('/api/wellness',{cache:'no-store'});if(!r.ok)throw Error();
    wellness=await r.json();wellnessReady=true;
    $('sport-status').textContent='训练、体重与体测，按日留存';$('health-status').textContent='每一次检查，都有据可查';
    renderWellness();
  }catch{
    wellnessReady=false;$('sport-status').textContent=$('health-status').textContent='暂时无法读取身体档案，请稍后重试。';renderWellness();
  }
}
function renderWellness(){
  const [y,m]=sportMonth.split('-').map(Number);
  $('sport-month-title').innerHTML=`<small>${y}</small>${m}月`;
  $('sport-count').textContent=sportView==='training'?Object.keys(wellness.training).filter(d=>d.startsWith(sportMonth)).length+' 天有运动记录':'体重与体测记录';
  const offset=(new Date(y,m-1,1).getDay()+6)%7,total=new Date(y,m,0).getDate();
  $('sport-calendar').innerHTML='<div class="calendar-blank"></div>'.repeat(offset)+Array.from({length:total},(_,i)=>{
    const d=sportMonth+'-'+String(i+1).padStart(2,'0'),t=wellness.training[d],rs=wellness.reports.filter(r=>r.date===d&&(r.kind==='体测'||r.weight));
    return `<button class="sport-day ${d===todayCN?'is-today':''}" data-exercise-day="${d}" aria-label="${d}${sportView==='training'?(t?' 有运动记录':''):(rs.length?' 有身体记录':'')}"><span>${i+1}</span>${sportView==='training'?(t?`<i class="activity-dot"></i>${t.items.slice(0,2).map(x=>`<small class="training-label">${escape(x.name)}</small>`).join('')}${t.items.length>2?'<small>更多记录</small>':''}`:''):(rs.length?rs.slice(0,2).map(r=>`<small class="training-label body-label">${r.weight?escape(r.weight)+' kg':escape(r.kind)}</small>`).join(''):'')}</button>`;
  }).join('');
  $('sport-calendar').querySelectorAll('button').forEach(b=>b.onclick=()=>openExercise(b.dataset.exerciseDay));
  const weight=wellness.reports.find(r=>r.weight),height=wellness.reports.find(r=>r.height);
  $('sport-weight').innerHTML=(weight?escape(weight.weight):'—')+' <small>kg</small>';
  $('sport-weight-date').textContent=weight?weight.date+(weight.source?' · '+weight.source:''):'体重待记录';
  $('health-height').textContent=height?.height||'—';$('health-weight').textContent=weight?.weight||'—';
  const hospital=wellness.reports.find(r=>['体检','就诊'].includes(r.kind));
  $('hospital-days').innerHTML=(hospital?Math.max(0,dateDiff(todayCN,hospital.date)):'—')+' <small>天</small>';
  $('hospital-label').textContent=hospital?hospital.date+' · '+hospital.title:'添加体检或就诊日期后显示';
  const due=wellness.nextAssessment;
  $('assessment-date').textContent=due?(due===todayCN?'今天可以安排体测':due):'每月月初';
  $('assessment-note').textContent=window.tiangongAccount?.user.id==='owner'?'每月1日安排一次体测，由山君在当天11:00提醒。':'按月留存体测，慢慢看身体的变化。';
  renderBodyTrends();
  renderReportList('body-reports',wellness.reports.filter(r=>r.kind==='体测'));
  renderReportList('health-reports',wellness.reports.filter(r=>!['体测','体重记录'].includes(r.kind)));
}
function renderReportList(id,rs){
  $(id).innerHTML=rs.length?rs.map(r=>`<article class="report-card report-card-v20">${reportContentMarkup(r)}</article>`).join(''):'<div class="report-empty">还没有报告。可以先留存原件，再逐步整理内容。</div>';
}
function openExercise(d){
  $('exercise-day').textContent=d;const t=wellness.training[d],rs=wellness.reports.filter(r=>r.date===d&&(r.kind==='体测'||r.weight));
  $('exercise-details').innerHTML=sportView==='training'?(t?t.items.map(x=>`<article class="exercise-card"><span class="tag">${escape(x.source)}</span><h3>${escape(x.name)}</h3><p>${x.start?escape(x.start)+' — '+escape(x.end):'时间待补充'}</p><div class="exercise-numbers"><div><b>${x.minutes??'—'}</b><span>分钟</span></div><div><b>${x.kcal??'—'}</b><span>大卡</span></div></div></article>`).join('')+(t.moves.length?'<p class="muted">动作：'+t.moves.map(escape).join('、')+'</p>':'')+'<p class="detail-note">'+escape(t.note)+'</p>':'<p class="empty-day">这一天尚无运动记录。<br>不代表当天没有运动。</p>'):(rs.length?rs.map(r=>`<article class="exercise-card report-card-v20">${reportContentMarkup(r)}</article>`).join(''):'<p class="empty-day">这一天尚无体重或体测记录。</p>');
  $('exercise-detail').showModal();
}
function moveSportMonth(n){const [y,m]=sportMonth.split('-').map(Number),d=new Date(y,m-1+n,1);sportMonth=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');renderWellness();}
$('sport-prev').onclick=()=>moveSportMonth(-1);$('sport-next').onclick=()=>moveSportMonth(1);$('sport-now').onclick=()=>{sportMonth=todayCN.slice(0,7);renderWellness();};$('exercise-close').onclick=()=>$('exercise-detail').close();
document.querySelectorAll('[data-sport-view]').forEach(b=>b.onclick=()=>{sportView=b.dataset.sportView;document.querySelectorAll('[data-sport-view]').forEach(x=>x.classList.toggle('selected',x===b));renderWellness();});
document.querySelectorAll('[data-add-report]').forEach(b=>b.onclick=()=>{
  if(!wellnessReady){toast('请先连接服务');return;}
  $('report-form').reset();$('report-kind').value=b.dataset.addReport;$('report-date').value=todayCN;$('report-date').max=todayCN;$('report-error').textContent='';$('report-editor').showModal();
});
$('report-close').onclick=()=>$('report-editor').close();
$('report-form').onsubmit=async e=>{
  e.preventDefault();$('report-save').disabled=true;$('report-error').textContent='';
  try{
    const data={kind:$('report-kind').value,date:$('report-date').value,title:$('report-title').value.trim(),height:$('report-height').value||null,weight:$('report-weight').value||null,note:$('report-note').value,metrics:{},source:'本人记录',provenance:{method:'manual',recordedBy:'本人'}};
    for(const [id,key] of [['report-body-fat','bodyFatPercent'],['report-skeletal-muscle','skeletalMuscleKg'],['report-bmi','bmi']])if($(id)?.value)data.metrics[key]=$(id).value;
    const files=Array.from($('report-file').files);
    if(files.length>8||files.reduce((n,f)=>n+f.size,0)>12*1024*1024)throw Error('最多添加8份原件，合计不超过12MB');
    data.attachments=await Promise.all(files.map(async file=>({name:file.name,base64:await new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('附件读取失败'));reader.readAsDataURL(file);
    })})));
    const res=await fetch('/api/wellness/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),result=await res.json();
    if(!res.ok)throw Error(result.error||'保存失败');
    $('report-editor').close();await loadWellness();toast('记录与原件已保存');
  }catch(err){$('report-error').textContent=err.message;}finally{$('report-save').disabled=false;}
};
