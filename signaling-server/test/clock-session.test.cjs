const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { attachClockSession } = require('../clock-session');
const { LatencyTracker } = require('../public/latency');

function socket() {
    const ws = new EventEmitter(); ws.bufferedAmount = 0; ws.sent = [];
    ws.send = text => ws.sent.push(JSON.parse(text)); ws.close = code => { ws.closed = code; };
    return ws;
}

test('separate clock replies use the same monotonic timebase without video state', () => {
    const ws = socket(); attachClockSession(ws, () => 1234);
    ws.emit('message', Buffer.from(JSON.stringify({ type: 'latency_clock_request', request_id: 7 })), false);
    assert.deepEqual(ws.sent[0], { type: 'latency_clock_response', request_id: 7, server_receive_ms: 1234, server_send_ms: 1234 });
});

test('clock rejects non-clock payloads and rate excess', () => {
    const ws = socket(); attachClockSession(ws, () => 1);
    for (let i = 0; i < 17; i++) ws.emit('message', Buffer.from(JSON.stringify({ type: 'latency_clock_request', request_id: i })), false);
    assert.equal(ws.sent.length, 16); assert.equal(ws.closed, 1008);
    const bad = socket(); attachClockSession(bad); bad.emit('message', Buffer.from('{}'), false);
    assert.equal(bad.closed, 1008);
});

test('late clock reply cannot alias a new probe after video reconnect', () => {
    let now = 0; const tracker = new LatencyTracker(() => now);
    const old = tracker.request(); tracker.reset(); now = 10;
    const current = tracker.request(); assert.notEqual(old.request_id, current.request_id);
    assert.equal(tracker.acceptClock({ request_id: old.request_id, server_receive_ms: 2, server_send_ms: 3 }), false);
});
