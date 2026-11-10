// CRC is an accidental-corruption check, not authentication. No image is logged.
const table=new Uint32Array(256);
for(let n=0;n<256;n++){let c=n;for(let i=0;i<8;i++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;table[n]=c>>>0;}
export function crc32(data){let c=0xffffffff;for(const byte of data)c=table[(c^byte)&255]^(c>>>8);return (c^0xffffffff)>>>0;}

export function integrityError(meta,data,jpeg=false,actualCrc=crc32(data)){
  if(meta.integrity!=='crc32-v1'||!Number.isSafeInteger(meta.payload_bytes)||meta.payload_bytes!==data.length)return 'payload_length';
  if(!Number.isSafeInteger(meta.payload_crc32)||meta.payload_crc32<0||meta.payload_crc32>0xffffffff)return 'checksum_header';
  if(jpeg){
    if(!Array.isArray(meta.parts)||meta.parts.length<1||meta.parts.length>5||meta.parts.length!==meta.lengths?.length)return 'jpeg_parts';
    if(!meta.lengths.every(n=>Number.isSafeInteger(n)&&n>=4)||meta.lengths.reduce((a,b)=>a+b,0)!==data.length)return 'jpeg_lengths';
    let offset=0;
    for(let i=0;i<meta.parts.length;i++){
      const r=meta.parts[i]?.rect,n=meta.lengths[i];
      if(!Array.isArray(r)||r.length!==4||!r.every(Number.isSafeInteger)||r[0]<0||r[1]<0||r[2]<=0||r[3]<=0||r[0]+r[2]>1280||r[1]+r[3]>720)return 'jpeg_rectangle';
      if(data[offset]!==255||data[offset+1]!==216||data[offset+n-2]!==255||data[offset+n-1]!==217)return 'jpeg_marker';
      offset+=n;
    }
  }
  return actualCrc===meta.payload_crc32?null:'payload_checksum';
}
