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
test('rejects malformed lengths, indices and timestamp changes', () => {
  assert.equal(parseFragment(new ArrayBuffer(23)), null);
  assert.equal(packet(1, 2, 2), null);
  const a = new Assembler(); a.accept(packet(1));
  assert.equal(a.accept(packet(1, 1, 2, 6000000000)), null);
});
