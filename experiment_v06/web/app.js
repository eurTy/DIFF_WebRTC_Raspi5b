import {Clock} from '/protocol.js';
const $=id=>document.getElementById(id), video=$('video');
const scheme=location.protocol==='https:'?'wss':'ws';
let pc=null,signal=null,clockSocket=null,clock=new Clock(),epoch=0,wanted=true,retry=null,timer=null,clockTimer=null,rvfc=null;
let mapping=new Map(),pendingClock=new Map(),clockId=0,current=null,lastRtp=null,lastPresentation=null;
let displayed=0,lastDisplayAt=0,sessionStarted=0,previous=null,frameSamples=[],samples=[],reconnects=0,source=null;
let latest={fps:0,mbps:0,jitter_buffer_ms:null,rtt_ms:null,width:0,height:0,lost:0,freeze_count:0},targetFps=20;
let stage='idle',route='',startupTimer=null,lastError='',candidateCounts={host:0,srflx:0,relay:0};
const number=(value,digits=1)=>Number.isFinite(value)?value.toFixed(digits):'--';
const valid=v=>typeof v==='number'&&Number.isFinite(v);
function diagnose(event,message='',extra={}){
  const detail={event,message:String(message).slice(0,240),stage,route,secure:window.isSecureContext,
    webrtc:typeof window.RTCPeerConnection,ice:pc?.iceGatheringState||'',
    ice_connection:pc?.iceConnectionState||'',connection:pc?.connectionState||'',...candidateCounts,...extra};
  fetch('/api/client',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(detail)}).catch(()=>{});
}
function setStage(value){
  stage=value;$('path').textContent=`${route||'auto'} · ${stage} · ICE ${pc?.iceGatheringState||'--'} / ${pc?.iceConnectionState||'--'}`;
  diagnose('stage');
}
function bounded(promise,ms,label){
  let timer;
  return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(`${label} 超过 ${ms/1000} 秒`)),ms);})])
    .finally(()=>clearTimeout(timer));
}

