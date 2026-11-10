const assert=require('node:assert/strict');
(async()=>{
  const {crc32,integrityError}=await import('../experiment_v07/web/integrity.js');
  assert.equal(crc32(new TextEncoder().encode('123456789')),0xcbf43926);
  const data=Uint8Array.from([255,216,1,2,255,217]);
  const meta={integrity:'crc32-v1',payload_bytes:data.length,payload_crc32:crc32(data),parts:[{rect:[0,0,1280,720]}],lengths:[6]};
  assert.equal(integrityError(meta,data,true),null);
  assert.equal(integrityError(meta,data.slice(0,5),true),'payload_length');
  const altered=data.slice();altered[2]=3;
  assert.equal(integrityError(meta,altered,true),'payload_checksum');
  assert.equal(integrityError({...meta,lengths:[5]},data,true),'jpeg_lengths');
  assert.equal(integrityError({...meta,parts:[{rect:[0,0,1281,720]}]},data,true),'jpeg_rectangle');
  assert.equal(integrityError({...meta,payload_crc32:-1},data,true),'checksum_header');
  assert.equal(integrityError({...meta,parts:[]},data,true),'jpeg_parts');
  console.log('CRC32 standard vector and six rejection paths passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
