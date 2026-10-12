const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const modulePromise=import('data:text/javascript;base64,'+fs.readFileSync(require('node:path').join(__dirname,'../web/protocol.js')).toString('base64'));
test('watchdog allows legitimate low-rate full-frame pacing',async()=>{
 const {watchdogMs}=await modulePromise;assert.equal(watchdogMs(15000,1200,10),2500);assert.equal(watchdogMs(250000,128,2),31750);
});
test('four-timestamp offset, min RTT, warmup and expiry',async()=>{
 const {Clock}=await modulePromise,c=new Clock();assert.equal(c.estimate(100),null);
 for(let i=0;i<3;i++)assert.equal(c.add(1000+i*100,1510+i*100,1511+i*100,1021+i*100),true);
 assert.equal(c.estimate(1221).offset,500);assert.equal(c.estimate(1221).rtt,20);assert.equal(c.age(1700,1221),21);assert.equal(c.estimate(20000),null);assert.equal(c.add(1,3,2,5),false);
});
test('malformed binary and invalid frame metadata rejected',async()=>{
 const {unpack}=await modulePromise;assert.throws(()=>unpack(new ArrayBuffer(0)));
 const make=meta=>{const text=Buffer.from(JSON.stringify(meta)),buffer=Buffer.alloc(8+text.length+4);buffer.write('SVP1');buffer.writeUInt32BE(text.length,4);text.copy(buffer,8);buffer.set([255,216,255,217],8+text.length);return buffer.buffer.slice(buffer.byteOffset,buffer.byteOffset+buffer.length);};
 const valid={id:1,width:1280,height:720,captured_ms:1,sent_ms:2,encode_ms:1,parts:[{rect:[0,0,1280,720]}],lengths:[4]};assert.equal(unpack(make(valid)).meta.id,1);
 for(const change of [{id:true},{captured_ms:false},{lengths:[5]},{parts:[{rect:[0,0,10,10]}]}])assert.throws(()=>unpack(make({...valid,...change})));
});
