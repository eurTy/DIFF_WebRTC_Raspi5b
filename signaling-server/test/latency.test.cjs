const { test } = require('node:test');
const assert = require('node:assert/strict');
const { LatencyTracker } = require('../public/latency.js');

function fixture() {
  let at = 0;
  const tracker = new LatencyTracker(() => at);
  const clock = (outbound = 5, inbound = 5, offset = 10000) => {
    const sent = at;
    const request = tracker.request();
    at += outbound + inbound;
    return tracker.acceptClock({
      request_id: request.request_id,
      server_receive_ms: sent + outbound + offset,
      server_send_ms: sent + outbound + offset,
    });
  };
  const calibrated = () => { clock(); clock(); clock(); };
  return { tracker, clock, calibrated, set: value => { at = value; } };
}

test('shows no latency before three clock replies', () => {
  const f = fixture();
  f.clock(); f.clock();
  assert.equal(f.tracker.snapshot().state, 'calibrating');
  assert.equal(f.tracker.snapshot().delayMs, null);
});

test('uses minimum RTT offset instead of queued clock replies', () => {
  const f = fixture();
  f.clock(5, 5); f.clock(200, 1); f.clock(20, 20);
  f.tracker.noteMetadata(1, 10150);
  f.set(250); f.tracker.noteDecoded(1);
  const result = f.tracker.snapshot();
  assert.equal(result.delayMs, 100);
  assert.equal(result.uncertaintyMs, 6);
});

test('handles metadata arriving after JPEG decoding', () => {
  const f = fixture(); f.calibrated();
  f.set(200); f.tracker.noteDecoded(1);
  assert.equal(f.tracker.snapshot().state, 'waiting');
  f.tracker.noteMetadata(1, 10140);
  assert.equal(f.tracker.snapshot().delayMs, 60);
});

test('frame age grows during a stall without changing decoded latency', () => {
  const f = fixture(); f.calibrated();
  f.tracker.noteMetadata(1, 10140);
  f.set(200); f.tracker.noteDecoded(1);
  f.set(1200);
  assert.equal(f.tracker.snapshot().delayMs, 60);
  assert.equal(f.tracker.snapshot().ageMs, 1060);
});

test('expires statistics outside the ten-second window', () => {
  const f = fixture(); f.calibrated();
  f.tracker.noteMetadata(1, 10140);
  f.set(200); f.tracker.noteDecoded(1);
  f.set(10000); f.clock();
  f.set(10201);
  const result = f.tracker.snapshot();
  assert.equal(result.count, 0);
  assert.equal(result.meanMs, null);
  assert.equal(result.delayMs, 60);
});

test('calculates mean and nearest-rank P95', () => {
  const f = fixture(); f.calibrated();
  for (let id = 1; id <= 20; id++) {
    f.set(1000 + id);
    f.tracker.noteMetadata(id, 11000 + id - id * 10);
    f.tracker.noteDecoded(id);
  }
  const result = f.tracker.snapshot();
  assert.equal(result.count, 20);
  assert.equal(result.meanMs, 105);
  assert.equal(result.p95Ms, 190);
});

test('suppresses numbers after stale calibration', () => {
  const f = fixture(); f.calibrated();
  f.set(10031);
  assert.equal(f.tracker.snapshot().state, 'stale');
  assert.equal(f.tracker.snapshot().uncertaintyMs, null);
});

test('rejects mismatched, expired and invalid clock responses', () => {
  const f = fixture();
  assert.equal(f.tracker.acceptClock({ request_id: 999, server_receive_ms: 10000, server_send_ms: 10000 }), false);
  const request = f.tracker.request();
  f.set(10001);
  assert.equal(f.tracker.acceptClock({ ...request, server_receive_ms: 10000, server_send_ms: 10000 }), false);
  const second = f.tracker.request();
  assert.equal(f.tracker.acceptClock({ ...second, server_receive_ms: 20000, server_send_ms: 20010 }), false);
  const third = f.tracker.request();
  assert.equal(f.tracker.acceptClock({ ...third, server_receive_ms: NaN, server_send_ms: 20000 }), false);
});

test('does not turn negative latency into a false zero', () => {
  const f = fixture(); f.calibrated();
  f.set(100); f.tracker.noteDecoded(1); f.tracker.noteMetadata(1, 10200);
  assert.equal(f.tracker.snapshot().state, 'invalid');
  assert.equal(f.tracker.snapshot().delayMs, null);
});

test('clears old frame and clock state on reconnect', () => {
  const f = fixture(); f.calibrated();
  f.tracker.noteMetadata(1, 10010); f.tracker.noteDecoded(1);
  f.tracker.reset();
  assert.equal(f.tracker.snapshot().count, 0);
  assert.equal(f.tracker.snapshot().state, 'calibrating');
  assert.equal(f.tracker.latest, null);
});

test('older decode callback cannot replace latest displayed frame', () => {
  const f = fixture(); f.calibrated();
  f.tracker.noteDecoded(2, 200); f.tracker.noteDecoded(1, 100);
  assert.equal(f.tracker.latest.id, 2);
});

test('keeps tracking memory bounded and expires old minimum RTT', () => {
  const f = fixture(); f.calibrated();
  for (let id = 0; id < 1000; id++) f.tracker.noteMetadata(id, 10000 + id);
  f.tracker.prune();
  assert.ok(f.tracker.frames.size <= 512);
  f.set(31000); f.clock(10, 10); f.clock(10, 10); f.clock(10, 10);
  assert.equal(f.tracker.snapshot().uncertaintyMs, 11);
});
