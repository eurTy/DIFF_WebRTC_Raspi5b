const {chromium}=require('playwright');
const fs=require('node:fs/promises'), path=require('node:path'), assert=require('node:assert/strict');
const url=process.argv[2]||'http://127.0.0.1:8088', output=process.argv[3]||'runtime/browser';
(async()=>{
  await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const checks=[],errors=[];
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(url);
    await page.waitForFunction(()=>Number(document.querySelector('#frames').textContent)>10&&document.querySelector('#latency').textContent!=='--',null,{timeout:20000});
    const pixels=()=>page.locator('#video').evaluate(canvas=>{const data=canvas.getContext('2d').getImageData(0,0,1280,720).data;let min=255,max=0,sum=0;for(let i=0;i<data.length;i+=16){min=Math.min(min,data[i]);max=Math.max(max,data[i]);sum+=data[i];}return {min,max,sum};});
    const first=await pixels();await page.waitForTimeout(500);const second=await pixels();assert.ok(first.max-first.min>30);assert.notEqual(first.sum,second.sum);checks.push({test:'moving_canvas',first,second});
    for(const [name,width,height] of [['desktop',1440,1050],['tablet',1024,900],['mobile',390,844],['small',320,780]]){
      await page.setViewportSize({width,height});await page.waitForTimeout(250);
      const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,clipped:[...document.querySelectorAll('strong,button,dd,h2')].filter(e=>e.getClientRects().length&&e.scrollWidth>e.clientWidth+2).map(e=>e.id||e.textContent)}));
      assert.equal(layout.overflow,false);assert.deepEqual(layout.clipped,[]);await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});checks.push({test:name,...layout});
    }
    await page.setViewportSize({width:1440,height:1050});
    await page.locator('#pause').click();const paused=await page.locator('#frames').textContent();await page.waitForTimeout(500);assert.equal(await page.locator('#frames').textContent(),paused);checks.push({test:'pause',pass:true});
    for(const mode of ['full','uniform','static-roi','semantic']){await page.selectOption('#mode',mode);await page.locator('#connect').click();await page.waitForFunction(()=>Number(document.querySelector('#frames').textContent)>2);checks.push({test:mode,pass:true});}
    await page.selectOption('#region-choice','1');await page.locator('#weight').fill('9');await page.locator('#weight').dispatchEvent('change');await page.locator('#connect').click();await page.waitForFunction(()=>Number(document.querySelector('#frames').textContent)>2);
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download').click()]);await download.saveAs(path.join(output,'export.json'));const exported=JSON.parse(await fs.readFile(path.join(output,'export.json'),'utf8'));assert.ok(exported.frames.length);checks.push({test:'reconfigure_export',pass:true});
    assert.deepEqual(errors,[]);await fs.writeFile(path.join(output,'checks.json'),JSON.stringify({url,checks,errors},null,2));console.log(JSON.stringify({checks:checks.length,errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
