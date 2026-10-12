const {chromium}=require('playwright');
const fs=require('node:fs/promises'), path=require('node:path'), assert=require('node:assert/strict');
const url=process.argv[2]||'http://127.0.0.1:8088', output=process.argv[3]||'runtime/browser';
(async()=>{
  await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const checks=[],errors=[],ready=[];
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});
    page.on('pageerror',error=>errors.push(error.message));
    page.on('websocket',socket=>socket.on('framereceived',({payload})=>{if(typeof payload==='string'){try{const message=JSON.parse(payload);if(message.type==='ready')ready.push(message);}catch{}}}));
    await page.goto(url);
    await page.waitForFunction(()=>Number(document.querySelector('#frames').textContent)>10&&document.querySelector('#latency').textContent!=='--',null,{timeout:20000});
    const pixels=()=>page.locator('#video').evaluate(canvas=>{const data=canvas.getContext('2d').getImageData(0,0,1280,720).data;let min=255,max=0,sum=0;for(let i=0;i<data.length;i+=16){min=Math.min(min,data[i]);max=Math.max(max,data[i]);sum+=data[i];}return {min,max,sum};});
    const first=await pixels();await page.waitForFunction(previous=>{const data=document.querySelector('#video').getContext('2d').getImageData(0,0,1280,720).data;let sum=0;for(let i=0;i<data.length;i+=16)sum+=data[i];return sum!==previous;},first.sum,{timeout:10000});const second=await pixels();assert.ok(first.max-first.min>30);assert.notEqual(first.sum,second.sum);checks.push({test:'moving_canvas',first,second});
    for(const [name,width,height] of [['desktop',1440,1050],['tablet',1024,900],['mobile',390,844],['small',320,780]]){
      await page.setViewportSize({width,height});await page.waitForTimeout(250);
      const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,clipped:[...document.querySelectorAll('strong,button,dd,h2')].filter(e=>e.getClientRects().length&&e.scrollWidth>e.clientWidth+2).map(e=>e.id||e.textContent)}));
      assert.equal(layout.overflow,false);assert.deepEqual(layout.clipped,[]);await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});checks.push({test:name,...layout});
    }
    await page.setViewportSize({width:1440,height:1050});
    await page.locator('#pause').click();const paused=await page.locator('#frames').textContent();await page.waitForTimeout(500);assert.equal(await page.locator('#frames').textContent(),paused);checks.push({test:'pause',pass:true});
    for(const mode of ['full','uniform','static-roi','semantic']){await page.selectOption('#mode',mode);await page.locator('#connect').click();await page.waitForFunction(()=>Number(document.querySelector('#frames').textContent)>2);checks.push({test:mode,pass:true});}
    for(const feedback of ['single','median','realtime']){await page.selectOption('#feedback',feedback);await page.locator('#connect').click();await page.waitForFunction(()=>Number(document.querySelector('#frames').textContent)>2);assert.equal(ready.at(-1).feedback,feedback);checks.push({test:'feedback_'+feedback,pass:true});}
    await page.locator('#roi-x').fill('0.9');await page.locator('#roi-x').dispatchEvent('change');const readyCount=ready.length;await page.locator('#connect').click();await page.waitForTimeout(100);assert.equal(ready.length,readyCount);assert.ok((await page.locator('#notice').textContent()).includes('50%'));await page.locator('#reset').click();checks.push({test:'invalid_region_rejected',pass:true});
    const bounds=await page.locator('#regions').boundingBox();await page.mouse.move(bounds.x+bounds.width*.1,bounds.y+bounds.height*.1);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width*.3,bounds.y+bounds.height*.3);await page.mouse.up();assert.ok(Math.abs(Number(await page.locator('#roi-w').inputValue())-.2)<.003);assert.ok(Math.abs(Number(await page.locator('#roi-h').inputValue())-.2)<.003);await page.locator('#reset').click();checks.push({test:'region_drag',pass:true});
    await page.selectOption('#region-choice','1');await page.locator('#weight').fill('9');await page.locator('#weight').dispatchEvent('change');await page.locator('#connect').click();await page.waitForFunction(()=>Number(document.querySelector('#frames').textContent)>2);
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download').click()]);await download.saveAs(path.join(output,'export.json'));const exported=JSON.parse(await fs.readFile(path.join(output,'export.json'),'utf8'));assert.ok(exported.frames.length);checks.push({test:'reconfigure_export',pass:true});
    assert.deepEqual(errors,[]);await fs.writeFile(path.join(output,'checks.json'),JSON.stringify({url,checks,errors},null,2));console.log(JSON.stringify({checks:checks.length,errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
