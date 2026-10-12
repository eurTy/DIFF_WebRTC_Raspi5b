const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const url=process.argv[2]||'http://127.0.0.1:18088',output=process.argv[3]||'runtime/recovery.json';
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext(),page=await context.newPage();
  const errors=[],ready=[],checks=[];let block=false,suppress=false,delayed=false;
  page.on('pageerror',e=>errors.push(e.message));
  // Faults apply only to this test viewer; neither Pi networking nor other viewers change.
  await page.routeWebSocket('**/stream',route=>{
    if(block){route.close({code:1013,reason:'Test outage'});return;}
    const server=route.connectToServer();let deliverAt=0;
    server.onMessage(message=>{
      if(suppress)return;
      if(typeof message==='string'){
        try{const m=JSON.parse(message);if(m.type==='ready')ready.push(m);}catch{}
        route.send(message);
      }else{deliverAt=Math.max(deliverAt,Date.now()+(delayed?700:0));setTimeout(()=>{try{route.send(message);}catch{}},Math.max(0,deliverAt-Date.now()));}
    });
  });
  try{
    await page.goto(url);await page.waitForFunction(()=>+document.querySelector('#frames').textContent>5);
    assert.equal(ready.at(-1).feedback,'realtime');
    await page.locator('#weight').fill('8');await page.locator('#weight').dispatchEvent('change');await page.locator('#connect').click();
    await page.waitForFunction(()=>+document.querySelector('#frames').textContent>5);assert.equal(ready.at(-1).regions[0].weight,8);
    // Unsaved edits must not become active on network recovery.
    await page.locator('#weight').fill('3');await page.locator('#weight').dispatchEvent('change');
    block=true;suppress=true;const before=Number(await page.locator('#frames').textContent());await page.waitForTimeout(5500);
    assert.equal(Number(await page.locator('#frames').textContent()),before);
    assert.equal(await page.locator('#stale').isVisible(),true);
    assert.ok(Number(await page.locator('#age').textContent())>3000);
    const restored=Date.now();block=false;suppress=false;
    await page.waitForFunction(n=>+document.querySelector('#frames').textContent>n+3,before,{timeout:12000});
    const recoveryMs=Date.now()-restored;
    assert.equal(ready.at(-1).regions[0].weight,8);checks.push({test:'blackhole_recovery_keeps_config',recovery_ms:recoveryMs});
    delayed=true;await page.waitForTimeout(1800);delayed=false;
    await page.waitForFunction(()=>+document.querySelector('#dropped').textContent>0,{},{timeout:5000});
    checks.push({test:'late_frames_discarded',count:Number(await page.locator('#dropped').textContent())});
    block=true;suppress=true;await page.waitForTimeout(3000);await page.locator('#pause').click();const stopped=ready.length;
    block=false;suppress=false;await page.waitForTimeout(5000);assert.equal(ready.length,stopped);assert.equal(await page.locator('#status').textContent(),'已停止');checks.push({test:'pause_cancels_retry',pass:true});
    assert.deepEqual(errors,[]);await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify({url,checks,errors},null,2));console.log(JSON.stringify({checks,errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
