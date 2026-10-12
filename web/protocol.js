export function unpack(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 8 || buffer.byteLength > 4194304) throw Error('Invalid packet size');
  const view = new DataView(buffer), size = view.getUint32(4);
  if (view.getUint32(0) !== 0x53565031 || size < 2 || size > 49152 || size + 8 > buffer.byteLength) throw Error('Invalid header');
  const meta = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(new Uint8Array(buffer, 8, size)));
  if (!meta || meta.width !== 1280 || meta.height !== 720 || !Number.isSafeInteger(meta.id) || meta.id < 0) throw Error('Invalid metadata');
  if (!Array.isArray(meta.parts) || !Array.isArray(meta.lengths) || meta.parts.length < 1 || meta.parts.length > 5 || meta.lengths.length !== meta.parts.length) throw Error('Invalid parts');
  for (const key of ['captured_ms', 'sent_ms', 'encode_ms']) if (typeof meta[key] !== 'number' || !Number.isFinite(meta[key]) || meta[key] < 0) throw Error('Invalid timestamp');
  let offset = 8 + size;
  const images = meta.parts.map((part, i) => {
    if (!part || !Array.isArray(part.rect) || part.rect.length !== 4 || !part.rect.every(Number.isSafeInteger)) throw Error('Invalid rectangle');
    const [x,y,w,h] = part.rect, length = meta.lengths[i];
    if (x<0 || y<0 || w<=0 || h<=0 || x+w>1280 || y+h>720 || (i===0 && (x!==0 || y!==0 || w!==1280 || h!==720))) throw Error('Invalid bounds');
    if (!Number.isSafeInteger(length) || length<4 || offset+length>buffer.byteLength) throw Error('Invalid image length');
    const bytes = new Uint8Array(buffer, offset, length);
    if (bytes[0]!==255 || bytes[1]!==216) throw Error('Expected JPEG');
    offset += length;
    return new Blob([bytes], {type:'image/jpeg'});
  });
  if (offset !== buffer.byteLength) throw Error('Trailing data');
  return {meta, images};
}

export class Clock {
  constructor() { this.samples=[]; }
  add(t0,t1,t2,t3) {
    if (![t0,t1,t2,t3].every(Number.isFinite) || t3<t0 || t2<t1) return false;
    const rtt=(t3-t0)-(t2-t1);
    if (rtt<0 || rtt>1000) return false;
    this.samples=this.samples.filter(s=>t3-s.time<30000);
    this.samples.push({time:t3,rtt,offset:((t1-t0)+(t2-t3))/2});
    if(this.samples.length>40) this.samples.shift();
    return true;
  }
  estimate(now) {
    if (this.samples.length<3 || now-this.samples.at(-1).time>10000) return null;
    const fresh=this.samples.filter(s=>now-s.time<30000);
    if(fresh.length<3) return null;
    return fresh.reduce((a,b)=>a.rtt<b.rtt?a:b);
  }
  age(captured,now) {
    const estimate=this.estimate(now);
    if(!estimate || !Number.isFinite(captured)) return null;
    const value=now+estimate.offset-captured;
    return value>=-estimate.rtt/2-2 ? Math.max(0,value) : null;
  }
}
