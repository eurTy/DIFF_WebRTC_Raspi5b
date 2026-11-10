// Synthetic loopback only; corruption and delayed ACKs never touch live input.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const base='http://127.0.0.1:8091';
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const results=[];
  try{
    for(const viewport of [{width:390,height:844},{width:1440,height:1000}]){
      const page=await browser.newPage({viewport}),errors=[],mutated=[];
      let slow=false;
      page.on('pageerror',e=>errors.push(e.message));
      await page.routeWebSocket('**/stream',socket=>{
        const upstream=socket.connectToServer();
        socket.onMessage(message=>{
          const value=typeof message==='string'?JSON.parse(message):{};
          if(slow&&value.type==='ack')setTimeout(()=>{try{upstream.send(message);}catch{}},150);
          else upstream.send(message);
        });
        upstream.onMessage(message=>{
          if(Buffer.isBuffer(message)&&message.subarray(0,4).toString()==='SVP5'){
            const size=message.readUInt32BE(4),meta=JSON.parse(message.subarray(8,8+size));
            if(meta.parts&&[5,10,15].includes(meta.seq)&&!mutated.includes(meta.seq)){
              mutated.push(meta.seq);
              if(meta.seq===5){socket.send(message.subarray(0,message.length-71));return;}
              if(meta.seq===10){const value=Buffer.from(message);value[8+size+8]^=1;socket.send(value);return;}
              meta.lengths[0]++;
              const header=Buffer.from(JSON.stringify(meta)),prefix=Buffer.alloc(8);
              prefix.write('SVP5');prefix.writeUInt32BE(header.length,4);
              socket.send(Buffer.concat([prefix,header,message.subarray(8+size)]));return;
            }
          }
          socket.send(message);
        });
      });
      try{
        await page.goto(base);
        await page.waitForFunction(()=>window.__svtProbe?.().frames.length>=12,null,{timeout:30000});
        assert.equal((await page.evaluate(()=>window.__svtProbe())).current.source,'SYNTHETIC_TASK_FIXTURE');
        const h264=await page.evaluate(()=>window.__svtExport());
        assert(h264.frames.every(f=>f.width===1280&&f.height===720));
        await page.click('#pause');await page.waitForTimeout(250);
        await page.selectOption('#codec','jpeg');slow=true;await page.click('#connect');
        await page.waitForFunction(()=>window.__svtExport().frames.filter(f=>f.codec==='jpeg').length>=30,null,{timeout:30000});
        const data=await page.evaluate(()=>window.__svtExport());
        const rejected=data.events.filter(e=>e.type==='integrity_rejected');
        assert.deepEqual(mutated,[5,10,15]);
        assert.deepEqual(rejected.map(e=>e.reason),['payload_length','payload_checksum','jpeg_lengths']);
        assert(!data.events.some(e=>e.type==='resync'));
        assert.equal(data.reconnects,0);
        assert(!data.frames.some(f=>f.codec==='jpeg'&&mutated.includes(f.seq)));
        assert(data.frames.some(f=>f.codec==='jpeg'&&f.seq>20));
        const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,query:location.search}));
        assert(!layout.overflow);assert.equal(layout.query,'');assert.deepEqual(errors,[]);
        const state=await (await page.request.get(base+'/api/status')).json();
        assert(state.events.max_in_flight<=4);
        assert(state.events.max_in_flight_bytes<=state.delivery.byte_limit);
        assert(state.events.integrity_rejected>=3);
        const pixelSpan=await page.evaluate(()=>{
          const d=document.querySelector('#canvas').getContext('2d').getImageData(0,0,1280,720).data;
          let lo=255,hi=0;for(let i=0;i<d.length;i+=400){lo=Math.min(lo,d[i]);hi=Math.max(hi,d[i]);}return hi-lo;
        });
        assert(pixelSpan>20);
        await fs.mkdir('output/playwright/delivery',{recursive:true});
        await page.screenshot({path:`output/playwright/delivery/${viewport.width}.png`,fullPage:true});
        results.push({viewport,corruptFramesRejected:rejected.length,reconnects:data.reconnects,
          h264Resolution:[h264.frames[0].width,h264.frames[0].height],jpegPresented:data.frames.filter(f=>f.codec==='jpeg').length,
          maxInFlight:state.events.max_in_flight,maxInFlightBytes:state.events.max_in_flight_bytes,
          byteLimit:state.delivery.byte_limit,delayedAckMs:150,pixelSpan,...layout,errors});
        await page.click('#pause');await page.waitForTimeout(300);
      }finally{await page.close();}
    }
    await fs.writeFile('output/playwright/delivery/results.json',JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
