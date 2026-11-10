// Synthetic source only: delayed feedback, truncated JPEG, same-socket H.264 reset.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true}),results=[];
  try{
    for(const viewport of [{width:390,height:844},{width:1440,height:1000}]){
      const page=await browser.newPage({viewport}),errors=[];
      let mode='h264',held=false,holding=false,queue=[],corrupted=false;
      page.on('pageerror',error=>errors.push(error.message));
      await page.routeWebSocket('**/stream',socket=>{
        const upstream=socket.connectToServer();
        socket.onMessage(message=>{
          const m=typeof message==='string'?JSON.parse(message):{};
          if(m.type==='start')mode=m.codec;
          if(mode==='h264'&&m.type==='ack'){
            if(!held){held=true;holding=true;setTimeout(()=>{holding=false;for(const item of queue)upstream.send(item);queue=[];},1600);}
            if(holding){queue.push(message);return;}
          }
          upstream.send(message);
        });
        upstream.onMessage(message=>{
          if(Buffer.isBuffer(message)&&message.subarray(0,4).toString()==='SVP5'){
            const length=message.readUInt32BE(4),m=JSON.parse(message.subarray(8,8+length));
            if(m.parts&&m.seq===5&&!corrupted){corrupted=true;socket.send(message.subarray(0,message.length-71));return;}
          }
          socket.send(message);
        });
      });
      await page.goto('http://127.0.0.1:8091');
      await page.waitForFunction(()=>window.__svtExport?.().frames.filter(f=>f.generation>=1).length>=12,null,{timeout:30000});
      const h264=await page.evaluate(()=>window.__svtExport());
      assert.equal(h264.reconnects,0);assert.equal(h264.sessions.length,1);
      assert(h264.events.some(e=>e.type==='media_reset'));
      assert(h264.frames.every(f=>f.width===1280&&f.height===720));
      assert.equal((await page.evaluate(()=>window.__svtProbe())).current.source,'SYNTHETIC_TASK_FIXTURE');
      const videoSpan=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=1280;c.height=720;const x=c.getContext('2d');x.drawImage(document.querySelector('#video'),0,0);const a=x.getImageData(0,0,1280,720).data;let lo=255,hi=0;for(let i=0;i<a.length;i+=400){lo=Math.min(lo,a[i]);hi=Math.max(hi,a[i]);}return hi-lo;});
      assert(videoSpan>20);
      await fs.mkdir('output/playwright/recovery',{recursive:true});
      await page.screenshot({path:`output/playwright/recovery/h264-${viewport.width}.png`,fullPage:true});
      await page.click('#pause');await page.waitForTimeout(300);
      await page.selectOption('#codec','jpeg');await page.click('#connect');
      await page.waitForFunction(()=>window.__svtExport().frames.filter(f=>f.codec==='jpeg').length>=20,null,{timeout:30000});
      const downloadPromise=page.waitForEvent('download');await page.click('#download');
      const download=await downloadPromise,filename=`output/playwright/recovery/export-${viewport.width}.json`;
      await download.saveAs(filename);
      const data=JSON.parse(await fs.readFile(filename,'utf8'));
      assert.equal(data.reconnects,0);
      const fault=data.events.find(e=>e.type==='integrity_rejected');assert(fault);
      const s=data.sessions.find(s=>s.id===fault.session_id),trace=data.server_diagnostics;assert(trace);
      const sent=trace.events.find(e=>e.server_session_id===s.server_session_id&&e.event==='write_start'&&e.seq===5);
      const received=trace.events.find(e=>e.server_session_id===s.server_session_id&&e.event==='client_report'&&e.client_event==='integrity_rejected');
      assert(sent&&received);assert.equal(sent.expected_bytes-received.actual_bytes,71);
      assert.notEqual(sent.expected_crc32,received.actual_crc32);
      assert(data.frames.some(f=>f.codec==='jpeg'&&f.seq>5));
      const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,query:location.search}));
      assert(!layout.overflow);assert.equal(layout.query,'');assert.deepEqual(errors,[]);
      results.push({viewport,ackDelayMs:1600,h264SameSocketReset:true,h264Native720p:true,videoSpan,
        reconnected:data.reconnects,correlatedJpegShortfall:71,diagnosticEvents:trace.events.length,...layout,errors});
      await page.click('#pause');await page.waitForTimeout(350);await page.close();
    }
    await fs.writeFile('output/playwright/recovery/results.json',JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
