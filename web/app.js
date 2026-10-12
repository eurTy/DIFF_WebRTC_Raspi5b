import {unpack,Clock} from './protocol.js';
const $=id=>document.getElementById(id), ctx=$('video').getContext('2d'), overlay=$('regions').getContext('2d');
const defaults=[{name:'process-A',x:.075,y:.19,w:.275,h:.28,weight:6},{name:'process-B',x:.70,y:.48,w:.18,h:.22,weight:4}];
let regions=structuredClone(defaults), ws=null, clockWs=null, epoch=0, frames=0, overruns=0, latest=null, samples=[], history=[], pending=new Map(), sequence=0, drag=null;
let clock=new Clock(), clockTimer=null;
const url=path=>`${location.protocol==='https:'?'wss':'ws'}://${location.host}${path}`;
const metric=(id,value,digits=1)=>$(id).textContent=Number.isFinite(value)?value.toFixed(digits):'--';
function fields() {const r=regions[+$('region-choice').value];$('weight').value=r.weight;for(const key of ['x','y','w','h'])$('roi-'+key).value=r[key];drawRegions();}
function drawRegions(){overlay.clearRect(0,0,1280,720);if(!$('overlay').checked)return;regions.forEach((r,i)=>{overlay.strokeStyle=i? '#f3bd50':'#44edc4';overlay.lineWidth=3;overlay.strokeRect(r.x*1280,r.y*720,r.w*1280,r.h*720);overlay.fillStyle=overlay.strokeStyle;overlay.font='20px sans-serif';overlay.fillText(`${r.name} / ${r.weight}`,r.x*1280+5,Math.max(23,r.y*720-8));});}
function stop(){epoch++;ws?.close();clockWs?.close();clearInterval(clockTimer);pending.clear();clock=new Clock();ws=clockWs=null;$('status').textContent='已停止';}
function clockConnect(token){
  clockWs=new WebSocket(url('/clock'));const connection=clockWs;
  const ping=()=>{if(token!==epoch||connection.readyState!==1)return;const id=++sequence;pending.set(id,performance.now());for(const [k,v] of pending)if(performance.now()-v>10000)pending.delete(k);connection.send(JSON.stringify({type:'clock',id}));};
  connection.onopen=()=>{ping();clockTimer=setInterval(ping,1000);};
  connection.onmessage=e=>{if(token!==epoch)return;try{const m=JSON.parse(e.data),t0=pending.get(m.id);pending.delete(m.id);if(t0!==undefined)clock.add(t0,m.received_ms,m.sent_ms,performance.now());}catch{}};
  connection.onclose=()=>{if(token===epoch)clearInterval(clockTimer);};
}
async function connect(){
  for(const input of document.querySelectorAll('input[type=number]'))if(!input.reportValidity())return;
  if(regions.some(r=>![r.x,r.y,r.w,r.h,r.weight].every(Number.isFinite)||r.x<0||r.y<0||r.w<.02||r.h<.02||r.x+r.w>1||r.y+r.h>1||r.weight<1||r.weight>10)||regions.reduce((n,r)=>n+r.w*r.h,0)>.5){$('notice').textContent='区域须位于画面内，合计面积不超过 50%，权重为 1 至 10。';return;}
  const previous=ws;stop();const token=epoch;frames=overruns=0;latest=null;samples=[];history=[];$('frames').textContent='0';$('overruns').textContent='0';for(const id of ['latency','age','encoding','budget','level','patches'])$(id).textContent='--';$('notice').textContent='';$('empty').hidden=false;$('empty').textContent='等待帧';$('status').textContent='连接中';
  if(previous&&previous.readyState!==WebSocket.CLOSED)await new Promise(resolve=>{const timer=setTimeout(resolve,1000);previous.addEventListener('close',()=>{clearTimeout(timer);resolve();},{once:true});});
  if(token!==epoch)return;
  clockConnect(token);const connection=new WebSocket(url('/stream'));ws=connection;connection.binaryType='arraybuffer';
  connection.onopen=()=>connection.send(JSON.stringify({type:'start',mode:$('mode').value,kbps:+$('kbps').value,fps:+$('fps').value,seed:+$('seed').value,regions}));
  let decoding=false;
  connection.onmessage=async event=>{
    if(token!==epoch)return;
    if(typeof event.data==='string'){try{const m=JSON.parse(event.data);if(m.type==='ready'){$('status').textContent='传输中';$('source').textContent=m.source==='SYNTHETIC_TASK_FIXTURE'?'合成实验输入 · 非实机画面':'实时摄像头 · 只读输入';$('timing-kind').textContent=m.source==='LIVE_READ_ONLY_ADAPTER'?'采集发布时刻':m.source==='LIVE_USB_NEW_CAPTURE'?'读取完成时刻':'生成开始时刻';}else if(m.type==='source_error')$('notice').textContent='输入暂不可用：'+m.reason;}catch{}return;}
    if(decoding){connection.close(1008,'Multiple in-flight frames');return;}decoding=true;const bitmaps=[];
    try{
      const {meta,images}=unpack(event.data);
      for(const blob of images){const bitmap=await createImageBitmap(blob);bitmaps.push(bitmap);if(bitmap.width>1280||bitmap.height>720)throw Error('Oversized JPEG');}
      if(token!==epoch||connection.readyState!==1)return;
      const composed=document.createElement('canvas');composed.width=1280;composed.height=720;const target=composed.getContext('2d');
      bitmaps.forEach((bitmap,i)=>target.drawImage(bitmap,...meta.parts[i].rect));ctx.drawImage(composed,0,0);
      const now=performance.now(),latency=clock.age(meta.captured_ms,now);latest={meta,shown:now};frames++;if(meta.budget_exceeded)overruns++;
      const point={time:now,frame:meta.id,latency_ms:latency,bytes:event.data.byteLength,encode_ms:meta.encode_ms,level:meta.level,budget_bytes:meta.budget_bytes,budget_exceeded:meta.budget_exceeded,parts:meta.parts.map(p=>p.name)};
      samples.push(point);history.push(point);if(history.length>10000)history.shift();samples=samples.filter(p=>now-p.time<60000);
      metric('latency',latency);metric('encoding',meta.encode_ms);metric('budget',meta.budget_bytes/1000);$('frames').textContent=frames;$('overruns').textContent=overruns;$('level').textContent=meta.level;$('patches').textContent=meta.parts.slice(1).map(p=>p.name).join(', ')||'无';$('empty').hidden=true;$('notice').textContent='';
      connection.send(JSON.stringify({type:'ack',id:meta.id}));
    }catch(error){if(token===epoch){$('notice').textContent=error.message;connection.close(1008,'Invalid frame');}}
    finally{bitmaps.forEach(b=>b.close());decoding=false;}
  };
  connection.onclose=event=>{if(token===epoch){$('status').textContent='连接已关闭';if(event.code===1008)$('notice').textContent='参数或确认消息无效，或传输超过服务期限。';clockWs?.close();}};
  connection.onerror=()=>{if(token===epoch)$('notice').textContent='网络连接失败';};
}
function chart(now){const canvas=$('trend'),c=canvas.getContext('2d');c.clearRect(0,0,1000,150);const points=samples.filter(p=>p.latency_ms!==null),max=Math.max(100,...points.map(p=>p.latency_ms));c.font='12px sans-serif';c.fillStyle='#60767c';c.fillText(`${Math.ceil(max)} ms`,8,15);c.strokeStyle='#dce4e5';c.beginPath();c.moveTo(0,145);c.lineTo(1000,145);c.stroke();c.strokeStyle='#00867a';c.lineWidth=2;c.beginPath();points.forEach((p,i)=>{const x=1000*(1-(now-p.time)/60000),y=142-120*p.latency_ms/max;i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
setInterval(()=>{const now=performance.now(),est=clock.estimate(now),age=latest?clock.age(latest.meta.captured_ms,now):null;metric('age',age);document.body.dataset.stale=latest && (age===null||age>500)?'true':'false';$('sync').textContent=est?`校时 RTT ${est.rtt.toFixed(1)} ms · 对称假设误差约 ±${(est.rtt/2).toFixed(1)} ms`:'时钟未校准';const recent=samples.filter(p=>now-p.time<2000);metric('rate',recent.length/2);metric('bandwidth',recent.reduce((n,p)=>n+p.bytes,0)*8/2/1e6,3);chart(now);},250);
$('connect').onclick=connect;$('pause').onclick=stop;$('region-choice').onchange=fields;$('overlay').onchange=drawRegions;
for(const key of ['x','y','w','h'])$('roi-'+key).onchange=()=>{regions[+$('region-choice').value][key]=+$('roi-'+key).value;drawRegions();};
$('weight').onchange=()=>{regions[+$('region-choice').value].weight=+$('weight').value;drawRegions();};$('reset').onclick=()=>{regions=structuredClone(defaults);fields();};
function position(e){const b=$('regions').getBoundingClientRect();return {x:Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))};}
$('regions').onpointerdown=e=>{drag=position(e);$('regions').setPointerCapture(e.pointerId);};
$('regions').onpointerup=e=>{if(!drag)return;const end=position(e),r=regions[+$('region-choice').value],w=Math.abs(end.x-drag.x),h=Math.abs(end.y-drag.y);if(w>=.02&&h>=.02){Object.assign(r,{x:+Math.min(end.x,drag.x).toFixed(4),y:+Math.min(end.y,drag.y).toFixed(4),w:+w.toFixed(4),h:+h.toFixed(4)});fields();}drag=null;};$('regions').onpointercancel=()=>drag=null;
$('download').onclick=()=>{const payload={schema:1,exported_at:new Date().toISOString(),source:latest?.meta.source,measurement:'software timestamp estimate, not glass-to-glass',clock_assumption:'symmetric minimum-RTT path',frames:history};const link=document.createElement('a'),blobUrl=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));link.href=blobUrl;link.download='semantic-measurement.json';link.click();setTimeout(()=>URL.revokeObjectURL(blobUrl),1000);};
window.addEventListener('pagehide',stop);window.lucide?.createIcons();fields();
fetch('/api/status').then(r=>r.json()).then(status=>{$('fixture-control').hidden=status.source!=='fixture';connect();}).catch(()=>{$('notice').textContent='服务不可达';});
