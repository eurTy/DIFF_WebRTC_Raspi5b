const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path');
const [url='http://192.168.3.19:8088',out='runtime/endurance.json',seconds='1800',monitor='http://192.168.3.19:8080/observe/status']=process.argv.slice(2);
const quantile=(a,q)=>a.length?[...a].sort((x,y)=>x-y)[Math.floor((a.length-1)*q)]:null;
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});
  const records=new Map(),errors=[],samples=[],health=[],segments=[];let data,start,lastExport=0,lastHealth=0;
  const exportNow=async()=>{
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download').click()]);
    data=JSON.parse(await fs.readFile(await download.path(),'utf8'));
    for(const record of data.frames)records.set(record.time,record);
    segments.push({at_s:(Date.now()-start)/1000,displayed:data.displayed,discarded:data.discarded,retained:data.frames.length,collected:records.size});
    await download.delete();lastExport=Date.now();
  };
  const save=async complete=>{
    const frames=[...records.values()].filter(r=>r.time>=samples[0]?.browser_time),latencies=frames.map(r=>r.latency_ms).filter(Number.isFinite);
    const durations=samples.slice(1).map((s,i)=>s.elapsed_s-samples[i].elapsed_s);
    const duration=durations.reduce((a,b)=>a+b,0);
    const freshSeconds=durations.reduce((n,d,i)=>n+(samples[i].age_ms!==null&&samples[i].age_ms+d*1000<=250?d:0),0);
    const summary={observed_s:duration,displayed:data?.displayed,discarded:data?.discarded,reconnects:data?.reconnects,
      collected:records.size,export_complete:records.size===data?.displayed+data?.discarded,
      observed_fps:duration?(samples.at(-1).frames-samples[0].frames)/duration:null,
      p50_ms:quantile(latencies,.5),p95_ms:quantile(latencies,.95),max_ms:quantile(latencies,1),
      age_p95_ms:quantile(samples.map(s=>s.age_ms).filter(Number.isFinite),.95),
      fresh_250_fraction:samples.filter(s=>s.age_ms!==null&&s.age_ms<=250).length/samples.length,
      conservative_fresh_interval_fraction:duration?freshSeconds/duration:null,
      max_sample_gap_s:Math.max(0,...durations),unknown_age_samples:samples.filter(s=>s.age_ms===null).length,
      max_js_heap_bytes:Math.max(0,...samples.map(s=>s.heap_bytes||0)),health_errors:health.filter(s=>s.error).length};
    const result={complete,url,requested_duration_s:+seconds,measurement:'software publication to browser canvas; sample gaps retained; not optical; ordinary workstation load',started_at:new Date(start).toISOString(),errors,summary,segments,samples,health,export:{...data,frames:[...records.values()]}};
    await fs.mkdir(path.dirname(out),{recursive:true});await fs.writeFile(out,JSON.stringify(result,null,2));return summary;
  };
  page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto(url);await page.waitForFunction(()=>+document.querySelector('#frames').textContent>5,null,{timeout:25000});start=Date.now();lastExport=lastHealth=start;
    while(Date.now()-start<+seconds*1000){
      const sample=await page.evaluate(()=>{const n=id=>{const v=document.getElementById(id).textContent;return v==='--'?null:Number(v);};return {browser_time:performance.now(),frames:n('frames'),age_ms:n('age'),latency_ms:n('latency'),budget_kb:n('budget'),heap_bytes:performance.memory?.usedJSHeapSize,status:document.getElementById('status').textContent};});
      samples.push({elapsed_s:(Date.now()-start)/1000,...sample});
      if(Date.now()-lastExport>=60000){await exportNow();console.log(JSON.stringify(await save(false)));}
      if(monitor&&Date.now()-lastHealth>=15000){lastHealth=Date.now();try{const r=await page.request.get(monitor,{timeout:2000});health.push({elapsed_s:(Date.now()-start)/1000,...await r.json()});}catch(e){health.push({elapsed_s:(Date.now()-start)/1000,error:e.message});}}
      await page.waitForTimeout(100);
    }
    await exportNow();console.log(JSON.stringify(await save(true)));
    if(errors.length||records.size!==data.displayed+data.discarded)throw Error('Incomplete export or browser errors');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
