import {unpack,Clock,watchdogMs} from './protocol.js';
const $=id=>document.getElementById(id), ctx=$('video').getContext('2d'), overlay=$('regions').getContext('2d');
const composed=document.createElement('canvas');composed.width=1280;composed.height=720;const target=composed.getContext('2d');
const defaults=[{name:'process-A',x:.075,y:.19,w:.275,h:.28,weight:6},{name:'process-B',x:.70,y:.48,w:.18,h:.22,weight:4}];
let regions=structuredClone(defaults), ws=null, clockWs=null, epoch=0, frames=0, overruns=0, latest=null, samples=[], history=[], pending=new Map(), sequence=0, drag=null;
let clock=new Clock(), clockTimer=null, retryTimer=null, watchdog=null, active=false, applied=null, reconnects=0, dropped=0, retryAttempt=0, events=[];
const url=path=>`${location.protocol==='https:'?'wss':'ws'}://${location.host}${path}`;
const metric=(id,value,digits=1)=>$(id).textContent=Number.isFinite(value)?value.toFixed(digits):'--';
function notice(kind,text){$('notice').dataset.kind=kind;$('notice').textContent=text;}
function record(type,detail={}){events.push({time:performance.now(),type,...detail});if(events.length>1000)events.shift();}
function fields(){const r=regions[+$('region-choice').value];$('weight').value=r.weight;for(const key of ['x','y','w','h'])$('roi-'+key).value=r[key];drawRegions();}
function drawRegions(){overlay.clearRect(0,0,1280,720);if(!$('overlay').checked)return;regions.forEach((r,i)=>{overlay.strokeStyle=i?'#f3bd50':'#44edc4';overlay.lineWidth=3;overlay.strokeRect(r.x*1280,r.y*720,r.w*1280,r.h*720);overlay.fillStyle=overlay.strokeStyle;overlay.font='20px sans-serif';overlay.fillText(`${r.name} / ${r.weight}`,r.x*1280+5,Math.max(23,r.y*720-8));});}
function disconnect(){epoch++;clearTimeout(retryTimer);retryTimer=null;clearInterval(watchdog);clearInterval(clockTimer);ws?.close();clockWs?.close();pending.clear();clock=new Clock();ws=clockWs=null;}
function stop(){active=false;record('pause');disconnect();$('status').textContent='已停止';}
function clockConnect(token){
  clockWs=new WebSocket(url('/clock'));const connection=clockWs;
  const ping=()=>{if(token!==epoch||connection.readyState!==1)return;const id=++sequence;pending.set(id,performance.now());for(const [k,v] of pending)if(performance.now()-v>10000)pending.delete(k);connection.send(JSON.stringify({type:'clock',id}));};
  connection.onopen=()=>{ping();setTimeout(ping,150);setTimeout(ping,300);clockTimer=setInterval(ping,1000);};
  connection.onmessage=e=>{if(token!==epoch)return;try{const m=JSON.parse(e.data),t0=pending.get(m.id);pending.delete(m.id);if(t0!==undefined)clock.add(t0,m.received_ms,m.sent_ms,performance.now());}catch{}};
  connection.onclose=()=>{if(token===epoch){clearInterval(clockTimer);if(active&&ws?.readyState===1)ws.close(4000,'Clock disconnected');}};
}
async function connect(){
  for(const input of document.querySelectorAll('input[type=number]'))if(!input.reportValidity())return;
  if(regions.some(r=>![r.x,r.y,r.w,r.h,r.weight].every(Number.isFinite)||r.x<0||r.y<0||r.w<.02||r.h<.02||r.x+r.w>1||r.y+r.h>1||r.weight<1||r.weight>10)||regions.reduce((n,r)=>n+r.w*r.h,0)>.5){notice('parameter','区域须位于画面内，合计面积不超过 50%，权重为 1 至 10。');return;}
  applied={type:'start',mode:$('mode').value,feedback:$('feedback').value,kbps:+$('kbps').value,fps:+$('fps').value,seed:+$('seed').value,regions:structuredClone(regions)};
  stop();active=true;frames=overruns=dropped=reconnects=retryAttempt=0;latest=null;samples=[];history=[];events=[];
  for(const id of ['frames','overruns','dropped','reconnects'])$(id).textContent='0';
  for(const id of ['latency','age','encoding','budget','level','patches'])$(id).textContent='--';
  notice('','');$('empty').hidden=false;$('empty').textContent='等待帧';await openStream();
}
async function openStream(){
  const previous=ws;disconnect();const token=epoch;
  $('status').textContent=reconnects?'重新连接中':'连接中';
  if(previous&&previous.readyState!==WebSocket.CLOSED)await new Promise(resolve=>{const timer=setTimeout(resolve,1000);previous.addEventListener('close',()=>{clearTimeout(timer);resolve();},{once:true});});
  if(token!==epoch||!active)return;
  clockConnect(token);const connection=new WebSocket(url('/stream'));ws=connection;connection.binaryType='arraybuffer';
  let lastReceived=performance.now(),receivedBytes=0,decoding=false;const queue=[];
  function retry(code){
    if(token!==epoch)return;
    record('disconnect',{code});disconnect();
    if(!active)return;
    if(code===1008){active=false;$('status').textContent='参数错误';notice('parameter','参数或确认消息无效，请检查配置。');return;}
    const delay=Math.min(1500,500*2**Math.min(retryAttempt++,2));
    $('status').textContent='等待重连';notice('network','连接中断，等待恢复');
    retryTimer=setTimeout(()=>{retryTimer=null;if(!active)return;reconnects++;$('reconnects').textContent=reconnects;openStream();},delay);
  }
  connection.onopen=()=>{if(token===epoch&&active)connection.send(JSON.stringify(applied));};
  async function drain(){
    if(decoding)return;decoding=true;
    try{
      while(queue.length&&token===epoch){
        const event=queue.shift(),bitmaps=[];
        try{
          const {meta,images}=unpack(event.data);
          let latency=clock.age(meta.captured_ms,performance.now());
          let discard=applied.feedback==='realtime'&&latency!==null&&latency>500;
          if(!discard){
            for(const blob of images){const bitmap=await createImageBitmap(blob);bitmaps.push(bitmap);if(bitmap.width>1280||bitmap.height>720)throw Error('Oversized JPEG');}
            latency=clock.age(meta.captured_ms,performance.now());
            discard=applied.feedback==='realtime'&&latency!==null&&latency>500;
          }
          if(token!==epoch||connection.readyState!==1)return;
          let now=performance.now();
          if(!discard){
            bitmaps.forEach((bitmap,i)=>target.drawImage(bitmap,...meta.parts[i].rect));ctx.drawImage(composed,0,0);
            now=performance.now();latency=clock.age(meta.captured_ms,now);
            latest={meta,shown:now,latency};frames++;$('empty').hidden=true;retryAttempt=0;
            $('frames').textContent=frames;$('level').textContent=meta.level;$('patches').textContent=meta.parts.slice(1).map(p=>p.name).join(', ')||'无';
            if(['source','network'].includes($('notice').dataset.kind))notice('','');
          }else{dropped++;$('dropped').textContent=dropped;}
          if(meta.budget_exceeded)overruns++;
          // Late arrivals stay in the exported population, not only displayed frames.
          const point={time:now,received_at:event.received,frame:meta.id,latency_ms:latency,dropped:discard,endpoint:discard?'rejected-before-draw':'canvas-draw-complete',send_window:meta.send_window??1,bytes:event.data.byteLength,encode_ms:meta.encode_ms,level:meta.level,budget_bytes:meta.budget_bytes,budget_exceeded:meta.budget_exceeded,parts:meta.parts.map(p=>p.name)};
          samples.push(point);history.push(point);if(history.length>10000)history.shift();samples=samples.filter(p=>now-p.time<60000);
          metric('latency',latency);metric('encoding',meta.encode_ms);metric('budget',meta.budget_bytes/1000);$('overruns').textContent=overruns;
          $('window').textContent=meta.send_window??1;
          connection.send(JSON.stringify({type:'ack',id:meta.id,dropped:discard}));
        }finally{bitmaps.forEach(b=>b.close());}
      }
    }catch(error){if(token===epoch){notice('parameter',error.message);connection.close(1008,'Invalid frame');}}
    finally{decoding=false;}
  }
  connection.onmessage=event=>{
    if(token!==epoch)return;
    if(typeof event.data==='string'){
      try{const m=JSON.parse(event.data);if(m.type==='ready'){
        record('ready',{feedback:m.feedback,window:m.window});$('status').textContent='传输中';
        $('source').textContent=m.source==='SYNTHETIC_TASK_FIXTURE'?'合成实验输入 · 非实机画面':'实时摄像头 · 只读输入';
        $('timing-kind').textContent=m.source==='LIVE_READ_ONLY_ADAPTER'?'采集发布时刻':m.source==='LIVE_USB_NEW_CAPTURE'?'读取完成时刻':'生成开始时刻';
      }else if(m.type==='source_error')notice('source','输入暂不可用：'+m.reason);}catch{}return;
    }
    lastReceived=performance.now();receivedBytes=event.data.byteLength;
    if(queue.length>=2){connection.close(1008,'Receive window exceeded');return;}
    queue.push({data:event.data,received:lastReceived});drain();
  };
  connection.onclose=event=>retry(event.code);
  connection.onerror=()=>{if(token===epoch)notice('network','网络连接失败');};
  watchdog=setInterval(()=>{
    const timeout=watchdogMs(receivedBytes,applied.kbps,applied.fps);
    if(token===epoch&&performance.now()-lastReceived>timeout)retry(4001);
  },500);
}
function chart(now){const canvas=$('trend'),c=canvas.getContext('2d');c.clearRect(0,0,1000,150);const points=samples.filter(p=>p.latency_ms!==null),max=Math.max(100,...points.map(p=>p.latency_ms));c.font='12px sans-serif';c.fillStyle='#60767c';c.fillText(`${Math.ceil(max)} ms`,8,15);c.strokeStyle='#dce4e5';c.beginPath();c.moveTo(0,145);c.lineTo(1000,145);c.stroke();c.strokeStyle='#00867a';c.lineWidth=2;c.beginPath();points.forEach((p,i)=>{const x=1000*(1-(now-p.time)/60000),y=142-120*p.latency_ms/max;i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
setInterval(()=>{
  const now=performance.now(),est=clock.estimate(now);
  let age=latest?clock.age(latest.meta.captured_ms,now):null;
  if(age===null&&latest&&latest.latency!==null)age=latest.latency+now-latest.shown;
  metric('age',age);const stale=!!latest&&(age===null||age>500);document.body.dataset.stale=String(stale);$('stale').hidden=!stale;
  $('sync').textContent=est?`校时 RTT ${est.rtt.toFixed(1)} ms · 对称假设误差约 ±${(est.rtt/2).toFixed(1)} ms`:'时钟未校准 · 帧龄沿用上次估计';
  const recent=samples.filter(p=>now-p.time<2000);metric('rate',recent.filter(p=>!p.dropped).length/2);metric('bandwidth',recent.reduce((n,p)=>n+p.bytes,0)*8/2/1e6,3);chart(now);
},250);
$('connect').onclick=connect;$('pause').onclick=stop;$('region-choice').onchange=fields;$('overlay').onchange=drawRegions;
$('preset').onchange=()=>{const preset=$('preset').value;if(preset==='custom')return;$('fps').value=preset==='smooth'?20:10;$('kbps').value=preset==='smooth'?2400:1200;};
for(const id of ['fps','kbps'])$(id).onchange=()=>{$('preset').value='custom';};
for(const key of ['x','y','w','h'])$('roi-'+key).onchange=()=>{regions[+$('region-choice').value][key]=+$('roi-'+key).value;drawRegions();};
$('weight').onchange=()=>{regions[+$('region-choice').value].weight=+$('weight').value;drawRegions();};$('reset').onclick=()=>{regions=structuredClone(defaults);fields();};
function position(e){const b=$('regions').getBoundingClientRect();return {x:Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))};}
$('regions').onpointerdown=e=>{drag=position(e);$('regions').setPointerCapture(e.pointerId);};
$('regions').onpointerup=e=>{if(!drag)return;const end=position(e),r=regions[+$('region-choice').value],w=Math.abs(end.x-drag.x),h=Math.abs(end.y-drag.y);if(w>=.02&&h>=.02){Object.assign(r,{x:+Math.min(end.x,drag.x).toFixed(4),y:+Math.min(end.y,drag.y).toFixed(4),w:+w.toFixed(4),h:+h.toFixed(4)});fields();}drag=null;};$('regions').onpointercancel=()=>drag=null;
$('download').onclick=()=>{const payload={schema:2,exported_at:new Date().toISOString(),source:latest?.meta.source,measurement:'software timestamp estimate, not glass-to-glass',clock_assumption:'symmetric minimum-RTT path',config:applied,displayed:frames,discarded:dropped,reconnects,events,frames:history};const link=document.createElement('a'),blobUrl=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));link.href=blobUrl;link.download='semantic-measurement.json';link.click();setTimeout(()=>URL.revokeObjectURL(blobUrl),1000);};
window.addEventListener('pagehide',stop);window.lucide?.createIcons();fields();
window.addEventListener('online',()=>{if(active&&retryTimer!==null){reconnects++;$('reconnects').textContent=reconnects;openStream();}});
fetch('/api/status').then(r=>r.json()).then(status=>{$('fixture-control').hidden=status.source!=='fixture';connect();}).catch(()=>{notice('network','服务不可达');});
