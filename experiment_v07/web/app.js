import {Clock} from './protocol.js';
import {MeasurementLog,browserEnvironment} from './telemetry.js';
import {integrityError,crc32} from './integrity.js';

const $=id=>document.getElementById(id),video=$('video'),canvas=$('canvas'),ctx=canvas.getContext('2d'),overlay=$('regions'),boxes=overlay.getContext('2d');
const defaults=[{name:'process-A',x:.075,y:.19,w:.275,h:.28,weight:6},{name:'process-B',x:.70,y:.48,w:.18,h:.22,weight:4}];
const log=new MeasurementLog({url:location.href,appVersion:document.body.dataset.appVersion||'unknown',environment:browserEnvironment()});
let regions=structuredClone(defaults),active=false,epoch=0,session=null,reconnects=0,current=null,clock=new Clock(),frames=[],history=log.frames,bytes=[],samples=log.samples,lastRegions={},lastShown=0,retryTimer=null,lastSampleAt=null;
let failures=0,serverEvidence=null;
log.run.delivery_revision='correlated-recovery-v1';
async function fetchEvidence(){
  try{
    const response=await fetch('/api/diagnostics',{cache:'no-store',signal:AbortSignal.timeout(2000)});
    if(!response.ok)throw Error('Diagnostic fetch failed');
    const data=await response.json(),ids=new Set(log.sessions.map(s=>s.id));
    data.sessions=data.sessions.filter(s=>ids.has(s.client_session_id));
    const serverIds=new Set(data.sessions.map(s=>s.server_session_id));data.events=data.events.filter(e=>serverIds.has(e.server_session_id));
    serverEvidence=data;return true;
  }catch{log.event('server_diagnostics_unavailable');return false;}
}
function reportFault(s,event,details={}){
  if(!s.measurement.server_session_id)return;
  const data={server_session_id:s.measurement.server_session_id,event,client_at_ms:performance.now(),...details};
  fetch('/api/diagnostics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),keepalive:true,signal:AbortSignal.timeout(2000)}).catch(()=>{});
}
function rejectEvidence(s,m,data){
  reportFault(s,'integrity_rejected',{seq:m.seq,declared_bytes:m.payload_bytes,actual_bytes:data.length,expected_crc32:m.payload_crc32,actual_crc32:m.actual_crc32});
}
const settings=()=>({codec:$('codec').value,kbps:+$('kbps').value,fps:+$('fps').value,semantic:$('semantic').checked,regions:structuredClone(regions)});
const metric=(id,value,digits=1)=>$(id).textContent=Number.isFinite(value)?value.toFixed(digits):'--';
const address=path=>`${location.protocol==='https:'?'wss':'ws'}://${location.host}${path}`;
const baseline=new URL(location.href);baseline.port='8089';baseline.pathname='/';baseline.search='';baseline.hash='';$('baseline').href=baseline.href;
const params=new URLSearchParams(location.search);
if(['h264','jpeg'].includes(params.get('codec')))$('codec').value=params.get('codec');
function drawBoxes(){boxes.clearRect(0,0,1280,720);regions.forEach((r,i)=>{boxes.strokeStyle=i?'#f4c15b':'#3fe3ca';boxes.fillStyle=boxes.strokeStyle;boxes.lineWidth=3;boxes.strokeRect(r.x*1280,r.y*720,r.w*1280,r.h*720);boxes.font='18px sans-serif';boxes.fillText(r.name,r.x*1280+5,Math.max(20,r.y*720-6));});}
function parsePacket(buffer){
  if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<10||buffer.byteLength>2113544)throw Error('Invalid packet');
  const view=new DataView(buffer),length=view.getUint32(4);
  if(view.getUint32(0)!==0x53565035||length<2||length>16384||length+8>=buffer.byteLength)throw Error('Invalid header');
  const meta=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(new Uint8Array(buffer,8,length)));
  if(!meta||!['init','frame'].includes(meta.kind))throw Error('Invalid metadata');
  if(meta.kind==='frame'&&(!Number.isSafeInteger(meta.seq)||!Number.isSafeInteger(meta.id)||!Number.isFinite(meta.captured_ms)))throw Error('Invalid frame');
  return {meta,data:new Uint8Array(buffer,8+length)};
}
function dispose(reason='dispose'){
  epoch++;clearTimeout(retryTimer);const s=session;session=null;
  if(s){log.end(reason,s.measurement);clearInterval(s.timer);clearInterval(s.clockTimer);s.ws.close();s.clockWs?.close();if(s.callback)video.cancelVideoFrameCallback(s.callback);if(s.url)URL.revokeObjectURL(s.url);}
  video.pause();video.removeAttribute('src');video.load();
}
function stop(){log.event('pause');active=false;dispose('user_pause');$('status').textContent='已暂停';}
function record(meta){
  const now=performance.now();current={...meta,shown:now,latency:clock.age(meta.captured_ms,now)};lastShown=now;frames.push(now);
  const measurement=session?.measurement,estimate=clock.estimate(now);
  if(session&&now-session.openedAt>5000)failures=0;
  if(measurement)measurement.presented_frames++;
  if(meta.diagnostic)meta.diagnostic.presented_at_ms=now;
  log.entry('frames',measurement,{at:now,id:meta.id,seq:meta.seq,latency_ms:current.latency,captured_ms:meta.captured_ms,
    server_sent_ms:meta.server_sent_ms??null,width:meta.width,height:meta.height,encode_ms:meta.encode_ms,
    received_at_ms:meta.diagnostic?.received_at_ms??null,receive_to_present_ms:meta.diagnostic?now-meta.diagnostic.received_at_ms:null,
    server_session_id:measurement?.server_session_id??null,generation:meta.generation??0,
    clock_rtt_ms:estimate?.rtt??null,clock_offset_ms:estimate?.offset??null,source:meta.source,
    presentation_method:session?.codec==='h264'?'requestVideoFrameCallback':'canvas_draw_after_decode'});
  for(const r of session?.regions||[])lastRegions[r.name]=meta.captured_ms;
  $('empty').hidden=true;$('notice').textContent='';$('status').textContent='传输中';metric('latency',current.latency);
  $('dimensions').textContent=`${meta.width} × ${meta.height} · ${session?.codec==='h264'?'H.264':'JPEG 全景'} · 编码 ${meta.encode_ms.toFixed(1)} ms`;
}
function clockStart(s,token){
  const connection=new WebSocket(address('/clock'));s.clockWs=connection;let counter=0;const pending=new Map();
  const ping=()=>{if(token!==epoch||connection.readyState!==1)return;const id=++counter;pending.set(id,performance.now());connection.send(JSON.stringify({type:'clock',id}));for(const [key,time] of pending)if(performance.now()-time>3000)pending.delete(key);};
  connection.onopen=()=>{ping();setTimeout(ping,100);setTimeout(ping,250);};
  connection.onmessage=event=>{if(token!==epoch)return;try{const data=JSON.parse(event.data),sent=pending.get(data.id);pending.delete(data.id);if(sent!==undefined){const now=performance.now(),accepted=clock.add(sent,data.received_ms,data.sent_ms,now);log.entry('clocks',s.measurement,{at:now,t0:sent,t1:data.received_ms,t2:data.sent_ms,t3:now,accepted,estimate:clock.estimate(now)});}}catch{}};
  s.clockTimer=setInterval(ping,1000);
}
function fail(s,message){if(session!==s||s.closing)return;reportFault(s,'client_resync');s.closing=true;s.queue=[];s.queueBytes=0;log.event('resync',{message},s.measurement);$('notice').textContent=message;s.ws.close(4001,'Resync to live input');}
async function createMedia(s,token,mime){
  const generation=s.mediaGeneration=(s.mediaGeneration||0)+1;
  const valid=()=>token===epoch&&generation===s.mediaGeneration&&!s.closing;
  if(s.callback)video.cancelVideoFrameCallback(s.callback);
  if(s.url){video.pause();video.removeAttribute('src');video.load();URL.revokeObjectURL(s.url);}
  s.sb=null;s.appending=null;s.metadata=[];s.queue=[];s.queueBytes=0;
  const Constructor=window.ManagedMediaSource||window.MediaSource;
  if(!Constructor||!Constructor.isTypeSupported(mime)){
    log.event('unsupported_media',{mime},s.measurement);active=false;$('notice').textContent='此浏览器不支持 H.264 播放接口，请选择 JPEG 对照或原版';s.ws.close();return;
  }
  video.disableRemotePlayback=true;video.muted=true;
  const media=new Constructor();s.media=media;s.url=URL.createObjectURL(media);video.src=s.url;
  media.addEventListener('sourceopen',()=>{
    if(!valid())return;
    try{s.sb=media.addSourceBuffer(mime);s.sb.addEventListener('updateend',()=>{if(valid())pump(s,token);});s.sb.addEventListener('error',()=>{if(valid())fail(s,'解码缓冲错误，重新同步');});media.duration=Infinity;pump(s,token);}
    catch(error){fail(s,error.message);}
  },{once:true});
  const presented=(now,detail)=>{
    if(!valid())return;
    let match=null;
    for(const item of s.metadata)if(item.media_time<=detail.mediaTime+.005)match=item;
    if(match&&match.seq!==current?.seq)record(match);
    s.metadata=s.metadata.filter(m=>m.media_time>detail.mediaTime-.1);
    s.callback=video.requestVideoFrameCallback(presented);
  };
  if(video.requestVideoFrameCallback)s.callback=video.requestVideoFrameCallback(presented);
  else{active=false;$('notice').textContent='浏览器缺少呈现计时接口，请选择 JPEG 对照';s.ws.close();}
}
function ack(s,meta,outcome='accepted'){if(session===s&&!s.closing&&s.ws.readyState===1&&meta.kind==='frame'){s.ws.send(JSON.stringify({type:'ack',seq:meta.seq,outcome,rx_bytes:meta.diagnostic?.payload_bytes,rx_crc32:meta.actual_crc32}));if(meta.diagnostic)meta.diagnostic.ack_at_ms=performance.now();}}
function pump(s,token){
  if(token!==epoch||s.closing||!s.sb||s.sb.updating||s.media.readyState!=='open')return;
  if(s.appending){if(s.appending.diagnostic)s.appending.diagnostic.append_completed_at_ms=performance.now();ack(s,s.appending);s.appending=null;}
  try{
    if(s.sb.buffered.length&&video.currentTime-s.sb.buffered.start(0)>4){s.sb.remove(0,video.currentTime-2);return;}
    const item=s.queue.shift();if(!item)return;s.queueBytes-=item.data.byteLength;
    s.appending=item.meta;if(item.meta.diagnostic)item.meta.diagnostic.append_started_at_ms=performance.now();s.sb.appendBuffer(item.data);
    if(item.meta.kind==='frame')s.metadata.push(item.meta);
    if(s.metadata.length>120)s.metadata.shift();
    if(video.paused)video.play().catch(()=>{if(token===epoch){log.event('play_rejected',{},s.measurement);$('notice').textContent='轻触播放按钮以开始观看';}});
  }catch(error){fail(s,error.message);}
}
async function jpegPump(s,token){
  if(s.decoding||token!==epoch||s.closing)return;s.decoding=true;
  try{
    while(s.queue.length&&token===epoch&&!s.closing){
      const item=s.queue.shift();s.queueBytes-=item.data.byteLength;const m=item.meta;
      const invalid=integrityError(m,item.data,true,m.actual_crc32);
      if(invalid){
        rejectEvidence(s,m,item.data);
        if(m.diagnostic)m.diagnostic.drop_reason=invalid;
        log.event('integrity_rejected',{seq:m.seq,reason:invalid,declared_bytes:m.payload_bytes,actual_bytes:item.data.length},s.measurement);
        ack(s,m,'integrity_rejected');continue;
      }
      if(s.queue.length||clock.age(m.captured_ms,performance.now())>1000){
        if(m.diagnostic)m.diagnostic.drop_reason=s.queue.length?'superseded_jpeg':'stale_jpeg';
        log.event('stale_frame_dropped',{seq:m.seq,reason:m.diagnostic?.drop_reason},s.measurement);ack(s,m,'superseded');continue;
      }
      if(m.diagnostic)m.diagnostic.decode_started_at_ms=performance.now();
      if(!Array.isArray(m.parts)||m.parts.length>5||m.parts.length!==m.lengths?.length)throw Error('Invalid JPEG parts');
      let offset=0;const images=[];
      try{
        for(let i=0;i<m.parts.length;i++){
          const rect=m.parts[i].rect,n=m.lengths[i];
          if(!Number.isSafeInteger(n)||n<4||offset+n>item.data.length||!Array.isArray(rect)||rect.length!==4||!rect.every(Number.isSafeInteger)||rect[0]<0||rect[1]<0||rect[2]<=0||rect[3]<=0||rect[0]+rect[2]>1280||rect[1]+rect[3]>720)throw Error('Invalid JPEG bounds');
          images.push(await createImageBitmap(new Blob([item.data.slice(offset,offset+n)],{type:'image/jpeg'})));offset+=n;
        }
        if(offset!==item.data.length)throw Error('Trailing JPEG data');
        if(token!==epoch||s.closing)return;
        if(m.diagnostic)m.diagnostic.decode_completed_at_ms=performance.now();
        const age=clock.age(m.captured_ms,performance.now());
        if(age===null||age<=1000){images.forEach((image,i)=>ctx.drawImage(image,...m.parts[i].rect));record(m);}
        else{if(m.diagnostic)m.diagnostic.drop_reason='stale_jpeg';log.event('stale_frame_dropped',{seq:m.seq,age_ms:age},s.measurement);}
        ack(s,m);
      }finally{images.forEach(image=>image.close());}
    }
  }catch(error){fail(s,error.message);}finally{s.decoding=false;}
}
function open(reason='user_start'){
  if(!active||document.hidden)return;dispose(reason);const token=epoch;clock=new Clock();current=null;lastRegions={};lastShown=performance.now();frames=[];bytes=[];
  const config=settings(),measurement=log.begin(config,reason);
  const ws=new WebSocket(address('/stream')),s={ws,measurement,queue:[],queueBytes:0,metadata:[],codec:config.codec,regions:config.regions,lastMessage:performance.now(),openedAt:performance.now()};session=s;
  ws.binaryType='arraybuffer';video.hidden=s.codec!=='h264';canvas.hidden=s.codec==='h264';$('empty').hidden=false;$('status').textContent='连接中';clockStart(s,token);
  ws.onopen=()=>{if(token===epoch){log.event('socket_open',{},measurement);ws.send(JSON.stringify({type:'start',client_session_id:measurement.id,...config}));}};
  ws.onmessage=event=>{
    if(token!==epoch||s.closing)return;s.lastMessage=performance.now();
    try{
      if(typeof event.data==='string'){
        const ready=JSON.parse(event.data);
        if(ready.type==='reset'&&s.codec==='h264'){log.event('media_reset',{generation:ready.generation},measurement);createMedia(s,token,ready.mime);return;}
        if(ready.type==='ready'){
          Object.assign(measurement,{source:ready.source,implementation:ready.implementation??null,server_config:ready.config??null,
            server_session_id:ready.server_session_id??null,server_boot_id:ready.boot_id??null,
            server_regions:ready.regions??null,mime:ready.mime??null,delivery_policy:ready.delivery_policy??null,transport:s.codec==='h264'?'WebSocket/H264-fMP4/TCP':'WebSocket/JPEG/TCP'});
          log.event('server_ready',{},measurement);$('source').textContent=ready.source==='SYNTHETIC_TASK_FIXTURE'?'合成测试输入':'实时摄像头 · 只读';if(s.codec==='h264')createMedia(s,token,ready.mime);}return;
      }
      const item=parsePacket(event.data),received=performance.now();bytes.push({at:received,size:event.data.byteLength});
      item.meta.actual_crc32=crc32(item.data);
      if(item.meta.kind==='frame'){
        const m=item.meta;measurement.received_frames++;s.lastReceivedSeq=m.seq;
        m.diagnostic=log.entry('packets',measurement,{at:received,received_at_ms:received,seq:m.seq,id:m.id,
          bytes:event.data.byteLength,payload_bytes:item.data.byteLength,captured_ms:m.captured_ms,server_sent_ms:m.server_sent_ms??null,
          width:m.width,height:m.height,encode_ms:m.encode_ms,window:m.window??null,pacing_kbps:m.pacing_kbps??null,
          jpeg_parts:m.parts??null,part_bytes:m.lengths??null,declared_payload_bytes:m.payload_bytes??null,payload_crc32:m.payload_crc32??null,
          delivery_policy:m.delivery_policy??null,actual_crc32:m.actual_crc32,generation:m.generation??0,server_session_id:m.server_session_id??null});
      }
      if(s.codec==='h264'){const invalid=integrityError(item.meta,item.data,false,item.meta.actual_crc32);if(invalid){rejectEvidence(s,item.meta,item.data);log.event('integrity_rejected',{seq:item.meta.seq,reason:invalid},s.measurement);fail(s,'H.264 数据不完整，重新同步');return;}}
      s.queue.push(item);s.queueBytes+=item.data.byteLength;
      if(s.queue.length>12||s.queueBytes>1048576){fail(s,'接收积压，重新同步最新画面');return;}
      if(s.codec==='h264')pump(s,token);else jpegPump(s,token);
    }catch(error){reportFault(s,'parse_error');fail(s,error.message);}
  };
  ws.onclose=event=>{
    log.event('socket_close',{code:event.code,reason:event.reason,was_clean:event.wasClean},measurement);
    log.end(`socket_close_${event.code}`,measurement);
    reportFault(s,'socket_close',{close_code:event.code});
    if(token!==epoch)return;
    s.closing=true;s.queue=[];s.queueBytes=0;
    clearInterval(s.timer);clearInterval(s.clockTimer);s.clockWs?.close();$('status').textContent=active?'等待重连':'已暂停';
    if(event.code===1008){active=false;$('notice').textContent='参数或确认消息无效';return;}
    if(active&&!document.hidden){const delay=Math.min(2000,250*2**Math.min(failures++,3));log.event('reconnect_scheduled',{delay_ms:delay},measurement);retryTimer=setTimeout(()=>{if(token===epoch&&active){reconnects++;$('reconnects').textContent=reconnects;open('automatic_reconnect');}},delay);}
  };
  ws.onerror=()=>{log.event('socket_error',{},measurement);if(token===epoch)$('notice').textContent='连接失败或实验会话被占用';};
  s.timer=setInterval(()=>{
    if(token!==epoch)return;
    if(performance.now()-s.lastMessage>6500||performance.now()-lastShown>8000){fail(s,'未收到新画面，重新连接');return;}
    if(s.codec==='h264'&&video.buffered.length){
      const end=video.buffered.end(video.buffered.length-1),ahead=end-video.currentTime;
      if(ahead>.4){log.event('buffer_seek',{ahead_ms:ahead*1000},measurement);video.currentTime=Math.max(video.buffered.start(video.buffered.length-1),end-.10);}
      video.playbackRate=ahead>.20?1.05:1;
      metric('buffer',Math.max(0,ahead)*1000);
    }else metric('buffer',null);
  },100);
}
function start(reason='user_start'){for(const field of [$('kbps'),$('fps')])if(!field.reportValidity())return;active=true;open(reason);}
$('connect').onclick=()=>start();$('pause').onclick=stop;
$('edit').onchange=()=>overlay.classList.toggle('editing',$('edit').checked);
$('reset').onclick=()=>{regions=structuredClone(defaults);drawBoxes();if(active)start('regions_reset');};
let drag=null;
const point=event=>{const rect=overlay.getBoundingClientRect();return {x:Math.max(0,Math.min(.999,(event.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(.999,(event.clientY-rect.top)/rect.height))};};
overlay.onpointerdown=event=>{if(!$('edit').checked)return;drag=point(event);overlay.setPointerCapture(event.pointerId);};
overlay.onpointerup=event=>{
  if(!drag)return;const end=point(event),x=Math.min(drag.x,end.x),y=Math.min(drag.y,end.y),w=Math.abs(end.x-drag.x),h=Math.abs(end.y-drag.y);drag=null;
  const index=+$('selected').value,other=regions.filter((_,i)=>i!==index).reduce((sum,r)=>sum+r.w*r.h,0);
  if(w<.02||h<.02||w*h+other>.5){$('notice').textContent='区域面积超限或过小';return;}
  regions[index]={...regions[index],x,y,w,h};drawBoxes();if(active)start('regions_changed');
};
overlay.onpointercancel=()=>{drag=null;};
setInterval(()=>{
  const now=performance.now(),age=current?clock.age(current.captured_ms,now):null;metric('age',age);const stale=!!current&&(age===null||age>500);$('stale').hidden=!stale;$('stale').textContent=age===null?'时效未校准':'画面已过期';document.body.dataset.stale=String(stale);
  frames=frames.filter(t=>now-t<2000);bytes=bytes.filter(p=>now-p.at<2000);metric('rate',frames.length/2);metric('bandwidth',bytes.reduce((sum,p)=>sum+p.size,0)*8/2e6,3);
  const estimate=clock.estimate(now);$('sync').textContent=estimate?`校时 RTT ${estimate.rtt.toFixed(1)} ms · 对称路径假设`:'时钟未校准';
  $('region-age').textContent=regions.map(r=>{const value=clock.age(lastRegions[r.name],now);return `${r.name}: ${value===null?'--':Math.round(value)} ms`;}).join(' · ');
  log.entry('samples',session?.measurement,{at:now,interval_ms:lastSampleAt===null?null:now-lastSampleAt,
    age_ms:age,latency_ms:current?.latency??null,display_fps:frames.length/2,
    buffer_ms:session?.codec==='h264'&&video.buffered.length?Math.max(0,video.buffered.end(video.buffered.length-1)-video.currentTime)*1000:null,
    state:$('status').textContent,notice:$('notice').textContent,visibility:document.visibilityState,active,
    socket_state:session?.ws.readyState??null,displayed_seq:current?.seq??null,
    displayed_session_id:current?.diagnostic?.session_id??null,last_received_seq:session?.lastReceivedSeq??null,
    queue_frames:session?.queue.length??0,queue_bytes:session?.queueBytes??0,websocket_buffered_bytes:session?.ws.bufferedAmount??0,
    payload_mbps:bytes.reduce((sum,p)=>sum+p.size,0)*8/2e6,clock_rtt_ms:estimate?.rtt??null,clock_offset_ms:estimate?.offset??null,
    video_ready_state:video.readyState,video_paused:video.paused,playback_rate:video.playbackRate});lastSampleAt=now;
  if(session?.ws.readyState===1&&now-(session.lastReport||0)>1000){session.lastReport=now;session.ws.send(JSON.stringify({type:'display',age_ms:age,latency_ms:current?.latency??null,fps:frames.length/2,buffer_ms:samples.at(-1).buffer_ms}));}
},250);
window.__svtExport=()=>({...log.snapshot({controls:settings(),reconnects,state:$('status').textContent,environment:browserEnvironment()}),server_diagnostics:structuredClone(serverEvidence)});
$('download').onclick=async()=>{const button=$('download');button.disabled=true;try{await fetchEvidence();log.event('export');const data=window.__svtExport(),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=log.filename(data);link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}finally{button.disabled=false;}};
for(const id of ['test-network','test-route','test-note'])$(id).addEventListener('change',()=>log.setContext({phone_network:$('test-network').value,vpn_route:$('test-route').value,note:$('test-note').value.slice(0,200)}));
for(const id of ['codec','kbps','fps','semantic'])$(id).addEventListener('change',()=>log.event('controls_changed',{controls:settings()}));
for(const type of ['waiting','stalled','playing','error','seeking'])video.addEventListener(type,()=>log.event(`video_${type}`,{media_time:video.currentTime,error_code:video.error?.code??null}));
for(const type of ['online','offline'])window.addEventListener(type,()=>log.event(type));
document.addEventListener('visibilitychange',()=>{log.event('visibility_changed',{visibility:document.visibilityState});if(document.hidden){dispose('background');$('status').textContent='后台暂停';}else if(active)open('foreground');});
window.addEventListener('pagehide',()=>{log.event('pagehide');dispose('pagehide');});window.addEventListener('pageshow',event=>{if(event.persisted&&active)open('page_restore');});
window.__svtProbe=()=>({current,frames:history.slice(),samples:samples.slice(),reconnects,status:$('status').textContent,notice:$('notice').textContent,codec:session?.codec});
window.lucide?.createIcons();drawBoxes();start('initial');