function recordMapping(meta){
  if(meta.type!=='frame'||!Number.isInteger(meta.rtp)||!valid(meta.captured_ms))return;
  mapping.set(meta.rtp,meta);
  while(mapping.size>160)mapping.delete(mapping.keys().next().value);
  if(meta.rtp===lastRtp)matchFrame();
}
function matchFrame(){
  const meta=mapping.get(lastRtp);
  if(!meta||!valid(lastPresentation))return;
  const latency=clock.age(meta.captured_ms,lastPresentation);
  current={...meta,latency_ms:latency};
  targetFps=meta.target_fps;
  frameSamples.push({session:epoch,at:lastPresentation,rtp:lastRtp,latency_ms:latency});
  if(frameSamples.length>1200)frameSamples.shift();
}
function onFrame(now,meta,generation){
  if(generation!==epoch)return;
  if(!displayed){clearTimeout(startupTimer);startupTimer=null;lastError='';$('notice').textContent='';setStage('first-frame');}
  displayed++;lastDisplayAt=performance.now();
  lastRtp=Number.isInteger(meta.rtpTimestamp)?meta.rtpTimestamp:null;
  lastPresentation=valid(meta.expectedDisplayTime)?meta.expectedDisplayTime:now;
  current=null;matchFrame();
  rvfc=video.requestVideoFrameCallback((n,m)=>onFrame(n,m,generation));
}
function stop(){
  epoch++;clearTimeout(retry);clearTimeout(startupTimer);startupTimer=null;clearInterval(timer);clearInterval(clockTimer);
  if(rvfc!==null&&video.cancelVideoFrameCallback)video.cancelVideoFrameCallback(rvfc);
  rvfc=null;
  if(signal){signal.onclose=null;signal.close();}signal=null;
  if(clockSocket)clockSocket.close();clockSocket=null;
  if(pc)pc.close();pc=null;
  video.srcObject=null;
  mapping.clear();pendingClock.clear();clock=new Clock();current=null;previous=null;
  lastRtp=null;lastPresentation=null;lastDisplayAt=0;displayed=0;
  latest={fps:0,mbps:0,jitter_buffer_ms:null,rtt_ms:null,width:0,height:0,lost:0,freeze_count:0};
  for(const id of ['age','latency','buffer','rtt'])$(id).textContent='--';
  $('fps').textContent='0.0';$('rate').textContent='0.000';
}
function fail(message,generation){
  if(generation!==epoch)return;
  lastError=message;diagnose('failure',message);
  stop();$('state').textContent='等待重连';$('notice').textContent=message;
  if(wanted&&!document.hidden){reconnects++;retry=setTimeout(start,Math.min(10000,1500*reconnects));}
}
function startClock(generation){
  clockSocket=new WebSocket(`${scheme}://${location.host}/clock`);
  clockSocket.onmessage=event=>{
    if(generation!==epoch)return;
    const message=JSON.parse(event.data),sent=pendingClock.get(message.id);
    if(sent!==undefined){clock.add(sent,message.received_ms,message.sent_ms,performance.now());pendingClock.delete(message.id);}
  };
  clockTimer=setInterval(()=>{
    if(clockSocket?.readyState!==WebSocket.OPEN)return;
    const id=++clockId;pendingClock.set(id,performance.now());
    while(pendingClock.size>20)pendingClock.delete(pendingClock.keys().next().value);
    clockSocket.send(JSON.stringify({type:'clock',id}));
  },500);
}
async function collect(generation){
  const connection=pc;if(!connection||generation!==epoch)return;
  try{
    const stats=await connection.getStats();if(generation!==epoch)return;
    let inbound=null,pair=null;
    stats.forEach(s=>{if(s.type==='inbound-rtp'&&s.kind==='video')inbound=s;if(s.type==='transport'&&s.selectedCandidatePairId)pair=stats.get(s.selectedCandidatePairId);});
    if(!pair)stats.forEach(s=>{if(s.type==='candidate-pair'&&s.nominated&&s.state==='succeeded')pair=s;});
    if(pair){latest.rtt_ms=valid(pair.currentRoundTripTime)?pair.currentRoundTripTime*1000:null;
      const local=stats.get(pair.localCandidateId),remote=stats.get(pair.remoteCandidateId);
      $('path').textContent=`ICE ${connection.iceConnectionState} · ${local?.protocol||'--'} · ${local?.candidateType||'--'} → ${remote?.candidateType||'--'}${local?.relayProtocol?` · TURN ${local.relayProtocol}`:''}`;}
    const now=performance.now();
    if(inbound){
      if(previous){
        const seconds=(now-previous.at)/1000;
        latest.fps=(displayed-previous.displayed)/seconds;
        latest.mbps=Math.max(0,inbound.bytesReceived-previous.bytes)*8/seconds/1e6;
        const emitted=inbound.jitterBufferEmittedCount-previous.emitted;
        latest.jitter_buffer_ms=emitted>0?(inbound.jitterBufferDelay-previous.delay)*1000/emitted:null;
      }
      latest.width=video.videoWidth;latest.height=video.videoHeight;
      latest.lost=Math.max(0,inbound.packetsLost||0);latest.freeze_count=inbound.freezeCount||0;
      previous={at:now,displayed,bytes:inbound.bytesReceived,emitted:inbound.jitterBufferEmittedCount,delay:inbound.jitterBufferDelay};
    }
    const age=current?clock.age(current.captured_ms,now):null;
    const report={type:'display',...latest,age_ms:age,latency_ms:current?.latency_ms??null};
    samples.push({...report,session:epoch,at:now});if(samples.length>1200)samples.shift();
    if(signal?.readyState===WebSocket.OPEN)signal.send(JSON.stringify(report));
    $('age').textContent=number(age);$('latency').textContent=number(report.latency_ms);
    $('fps').textContent=number(latest.fps);$('buffer').textContent=number(latest.jitter_buffer_ms);
    $('rtt').textContent=number(latest.rtt_ms);$('rate').textContent=number(latest.mbps,3);
    const sync=clock.estimate(now);
    $('timing').textContent=sync?`校时 RTT ${number(sync.rtt)} ms · 对称路径假设${lastRtp===null?' · 浏览器未提供逐帧 RTP 时间戳':''}`:'等待时钟校准';
    $('streamInfo').textContent=`H.264 · ${latest.width||1280} × ${latest.height||720} · 采样目标 ${targetFps} fps · 重连 ${reconnects}`;
    $('roiInfo').textContent=`重点区域：全画面同步刷新${age===null?'':` · 当前帧龄 ${number(age)} ms`}`;
    if(lastDisplayAt&&now-lastDisplayAt>5000)fail('媒体超过 5 秒未更新，重新协商',generation);
    else if(!lastDisplayAt&&video.requestVideoFrameCallback&&now-sessionStarted>20000)fail('尚未显示视频帧，重新协商',generation);
  }catch(error){if(generation===epoch)$('notice').textContent=error.message;}
}
async function start(){
  if(document.hidden||!wanted)return;
  stop();const generation=epoch;
  sessionStarted=performance.now();
  route=new URLSearchParams(location.search).get('route')||'auto';
  candidateCounts={host:0,srflx:0,relay:0};
  $('notice').textContent=lastError?`上次连接：${lastError}`:'';$('state').textContent='连接中';
  setStage('starting');
  startupTimer=setTimeout(()=>fail(`连接超时：${stage}；ICE ${pc?.iceGatheringState||'--'} / ${pc?.iceConnectionState||'--'}`,generation),25000);
  const kbps=Number($('kbps').value),fps=Number($('targetFps').value);targetFps=fps;
  if(!Number.isInteger(kbps)||kbps<1200||kbps>6000||!Number.isInteger(fps)||fps<10||fps>20){$('notice').textContent='参数范围：1200–6000 kbps，10–20 fps';return;}
  try{
    if(!window.RTCPeerConnection)throw Error('此浏览器不支持 WebRTC');
    setStage('ice-config');
    const response=await bounded(fetch('/api/ice',{cache:'no-store'}),8000,'ICE 配置');
    if(!response.ok)throw Error('ICE 配置读取失败');
    const config=await bounded(response.json(),3000,'ICE 配置解析');
    if(generation!==epoch)return;
    if(route==='tcp'||route==='udp')for(const server of config.iceServers)server.urls=server.urls.filter(url=>url.endsWith(`transport=${route}`));
    setStage('peer-creation');
    const connection=new RTCPeerConnection({iceServers:config.iceServers,bundlePolicy:'max-bundle',iceTransportPolicy:route==='auto'?'all':'relay'});pc=connection;
    const candidates=[];
    connection.onicecandidate=event=>{
      if(generation!==epoch)return;
      if(event.candidate){
        const kind=event.candidate.type||/\btyp (host|srflx|relay)\b/.exec(event.candidate.candidate)?.[1];
        if(kind in candidateCounts)candidateCounts[kind]++;
        if(kind==='relay')setStage('relay-candidate');
      }else{diagnose('ice-complete');}
      const message={type:'candidate',candidate:event.candidate?event.candidate.toJSON():null};
      if(signal?.readyState===WebSocket.OPEN)signal.send(JSON.stringify(message));else candidates.push(message);
    };
    connection.onicecandidateerror=event=>{
      if(generation===epoch)diagnose('ice-error','',{error_code:Number.isInteger(event.errorCode)?event.errorCode:0});
    };
    connection.onicegatheringstatechange=()=>{if(generation===epoch)diagnose('ice-gathering');};
    connection.oniceconnectionstatechange=()=>{if(generation===epoch)diagnose('ice-connection');};
    const transceiver=connection.addTransceiver('video',{direction:'recvonly'});
    const codecs=RTCRtpReceiver.getCapabilities?.('video')?.codecs;
    if(codecs&&transceiver.setCodecPreferences){const chosen=codecs.filter(c=>c.mimeType.toLowerCase()==='video/h264'||c.mimeType.toLowerCase()==='video/rtx');if(chosen.some(c=>c.mimeType.toLowerCase()==='video/h264'))transceiver.setCodecPreferences(chosen);}
    const channel=connection.createDataChannel('timing',{ordered:false,maxRetransmits:0});
    channel.onmessage=event=>{if(generation===epoch){try{recordMapping(JSON.parse(event.data));}catch{}}};
    connection.ontrack=event=>{
      if(generation!==epoch)return;
      setStage('remote-track');
      try{if('jitterBufferTarget' in event.receiver)event.receiver.jitterBufferTarget=0;}catch{}
      try{if('playoutDelayHint' in event.receiver)event.receiver.playoutDelayHint=0;}catch{}
      video.srcObject=event.streams[0]||new MediaStream([event.track]);
      video.play().catch(()=>{$('notice').textContent='点击播放启动视频';});
      if(video.requestVideoFrameCallback)rvfc=video.requestVideoFrameCallback((n,m)=>onFrame(n,m,generation));
    };
    connection.onconnectionstatechange=()=>{
      if(generation!==epoch)return;
      $('state').textContent=connection.connectionState==='connected'?'传输中':connection.connectionState;
      diagnose('connection');
      if(connection.connectionState==='failed')fail(`${route} 媒体连接失败，等待重试`,generation);
    };
    setStage('create-offer');
    const offer=await bounded(connection.createOffer(),8000,'创建 offer');
    if(generation!==epoch)return;
    setStage('local-description');
    await bounded(connection.setLocalDescription(offer),12000,'设置本地描述');
    if(generation!==epoch)return;
    setStage('signal-opening');
    startClock(generation);
    signal=new WebSocket(`${scheme}://${location.host}/signal`);
    signal.onopen=()=>{if(generation===epoch){
      setStage('offer-sent');
      signal.send(JSON.stringify({type:'offer',sdp:connection.localDescription.sdp,kbps,fps}));
      for(const candidate of candidates)signal.send(JSON.stringify(candidate));candidates.length=0;
    }};
    signal.onmessage=async event=>{
      if(generation!==epoch)return;
      try{const data=JSON.parse(event.data);if(data.type!=='answer')return;
        setStage('answer-received');
        source=data.source;$('source').textContent=source==='SYNTHETIC_TASK_FIXTURE'?'合成测试源 · 只读':'实时摄像头 · 只读';
        await bounded(connection.setRemoteDescription({type:'answer',sdp:data.sdp}),8000,'设置远端描述');
        if(generation===epoch){setStage('remote-description');timer=setInterval(()=>collect(generation),1000);}
      }catch(error){fail(error.message,generation);}
    };
    signal.onerror=()=>{if(generation===epoch)diagnose('signal-error');};
    signal.onclose=event=>fail(`信令断开 ${event.code}：${event.reason||stage}`,generation);
  }catch(error){fail(error.message,generation);}
}
$('connect').onclick=()=>{wanted=true;reconnects=0;start();};
$('pause').onclick=()=>{wanted=false;stop();$('state').textContent='已暂停';$('fps').textContent='0.0';};
$('showRegions').onchange=()=>$('regions').hidden=!$('showRegions').checked;
$('download').onclick=()=>{const blob=new Blob([JSON.stringify(window.__rtcProbe(),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='rtc-v06-metrics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
window.__rtcProbe=()=>({source,reconnects,stage,route,candidateCounts,connection:pc?.connectionState,ice:pc?.iceConnectionState,current,latest,samples,frames:frameSamples,displayed,scope:'Software publish-to-presentation estimate, not optical end-to-end latency'});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else if(wanted)start();});
window.addEventListener('pagehide',stop);window.addEventListener('pageshow',e=>{if(e.persisted&&wanted)start();});
window.addEventListener('load',()=>window.lucide?.createIcons());
start();
