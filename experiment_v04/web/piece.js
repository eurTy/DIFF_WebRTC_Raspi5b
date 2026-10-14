const decoder=new TextDecoder();

export function parsePiece(buffer){
  if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<12||buffer.byteLength>2*1024*1024)throw Error('Invalid piece size');
  const view=new DataView(buffer),size=view.getUint32(4);
  if(view.getUint32(0)!==0x53565032||size<2||size>4096||8+size+2>buffer.byteLength)throw Error('Invalid piece header');
  const meta=JSON.parse(decoder.decode(new Uint8Array(buffer,8,size)));
  if(!['base','patch'].includes(meta.kind)||!Number.isSafeInteger(meta.id)||meta.id<0||!Number.isFinite(meta.captured_ms)||meta.captured_ms<0||!Array.isArray(meta.rect)||meta.rect.length!==4)throw Error('Invalid piece metadata');
  const [x,y,w,h]=meta.rect;
  if(![x,y,w,h].every(Number.isInteger)||x<0||y<0||w<=0||h<=0||x+w>1280||y+h>720||meta.kind==='base'&&(x!==0||y!==0||w!==1280||h!==720))throw Error('Invalid piece rectangle');
  if(meta.kind==='patch'&&(!meta.name||meta.name!==meta.patch_name))throw Error('Invalid patch name');
  const bytes=new Uint8Array(buffer,8+size);
  if(bytes[0]!==0xff||bytes[1]!==0xd8)throw Error('Invalid JPEG');
  return {meta,blob:new Blob([bytes],{type:'image/jpeg'}),bytes:buffer.byteLength};
}
