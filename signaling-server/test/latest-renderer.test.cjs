const { test } = require('node:test');
const assert = require('node:assert/strict');
const { LatestRenderer } = require('../public/latest-renderer');
const { LatencyTracker } = require('../public/latency');
const tick = () => new Promise(resolve => setImmediate(resolve));

test('decoder holds one pending latest frame and returns discarded credit', async () => {
    const rendered = [], released = [], dropped = [];
    let complete, active = true;
    const decoder = new LatestRenderer({ active: () => active,
        render: async frame => { rendered.push(frame); if (frame === 1) await new Promise(r => { complete = r; }); },
        release: frame => released.push(frame), discard: frame => dropped.push(frame), error: e => { throw e; } });
    decoder.enqueue(1); decoder.enqueue(2); decoder.enqueue(3);
    assert.deepEqual(rendered, [1]); assert.deepEqual(dropped, [2]); assert.deepEqual(released, [2]);
    complete(); await tick();
    assert.deepEqual(rendered, [1, 3]); assert.deepEqual(released, [2, 1, 3]);
    active = false; decoder.enqueue(4); assert.equal(decoder.pending, null);
});

test('closing a connection prevents a pending frame from being rendered or acknowledged', async () => {
    let active = true, complete; const rendered = [], released = [];
    const decoder = new LatestRenderer({ active: () => active,
        render: frame => { rendered.push(frame); return new Promise(r => { complete = r; }); },
        release: frame => released.push(frame), discard: () => {}, error: e => { throw e; } });
    decoder.enqueue(1); decoder.enqueue(2); active = false; complete(); await tick();
    assert.deepEqual(rendered, [1]); assert.deepEqual(released, []); assert.equal(decoder.pending, null);
});

test('stage metrics distinguish local intervals from calibrated cross-clock estimates', () => {
    let now = 200; const tracker = new LatencyTracker(() => now);
    tracker.clocks = [1, 2, 3].map(() => ({ at: now, rtt: 4, offset: 1000 })); tracker.lastReply = now;
    tracker.noteTiming(1, { driver: 1100, dequeued: 1110, published: 1112, sent: 1120, flags: 0x2000 }, 150);
    tracker.noteDecoded(1, 160);
    assert.deepEqual(tracker.stages(), { source: 'V4L2 EOF', capture: 12, dispatch: 8, network: 30, decode: 10, total: 60 });
    tracker.latest.timing.flags = 0x12000;
    assert.equal(tracker.stages().source, 'V4L2 SOE');
    tracker.latest.timing.driver = 1113;
    assert.equal(tracker.stages().capture, null); assert.equal(tracker.stages().total, null);
    tracker.latest.timing.flags = 0;
    assert.equal(tracker.stages().capture, null); assert.equal(tracker.stages().total, null);
    now += 11000; assert.equal(tracker.stages(), null);
});
