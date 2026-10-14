const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
  const {loadSettings,saveSettings,SETTINGS_KEY}=await import('data:text/javascript;base64,'+Buffer.from(await fs.readFile('web/settings.js')).toString('base64'));
  const values=new Map(),storage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)};
  const config={type:'start',mode:'semantic',feedback:'realtime',fps:20,kbps:2400,seed:7,regions:[{name:'process-A',x:0,y:0,w:.2,h:.2,weight:6},{name:'process-B',x:.7,y:.5,w:.2,h:.2,weight:4}]};
  assert.equal(loadSettings(storage),null);
  assert.equal(saveSettings(storage,config),true);assert.deepEqual(loadSettings(storage),config);
  config.regions[0].weight=9;assert.equal(loadSettings(storage).regions[0].weight,6);
  for(const change of [{fps:NaN},{fps:1},{kbps:true},{mode:'invalid'},{regions:[]},{seed:999}])assert.equal(saveSettings(storage,{...config,...change}),false);
  values.set(SETTINGS_KEY,'{broken');assert.equal(loadSettings(storage),null);
  values.set(SETTINGS_KEY,JSON.stringify({version:2,config}));assert.equal(loadSettings(storage),null);
  const denied={getItem(){throw Error('denied');},setItem(){throw Error('denied');}};
  assert.equal(loadSettings(denied),null);assert.equal(saveSettings(denied,config),false);
  console.log('Settings validation, persistence, versioning and denied storage: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
