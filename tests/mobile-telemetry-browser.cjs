// Local synthetic source only. Never save screenshots of the live camera.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path');
const base=process.argv[2]||'http://127.0.0.1:8091';
const url=new URL(base);
if(!['127.0.0.1','localhost'].includes(url.hostname))throw Error('Synthetic loopback only');
const out='output/playwright/telemetry';
(async()=>{
  await fs.mkdir(out,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const viewport of [{width:390,height:844},{width:1440,height:1000}]){
      const page=await browser.newPage({viewport,acceptDownloads:true}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      try{
        let mediaSocket;
        await page.routeWebSocket('**/stream',socket=>{mediaSocket=socket;socket.connectToServer();});
        await page.goto(base);
        await page.waitForFunction(()=>window.__svtProbe?.().frames.length>=12,null,{timeout:30000});
        assert.equal((await page.evaluate(()=>window.__svtProbe())).current.source,'SYNTHETIC_TASK_FIXTURE');
        await page.locator('.test-conditions summary').click();
        await page.selectOption('#test-network','4g');
        await page.selectOption('#test-route','unknown');
        await page.fill('#test-note','synthetic test; not a real 4G measurement');
        await page.locator('#test-note').blur();
        await page.selectOption('#codec','jpeg');
        const before=await page.evaluate(()=>window.__svtExport());
        assert.equal(before.controls_at_export.codec,'jpeg');
        assert.equal(before.sessions.find(s=>s.id===before.active_session_id).config.codec,'h264');
        assert(before.packets.some(p=>p.append_completed_at_ms>=p.received_at_ms));
        await page.click('#pause');
        await page.waitForTimeout(250);
        const paused=await page.evaluate(()=>window.__svtExport());
        assert.equal(paused.active_session_id,null);
        assert(paused.events.some(e=>e.type==='pause'));
        await page.click('#connect');
        await page.waitForFunction(()=>window.__svtExport().frames.filter(f=>f.codec==='jpeg').length>=8,null,{timeout:30000});
        const [download]=await Promise.all([page.waitForEvent('download'),page.click('#download')]);
        const file=path.join(out,`${url.port}-${viewport.width}.json`);
        await download.saveAs(file);
        const data=JSON.parse(await fs.readFile(file,'utf8'));
        assert.equal(data.schema,8);assert.equal(data.run.endpoint.port,Number(url.port));
        assert.equal(data.run.app_version,url.port==='8090'?'v0.5':'v0.7');
        assert.equal(data.conditions.phone_network,'4g');assert.equal(data.conditions.vpn_route,'unknown');
        assert(data.conditions.note.includes('not a real 4G'));
        assert.match(download.suggestedFilename(),new RegExp(`^svt-${url.port}-mixed-`));
        assert.deepEqual(data.codecs,['h264','jpeg']);
        for(const s of data.sessions){
          assert.equal(s.implementation.source_sha256.length,64);
          assert.equal(s.server_config.codec,s.config.codec);
          assert.equal(s.source,'SYNTHETIC_TASK_FIXTURE');
        }
        for(const f of data.frames){
          const s=data.sessions.find(s=>s.id===f.session_id);
          assert.equal(f.codec,s.config.codec);assert(f.height>0);
          assert(f.at>=f.received_at_ms);assert(f.server_sent_ms>=f.captured_ms);
        }
        assert(data.packets.some(p=>p.codec==='jpeg'&&p.decode_completed_at_ms>=p.received_at_ms));
        assert(data.clocks.some(c=>c.accepted));
        assert(data.samples.some(s=>s.clock_rtt_ms!==null));
        const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,
          baseline:document.querySelector('#baseline').href,query:location.search}));
        assert(!layout.overflow);assert(!new URL(layout.baseline).search);assert.equal(layout.query,'');
        const span=await page.evaluate(()=>{
          const c=document.querySelector('#canvas'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          let lo=255,hi=0;for(let i=0;i<d.length;i+=400){lo=Math.min(lo,d[i]);hi=Math.max(hi,d[i]);}return hi-lo;
        });
        assert(span>20);assert.deepEqual(errors,[]);
        await page.screenshot({path:path.join(out,`${url.port}-${viewport.width}.png`),fullPage:true});
        const sessionCount=data.sessions.length;
        await mediaSocket.close({code:1013,reason:'Synthetic reconnect test'});
        await page.waitForFunction(count=>window.__svtExport().sessions.length>count&&window.__svtProbe().status==='传输中',sessionCount,{timeout:15000});
        const recovered=await page.evaluate(()=>window.__svtExport());
        assert(recovered.events.some(e=>e.type==='socket_close'&&e.code===1013));
        assert(recovered.sessions.some(s=>s.start_reason==='automatic_reconnect'));
        assert(recovered.reconnects>=1);
        await page.evaluate(()=>{
          Object.defineProperty(document,'hidden',{configurable:true,value:true});
          Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
          document.dispatchEvent(new Event('visibilitychange'));
        });
        const hidden=await page.evaluate(()=>window.__svtExport());
        assert.equal(hidden.active_session_id,null);assert.equal(hidden.sessions.at(-1).end_reason,'background');
        await page.evaluate(()=>{
          delete document.hidden;delete document.visibilityState;
          document.dispatchEvent(new Event('visibilitychange'));
        });
        await page.waitForFunction(()=>window.__svtExport().sessions.at(-1).start_reason==='foreground'&&window.__svtProbe().status==='传输中',null,{timeout:15000});
        console.log(JSON.stringify({port:url.port,viewport,frames:data.frames.length,sessions:data.sessions.length,
          schema:data.schema,pixelSpan:span,overflow:layout.overflow,reconnect:true,simulatedVisibility:true,errors}));
        await page.click('#pause');
      }finally{await page.close();}
    }
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
