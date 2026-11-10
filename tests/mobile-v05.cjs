// Synthetic local screenshots only. Remote runs do not retain camera images.
const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path');
const base=process.argv[2]||'http://127.0.0.1:8090',out=process.argv[3]||'runtime/mobile-v05';
const quantile=(a,q)=>a.length?[...a].sort((a,b)=>a-b)[Math.floor((a.length-1)*q)]:null;
(async()=>{
  await fs.mkdir(out,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true}),results=[];
  try{
    for(const viewport of [{width:390,height:844},{width:1440,height:1000}]){
      for(const codec of ['h264','jpeg']){
        const page=await browser.newPage({viewport}),errors=[];page.on('pageerror',e=>errors.push(e.message));
        try{
          await page.goto(`${base}/?codec=${codec}`);
          await page.waitForFunction(()=>window.__svtProbe?.().frames.length>20,null,{timeout:25000});
          await page.waitForTimeout(12000);
          const raw=await page.evaluate(()=>window.__svtProbe());
          const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
          const ages=raw.samples.slice(12).map(s=>s.age_ms).filter(Number.isFinite);
          const latencies=raw.frames.slice(20).map(s=>s.latency_ms).filter(Number.isFinite);
          const [first,last]=[raw.frames[20],raw.frames.at(-1)];
          const result={codec,viewport,errors,overflow,frames:raw.frames.length,
            fps:(raw.frames.length-21)*1000/(last.at-first.at),
            age_p50_ms:quantile(ages,.5),age_p95_ms:quantile(ages,.95),
            latency_p50_ms:quantile(latencies,.5),latency_p95_ms:quantile(latencies,.95),
            reconnects:raw.reconnects,source:raw.current?.source,raw};
          if(new URL(base).hostname==='127.0.0.1'){
            await page.screenshot({path:path.join(out,`${codec}-${viewport.width}.png`),fullPage:true});
            result.pixelSpan=await page.evaluate(()=>{const source=document.querySelector('#codec').value==='h264'?document.querySelector('video'):document.querySelector('#canvas');const c=document.createElement('canvas');c.width=64;c.height=36;const ctx=c.getContext('2d');ctx.drawImage(source,0,0,64,36);const a=ctx.getImageData(0,0,64,36).data;let min=255,max=0;for(let i=0;i<a.length;i+=4){min=Math.min(min,a[i]);max=Math.max(max,a[i]);}return max-min;});
          }
          await page.click('#pause');await page.waitForTimeout(300);await page.click('#connect');
          const count=raw.frames.length;
          await page.waitForFunction(n=>window.__svtProbe().frames.length>n+8,count,{timeout:15000});
          result.restart=true;results.push(result);
          if(overflow||errors.length||result.pixelSpan<20)process.exitCode=1;
          console.log(JSON.stringify({...result,raw:undefined}));
        }catch(e){results.push({codec,viewport,error:e.message,errors});console.error(e);process.exitCode=1;}
        finally{await page.close();await new Promise(r=>setTimeout(r,1000));}
      }
    }
  }finally{await browser.close();await fs.writeFile(path.join(out,'results.json'),JSON.stringify({scope:'Browser presentation estimates; not optical or a cellular benchmark',results},null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
