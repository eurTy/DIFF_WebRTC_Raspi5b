// Local synthetic images only; remote camera content is never saved.
const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path');
const base=process.argv[2]||'http://127.0.0.1:8091',out=process.argv[3]||'runtime/rtc-v06';
const percentile=(a,p)=>a.length?[...a].sort((x,y)=>x-y)[Math.floor((a.length-1)*p)]:null;
(async()=>{
  await fs.mkdir(out,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true}),results=[];
  try{
    for(const viewport of [{width:390,height:844},{width:1440,height:1000}]){
      const page=await browser.newPage({viewport}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      try{
        await page.goto(base);
        await page.waitForFunction(()=>window.__rtcProbe?.().frames.length>30,null,{timeout:30000});
        await page.waitForTimeout(12000);
        const raw=await page.evaluate(()=>window.__rtcProbe());
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
        const latencies=raw.frames.slice(25).map(f=>f.latency_ms).filter(Number.isFinite);
        const result={viewport,source:raw.source,errors,overflow,connection:raw.connection,
          frames:raw.frames.length,reconnects:raw.reconnects,latest:raw.latest,
          latency_p50_ms:percentile(latencies,.5),latency_p95_ms:percentile(latencies,.95),raw};
        if(raw.source==='SYNTHETIC_TASK_FIXTURE'){
          await page.screenshot({path:path.join(out,`rtc-${viewport.width}.png`),fullPage:true});
          result.pixelSpan=await page.evaluate(()=>{
            const c=document.createElement('canvas');c.width=64;c.height=36;
            const ctx=c.getContext('2d');ctx.drawImage(document.querySelector('video'),0,0,64,36);
            const a=ctx.getImageData(0,0,64,36).data;let min=255,max=0;
            for(let i=0;i<a.length;i+=4){min=Math.min(min,a[i]);max=Math.max(max,a[i]);}return max-min;
          });
        }
        await page.click('#pause');await page.waitForTimeout(1500);await page.click('#connect');
        await page.waitForFunction(()=>window.__rtcProbe().displayed>10,null,{timeout:20000});result.restart=true;
        results.push(result);
        if(errors.length||overflow||result.pixelSpan<20||raw.latest.width!==1280||raw.latest.height!==720||result.latency_p50_ms===null)process.exitCode=1;
        console.log(JSON.stringify({...result,raw:undefined}));
      }catch(e){results.push({viewport,error:e.message,errors,probe:await page.evaluate(()=>window.__rtcProbe?.()).catch(()=>null)});console.error(e);process.exitCode=1;}
      finally{await page.close();await new Promise(r=>setTimeout(r,2000));}
    }
  }finally{await browser.close();await fs.writeFile(path.join(out,'results.json'),JSON.stringify({scope:'Synthetic/desktop WebRTC validation; not a phone 4G benchmark',results},null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
