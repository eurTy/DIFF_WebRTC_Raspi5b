// Synthetic fixture only; never save frames from the live Raspberry Pi.
const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path');
const base=process.argv[2]||'http://127.0.0.1:8091',out=process.argv[3]||'runtime/ws-jpeg-v07';
const percentile=(items,p)=>items.length?[...items].sort((a,b)=>a-b)[Math.floor((items.length-1)*p)]:null;
(async()=>{
  await fs.mkdir(out,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const viewport of [{width:390,height:844},{width:1440,height:1000}]){
      const page=await browser.newPage({viewport}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      try{
        await page.goto(base);
        await page.waitForFunction(()=>window.__jpegProbe?.().frames.length>12,null,{timeout:30000});
        await page.waitForTimeout(5000);
        const probe=await page.evaluate(()=>window.__jpegProbe());
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
        const pixelSpan=await page.evaluate(()=>{
          const c=document.createElement('canvas');c.width=64;c.height=36;
          const x=c.getContext('2d');x.drawImage(document.querySelector('#image'),0,0,64,36);
          const data=x.getImageData(0,0,64,36).data;let low=255,high=0;
          for(let i=0;i<data.length;i+=4){low=Math.min(low,data[i]);high=Math.max(high,data[i]);}
          return high-low;
        });
        const latencies=probe.frames.map(frame=>frame.latency_ms).filter(Number.isFinite);
        if(probe.source==='SYNTHETIC_TASK_FIXTURE')await page.screenshot({path:path.join(out,`jpeg-${viewport.width}.png`),fullPage:true});
        await page.click('#pause');await page.waitForTimeout(500);await page.click('#connect');
        await page.waitForFunction(()=>window.__jpegProbe().status==='传输中',null,{timeout:15000});
        const result={viewport,source:probe.source,status:probe.status,frames:probe.frames.length,
          fps:probe.samples.at(-1)?.display_fps,latency_p50_ms:percentile(latencies,.5),
          latency_p95_ms:percentile(latencies,.95),overflow,pixelSpan,errors,restart:true};
        console.log(JSON.stringify(result));
        if(errors.length||overflow||pixelSpan<20||probe.status!=='传输中'||probe.frames.length<12)process.exitCode=1;
      }catch(error){console.error(error);process.exitCode=1;}
      finally{await page.close();}
    }
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
