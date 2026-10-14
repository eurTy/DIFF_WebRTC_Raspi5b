// Observational comparison of preserved native-JPEG paths. No legacy file changes.
const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path');
const [base='http://192.168.3.19:8080',out='runtime/transport-comparison.json',seconds='60']=process.argv.slice(2);
const quantile=(a,q)=>a.length?[...a].sort((x,y)=>x-y)[Math.floor((a.length-1)*q)]:null;
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true}),cells=[];
  try{
    for(const transport of ['ws','webrtc','webrtc','ws']){
      const page=await browser.newPage(),errors=[],startedAt=new Date().toISOString();page.on('pageerror',e=>errors.push(e.message));
      try{
        await page.goto(`${base}/?transport=${transport}&flow=decode&clock=separate`);
        await page.waitForFunction(()=>typeof latency!=='undefined'&&latency.snapshot().state==='ready'&&frameCount>30,null,{timeout:45000});
        await page.waitForTimeout(3000);
        await page.evaluate(()=>{
          const probe=window.__transportProbe={frames:[],ages:[],start:performance.now(),bytes:byteCount,count:frameCount};
          const original=latency.noteDecoded.bind(latency);
          latency.noteDecoded=(id,at)=>{
            original(id,at);const snapshot=latency.snapshot(),frame=latency.frames.get(id);
            probe.frames.push({at:performance.now(),id,latency_ms:snapshot.delayMs,clock_uncertainty_ms:snapshot.uncertaintyMs,published:frame.published});
          };
          probe.timer=setInterval(()=>probe.ages.push({at:performance.now(),...latency.snapshot()}),100);
        });
        await page.waitForTimeout(Number(seconds)*1000);
        const raw=await page.evaluate(async()=>{
          const probe=window.__transportProbe;clearInterval(probe.timer);
          const channels=[controlChannel,videoChannel].filter(Boolean).map(c=>({label:c.label,ordered:c.ordered,maxPacketLifeTime:c.maxPacketLifeTime,maxRetransmits:c.maxRetransmits}));
          const keys=['id','type','candidateType','protocol','relayProtocol','state','nominated','writable','currentRoundTripTime','totalRoundTripTime','bytesReceived','bytesSent','packetsReceived','packetsSent','messagesReceived','messagesSent','label','dataChannelIdentifier','localCandidateId','remoteCandidateId'];
          const rtcStats=pc?[...await pc.getStats()].map(([,s])=>s).filter(s=>['candidate-pair','local-candidate','remote-candidate','data-channel'].includes(s.type)).map(s=>Object.fromEntries(keys.filter(k=>k in s).map(k=>[k,s[k]]))):[];
          return {...probe,timer:undefined,end:performance.now(),byte_count:byteCount-probe.bytes,frame_count:frameCount-probe.count,width:videoEl.naturalWidth,height:videoEl.naturalHeight,channels,rtcStats,status:statusEl.textContent};
        });
        const duration=(raw.end-raw.start)/1000,latencies=raw.frames.map(f=>f.latency_ms).filter(Number.isFinite);
        const result={transport,started_at:startedAt,duration_s:duration,fps:raw.frame_count/duration,payload_mbps:raw.byte_count*8/duration/1e6,p50_ms:quantile(latencies,.5),p95_ms:quantile(latencies,.95),max_ms:quantile(latencies,1),age_p95_ms:quantile(raw.ages.map(a=>a.ageMs).filter(Number.isFinite),.95),fresh_250_fraction:raw.ages.filter(a=>a.ageMs!==null&&a.ageMs<=250).length/raw.ages.length,width:raw.width,height:raw.height,errors,raw};
        cells.push(result);console.log(JSON.stringify({...result,raw:undefined}));
      }catch(e){cells.push({transport,started_at:startedAt,error:e.message,errors});console.log(JSON.stringify(cells.at(-1)));}
      finally{await page.close();}
      await fs.mkdir(path.dirname(out),{recursive:true});await fs.writeFile(out,JSON.stringify({complete:cells.length===4,scope:'Preserved native 720p camera JPEG; sequential live scenes and Wi-Fi uncontrolled; sender pacing differs; NOT an isolated protocol or equal-bitrate causal comparison; software publication-to-img.decode endpoint',cells},null,2));
      await new Promise(resolve=>setTimeout(resolve,2000));
    }
    if(cells.some(c=>c.error||c.errors.length))process.exitCode=1;
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
