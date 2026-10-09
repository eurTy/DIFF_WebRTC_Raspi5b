(function (root) {
  'use strict';
  const newer = (a, b) => a !== b && ((a - b) >>> 0) < 0x80000000;

  function parseFragment(buffer) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 16) return null;
    const view = new DataView(buffer);
    const magic = view.getUint32(0);
    const headerSize = magic === 0x56504632 ? 24 : magic === 0x56504631 ? 16 : 0;
    if (!headerSize || buffer.byteLength < headerSize) return null;
    const id = view.getUint32(4), index = view.getUint16(8), total = view.getUint16(10);
    const length = view.getUint32(12);
    if (!total || total > 8192 || index >= total || !length || length > 65536 || buffer.byteLength !== headerSize + length) return null;
    return {
      id, index, total, payload: new Uint8Array(buffer, headerSize, length),
      published: headerSize === 24 ? view.getUint32(16) * 4294967296 + view.getUint32(20) : null,
    };
  }

  class Assembler {
    constructor(now = () => performance.now()) { this.now = now; this.reset(); }
    reset() {
      this.frames = this.frames || new Map(); this.frames.clear();
      this.retired = new Map(); this.lastRendered = null;
      this.latestPublished = 0; this.latestId = null; this.dropped = 0;
    }
    discard(id) {
      if (this.frames.delete(id)) this.dropped++;
      this.retired.set(id, this.now());
    }
    prune() {
      const now = this.now();
      for (const [id, frame] of this.frames) if (now - frame.createdAt > 120) this.discard(id);
      while (this.frames.size > 2) this.discard(this.frames.keys().next().value);
      for (const [id, at] of this.retired) if (now - at > 2000) this.retired.delete(id);
      while (this.retired.size > 128) this.retired.delete(this.retired.keys().next().value);
    }
    ensureFrame(id, total, totalBytes = 0) {
      if (!Number.isInteger(total) || total <= 0 || total > 8192 || this.retired.has(id) ||
          (this.lastRendered !== null && !newer(id, this.lastRendered))) return null;
      if (!this.frames.has(id)) this.frames.set(id, {
        totalFragments: total, totalBytes, chunks: new Array(total), received: 0,
        receivedBytes: 0, createdAt: this.now(), published: null,
      });
      return this.frames.get(id);
    }
    accept(packet) {
      if (!packet) return null;
      // Capture restarts can reset frame IDs while the monotonic clock keeps advancing.
      if (packet.published > this.latestPublished && this.latestId !== null &&
          packet.id !== this.latestId && !newer(packet.id, this.latestId)) this.reset();
      if (packet.published > this.latestPublished) { this.latestPublished = packet.published; this.latestId = packet.id; }
      this.prune();
      const frame = this.ensureFrame(packet.id, packet.total);
      if (!frame) return null;
      if (frame.totalFragments !== packet.total ||
          (frame.published !== null && frame.published !== packet.published)) { this.discard(packet.id); return null; }
      frame.published = packet.published;
      if (!frame.chunks[packet.index]) {
        frame.chunks[packet.index] = packet.payload;
        frame.received++;
        frame.receivedBytes += packet.payload.byteLength;
      }
      if (frame.receivedBytes > 8 * 1024 * 1024) { this.discard(packet.id); return null; }
      this.prune();
      return this.frames.has(packet.id) ? frame : null;
    }
    finish(id) {
      const frame = this.frames.get(id);
      if (!frame || frame.received !== frame.totalFragments) return false;
      if (this.lastRendered !== null && !newer(id, this.lastRendered)) { this.discard(id); return false; }
      this.lastRendered = id;
      this.frames.delete(id);
      for (const older of this.frames.keys()) if (!newer(older, id)) this.discard(older);
      return true;
    }
  }
  const api = { Assembler, parseFragment, newer };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.VideoFrames = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
