const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path');
const [url='http://192.168.3.19:8088',out='runtime/soak.json',seconds='90',feedback='single',outage='0',fps='10',kbps='1200']=process.argv.slice(2);
const percentile=(a,q)=>a.length?[...a].sort((x,y)=>x-y)[Math.min(a.length-1,Math.floor((a.length-1)*q))]:null;
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
  const page=await context.newPage(),errors=[],samples=[],events=[];
  let blocked=false;
  if(Number(outage)>0)await page.routeWebSocket('**/stream',route=>{
    if(blocked){route.close({code:1013,reason:'Test stream outage'});return;}
    const server=route.connectToServer();
    server.onMessage(message=>{if(!blocked)route.send(message);});
  });
  page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto(url);await page.waitForFunction(()=>+document.querySelector('#frames').textContent>2);
    await page.selectOption('#feedback',feedback);await page.locator('#fps').fill(fps);await page.locator('#kbps').fill(kbps);await page.locator('#connect').click();
    await page.waitForFunction(()=>+document.querySelector('#frames').textContent>2);
    // One browser clock schedules both samples and outage; gaps remain in the denominator.
    const start=Date.now();let offline=false,injected=false;
    while(Date.now()-start<Number(seconds)*1000){
      const elapsed=(Date.now()-start)/1000;
      if(Number(outage)>0&&!injected&&elapsed>=15){blocked=true;offline=true;injected=true;events.push({type:'stream_blackhole',elapsed_s:elapsed});}
      if(offline&&elapsed>=15+Number(outage)){blocked=false;offline=false;events.push({type:'stream_restored',elapsed_s:elapsed});}
      samples.push({elapsed_s:elapsed,...await page.evaluate(()=>{const value=id=>{const s=document.getElementById(id)?.textContent;return s&&s!=='--'?Number(s):null;};return {frames:value('frames'),age_ms:value('age'),latency_ms:value('latency'),budget_kb:value('budget'),fps:value('rate'),status:document.getElementById('status').textContent,stale:document.body.dataset.stale};})});
      await page.waitForTimeout(100);
    }
    blocked=false;
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download').click()]);
    const exportPath=await download.path();const data=JSON.parse(await fs.readFile(exportPath,'utf8'));
    const arrivals=data.frames.filter(r=>Number.isFinite(r.latency_ms)).map(r=>r.latency_ms);
    const valid=samples.map(s=>s.age_ms).filter(Number.isFinite);
    const result={url,feedback,fps:Number(fps),kbps:Number(kbps),duration_s:Number(seconds),outage_s:Number(outage),measurement:'software publication to browser draw; not optical; live scene not controlled',errors,events,
      summary:{received:data.frames.length,displayed:data.frames.filter(r=>!r.dropped).length,p50_ms:percentile(arrivals,.5),p95_ms:percentile(arrivals,.95),max_ms:percentile(arrivals,1),age_p95_ms:percentile(valid,.95),fresh_250_fraction:samples.filter(s=>s.age_ms!==null&&s.age_ms<=250&&s.stale!=='true').length/samples.length,unknown_age_fraction:1-valid.length/samples.length,min_budget_kb:Math.min(...samples.map(s=>s.budget_kb).filter(Number.isFinite)),final_status:samples.at(-1).status},samples,export:data};
    await fs.mkdir(path.dirname(out),{recursive:true});await fs.writeFile(out,JSON.stringify(result,null,2));console.log(JSON.stringify(result.summary));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
