import {Clock} from './protocol.js';
import {parsePiece} from './piece.js';

const $=id=>document.getElementById(id),video=$('video').getContext('2d'),boxes=$('regions').getContext('2d');
const regions=[{name:'process-A',x:.075,y:.19,w:.275,h:.28,weight:6},{name:'process-B',x:.70,y:.48,w:.18,h:.22,weight:4}];
let ws=null,clockWs=null,clock=new Clock(),epoch=0,active=false,pending=null,current=null,frames=0,patches=0,rejected=0,reconnects=0,history=[],lastPatch={},bytes=[],lastFrameAt=0,patchDeadline=80;
const metric=(id,value,digits=1)=>$(id).textContent=Number.isFinite(value)?value.toFixed(digits):'--';
const address=path=>`${location.protocol==='https:'?'wss':'ws'}://${location.host}${path}`;
function drawBoxes(){boxes.clearRect(0,0,1280,720);regions.forEach((r,i)=>{boxes.strokeStyle=i?'#f6c458':'#35efcf';boxes.lineWidth=3;boxes.strokeRect(r.x*1280,r.y*720,r.w*1280,r.h*720);boxes.font='18px sans-serif';boxes.fillStyle=boxes.strokeStyle;boxes.fillText(r.name,r.x*1280+5,Math.max(22,r.y*720-6));});}
function stop(){active=false;epoch++;if(pending)finalize(false);ws?.close();clockWs?.close();ws=clockWs=null;pending=null;$('status').textContent='已停止';}
function startClock(token){
  const connection=new WebSocket(address('/clock'));clockWs=connection;let id=0;const requests=new Map();
  const ping=()=>{if(token!==epoch||connection.readyState!==1)return;const key=++id;requests.set(key,performance.now());connection.send(JSON.stringify({type:'clock',id:key}));for(const [k,t] of requests)if(performance.now()-t>5000)requests.delete(k);};
  connection.onopen=()=>{ping();setTimeout(ping,150);};
  connection.onmessage=e=>{try{const message=JSON.parse(e.data),sent=requests.get(message.id);requests.delete(message.id);if(token===epoch&&sent!==undefined)clock.add(sent,message.received_ms,message.sent_ms,performance.now());}catch{}};
  const timer=setInterval(()=>{if(token!==epoch){clearInterval(timer);return;}ping();},1000);
}
function finalize(patchShown){
  const item=pending;if(!item||item.done)return;item.done=true;clearTimeout(item.timer);pending=null;
  const wait=Math.min(100,Math.max(0,performance.now()-item.baseDrawnAt));
  if(ws?.readyState===1)ws.send(JSON.stringify({type:'ack',id:item.id,base:item.baseShown,patch:patchShown,wait_ms:wait}));
  history.push({time:performance.now(),frame:item.id,base:item.baseShown,patch:patchShown,latency_ms:item.latency,source:item.source});
  if(history.length>10000)history.shift();
}
async function handlePiece(buffer,token){
  const {meta,blob,bytes:count}=parsePiece(buffer),now=performance.now();
  bytes.push({time:now,count});
  if(meta.kind==='patch'){
    const item=pending;
    if(!item||item.done||item.id!==meta.id||item.patchName!==meta.name||current?.id!==meta.id)return;
    const age=clock.age(meta.captured_ms,now);
    if(now-item.baseDrawnAt>patchDeadline||age!==null&&age>250)return;
    const bitmap=await createImageBitmap(blob);
    try{
      if(token!==epoch||pending!==item||item.done||current?.id!==meta.id||performance.now()-item.baseDrawnAt>patchDeadline)return;
      video.drawImage(bitmap,...meta.rect);lastPatch[meta.name]=performance.now();patches++;$('patches').textContent=patches;
      finalize(true);
    }finally{bitmap.close();}
    return;
  }
  if(pending)finalize(false);
  const age=clock.age(meta.captured_ms,now);
  if(current&&meta.id<=current.id||age!==null&&age>250){
    rejected++;$('rejected').textContent=rejected;
    pending={id:meta.id,baseShown:false,patchName:null,baseDrawnAt:now,latency:age,source:meta.source,done:false};
    finalize(false);return;
  }
  const bitmap=await createImageBitmap(blob);
  try{
    if(token!==epoch)return;
    video.clearRect(0,0,1280,720);video.drawImage(bitmap,...meta.rect);
    const shown=performance.now();current={id:meta.id,captured_ms:meta.captured_ms,shown,latency:clock.age(meta.captured_ms,shown)};
    current.source=meta.source;lastFrameAt=shown;frames++;$('empty').hidden=true;$('status').textContent='传输中';$('source').textContent=meta.source==='SYNTHETIC_TASK_FIXTURE'?'合成实验输入 · 非实机画面':'实时摄像头 · 只读输入';
    metric('latency',current.latency);
    pending={id:meta.id,baseShown:true,patchName:meta.patch_name,baseDrawnAt:shown,latency:current.latency,source:meta.source,done:false};
    if(meta.patch_name)pending.timer=setTimeout(()=>finalize(false),patchDeadline);
    else finalize(false);
  }finally{bitmap.close();}
}
function start(){
  for(const field of document.querySelectorAll('input[type=number]'))if(!field.reportValidity())return;
  stop();active=true;clock=new Clock();current=null;pending=null;frames=patches=rejected=reconnects=0;history=[];lastPatch={};bytes=[];$('empty').hidden=false;$('empty').textContent='等待帧';$('notice').textContent='';$('patches').textContent='0';$('rejected').textContent='0';
  open();
}
function open(){
  if(!active)return;epoch++;const token=epoch;startClock(token);$('status').textContent=reconnects?'重新连接中':'连接中';
  const connection=new WebSocket(address('/stream'));ws=connection;connection.binaryType='arraybuffer';let queue=Promise.resolve(),lastMessage=performance.now();
  connection.onopen=()=>{if(token===epoch)connection.send(JSON.stringify({type:'start',fps:+$('fps').value,kbps:+$('kbps').value,regions}));};
  connection.onmessage=e=>{
    if(token!==epoch)return;lastMessage=performance.now();
    if(typeof e.data==='string'){try{const message=JSON.parse(e.data);if(message.type==='source_error')$('notice').textContent='输入暂不可用：'+message.reason;else if(message.type==='ready'&&Number.isInteger(message.patch_deadline_ms)&&message.patch_deadline_ms>=20&&message.patch_deadline_ms<=200)patchDeadline=message.patch_deadline_ms;}catch{}return;}
    queue=queue.then(()=>handlePiece(e.data,token)).catch(error=>{if(token===epoch){$('notice').textContent=error.message;connection.close(1008,'Invalid piece');}});
  };
  connection.onclose=e=>{if(token!==epoch||!active)return;if(pending)finalize(false);clockWs?.close();$('status').textContent='等待重连';$('notice').textContent=e.code===1008?'配置或数据无效':'连接中断，等待恢复';if(e.code===1008){active=false;return;}setTimeout(()=>{if(token===epoch&&active){reconnects++;open();}},Math.min(2000,500*2**Math.min(reconnects,2)));};
  connection.onerror=()=>{if(token===epoch)$('notice').textContent='网络连接失败';};
  const watchdog=setInterval(()=>{if(token!==epoch){clearInterval(watchdog);return;}if(performance.now()-lastMessage>5000&&connection.readyState===1)connection.close(4001,'No fresh input');},1000);
}
setInterval(()=>{
  const now=performance.now(),age=current?clock.age(current.captured_ms,now):null;
  metric('age',age);const stale=!!current&&(age===null||age>500);document.body.dataset.stale=String(stale);$('stale').hidden=!stale;$('stale').textContent=age===null?'时效未校准':'画面已过期';
  metric('rate',frames&&lastFrameAt?history.filter(p=>p.base&&now-p.time<2000).length/2:0);
  bytes=bytes.filter(p=>now-p.time<2000);metric('bandwidth',bytes.reduce((total,p)=>total+p.count,0)*8/2e6,3);
  const estimate=clock.estimate(now);$('sync').textContent=estimate?`校时 RTT ${estimate.rtt.toFixed(1)} ms · 对称路径假设`:'时钟未校准';
  $('region-age').textContent=regions.map(r=>`${r.name}：${lastPatch[r.name]===undefined?'未确认':Math.round(now-lastPatch[r.name])+' ms 前清晰显示'}`).join(' · ');
},250);
$('connect').onclick=start;$('pause').onclick=stop;
$('preset').onchange=()=>{const p=$('preset').value;if(p==='custom')return;$('fps').value=p==='smooth'?20:10;$('kbps').value=p==='smooth'?2400:1200;};
for(const id of ['fps','kbps'])$(id).onchange=()=>{$('preset').value='custom';};
$('download').onclick=()=>{const data={schema:4,measurement:'software timestamp estimate, not glass-to-glass',source:current?.source,config:{fps:+$('fps').value,kbps:+$('kbps').value,regions},history_truncated:frames>history.length,reconnects,frames:history};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='task-freshness-v04.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
window.addEventListener('pagehide',stop);window.lucide?.createIcons();drawBoxes();start();
