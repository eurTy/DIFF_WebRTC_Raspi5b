const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const [url='http://127.0.0.1:18088',output='runtime/startup.json']=process.argv.slice(2);
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext(),page=await context.newPage();
  const errors=[],checks=[],starts=[];let blocked=true;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/status',route=>route.abort());
  await page.routeWebSocket('**/stream',route=>{
    if(blocked){route.close({code:1013,reason:'Startup unavailable'});return;}
    const server=route.connectToServer();
    route.onMessage(message=>{if(typeof message==='string'){try{const value=JSON.parse(message);if(value.type==='start')starts.push(value);}catch{}}server.send(message);});
  });
  const receiving=()=>page.waitForFunction(()=>+document.querySelector('#frames').textContent>4,null,{timeout:15000});
  try{
    await page.goto(url);await page.waitForTimeout(2000);assert.equal(+await page.locator('#frames').textContent(),0);
    blocked=false;await receiving();checks.push('first-load outage recovers without status endpoint');
    await page.locator('#weight').fill('8');await page.locator('#weight').dispatchEvent('change');
    await page.locator('#fps').fill('12');await page.locator('#connect').click();await receiving();
    await page.locator('#weight').fill('3');await page.locator('#weight').dispatchEvent('change');
    await page.reload();await receiving();assert.equal(starts.at(-1).fps,12);assert.equal(starts.at(-1).regions[0].weight,8);
    assert.equal(await page.locator('#weight').inputValue(),'8');checks.push('reload restores applied settings, not pending edits');
    await page.evaluate(()=>localStorage.setItem('semantic-video-settings-v1','broken'));await page.reload();await receiving();
    assert.equal(starts.at(-1).fps,20);checks.push('invalid storage falls back to defaults');
    blocked=true;await page.reload();await page.waitForTimeout(500);await page.locator('#pause').click();const count=starts.length;
    blocked=false;await page.waitForTimeout(2500);assert.equal(starts.length,count);assert.equal(await page.locator('#status').textContent(),'已停止');checks.push('pause cancels first-load retry');
    await page.addInitScript(()=>Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Denied','SecurityError');}}));
    await page.reload();await receiving();checks.push('denied storage still streams');
    assert.deepEqual(errors,[]);await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify({url,checks,errors},null,2));console.log(JSON.stringify({checks,errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
