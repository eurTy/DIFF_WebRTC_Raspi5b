const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Assembler, parseFragment } = require('../public/video-frames.js');

function packet(id, index = 0, total = 2, published = 5000000000 + id) {
  const buffer = new ArrayBuffer(28);
  const view = new DataView(buffer);
  view.setUint32(0, 0x56504632); view.setUint32(4, id);
  view.setUint16(8, index); view.setUint16(10, total); view.setUint32(12, 4);
  view.setUint32(16, Math.floor(published / 4294967296)); view.setUint32(20, published % 4294967296);
  return parseFragment(buffer);
}

test('parses VPF2 publication timestamps above 32 bits', () => {
  assert.equal(packet(1).published, 5000000001);
  assert.equal(packet(1).payload.byteLength, 4);
});

test('parses VPF3 microsecond timing and rejects truncated headers', () => {
  const buffer = new ArrayBuffer(68), view = new DataView(buffer);
  view.setUint32(0, 0x56504633); view.setUint32(4, 42);
  view.setUint16(10, 1); view.setUint32(12, 4);
  view.setBigUint64(16, 5000000000n);
  [4999999967000n, 4999999999000n, 5000000000123n, 5000000001456n]
    .forEach((value, index) => view.setBigUint64(24 + index * 8, value));
  view.setUint32(56, 0x12000); view.setUint32(60, 456);
  const parsed = parseFragment(buffer);
  assert.equal(parsed.payload.byteLength, 4);
  assert.equal(parsed.published, 5000000000);
  assert.deepEqual(parsed.timing, { driver: 4999999967, dequeued: 4999999999,
    published: 5000000000.123, sent: 5000000001.456, flags: 0x12000, sequence: 456 });
  for (const length of [16, 24, 63, 64, 67]) assert.equal(parseFragment(buffer.slice(0, length)), null);
});
test('reassembles out-of-order fragments and ignores duplicates', () => {
  const a = new Assembler();
  a.accept(packet(1, 1)); a.accept(packet(1, 1));
  assert.equal(a.frames.get(1).received, 1);
  const complete = a.accept(packet(1));
  assert.equal(complete.received, 2);
  assert.equal(complete.receivedBytes, 8);
  assert.equal(a.finish(1), true);
});
test('never displays an older frame after a newer completed frame', () => {
  const a = new Assembler();
  a.accept(packet(1)); a.accept(packet(2)); a.accept(packet(2, 1)); a.finish(2);
  assert.equal(a.accept(packet(1, 1)), null);
  assert.equal(a.finish(1), false);
});
test('expires missing-fragment frames and does not revive them', () => {
  let now = 0; const a = new Assembler(() => now);
  a.accept(packet(1)); now = 121;
  assert.equal(a.accept(packet(1, 1)), null);
  assert.equal(a.dropped, 1);
});
test('limits reassembly to two concurrent frames', () => {
  const a = new Assembler();
  a.accept(packet(1)); a.accept(packet(2)); a.accept(packet(3));
  assert.equal(a.frames.size, 2);
  assert.equal(a.frames.has(1), false);
});
test('handles frame ID wraparound and a capture process restart', () => {
  const a = new Assembler();
  a.accept(packet(0xffffffff, 0, 1, 1000)); a.finish(0xffffffff);
  a.accept(packet(0, 0, 1, 1001)); assert.equal(a.finish(0), true);
  a.accept(packet(200, 0, 1, 2000)); a.finish(200);
  a.accept(packet(0, 0, 1, 3000)); assert.equal(a.finish(0), true);
});

test('rejects delayed frames from before a capture restart', () => {
  const a = new Assembler();
  a.accept(packet(500, 0, 1, 1000)); a.finish(500);
  a.accept(packet(0, 0, 1, 2000)); a.finish(0);
  assert.equal(a.accept(packet(501, 0, 1, 1001)), null);
  assert.equal(a.finish(501), false);
  a.accept(packet(1, 0, 1, 2033));
  assert.equal(a.finish(1), true);
});
test('rejects malformed lengths, indices and timestamp changes', () => {
  assert.equal(parseFragment(new ArrayBuffer(23)), null);
  assert.equal(packet(1, 2, 2), null);
  const a = new Assembler(); a.accept(packet(1));
  assert.equal(a.accept(packet(1, 1, 2, 6000000000)), null);
});
