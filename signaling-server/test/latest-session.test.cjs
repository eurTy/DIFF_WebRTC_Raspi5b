const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { attachLatestSession } = require('../latest-session');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Socket extends EventEmitter {
    readyState = 1;
    bufferedAmount = 0;
    sent = [];
    callbacks = [];
    send(data, options, callback) {
        this.sent.push(data);
        if (callback) this.callbacks.push(callback);
    }
    request(after) { this.emit('message', Buffer.from(JSON.stringify({ type: 'frame_request', after })), false); }
    close(code) { this.closeCode = code; this.readyState = 3; this.emit('close'); }
}

test('one frame of credit prevents queued history and tolerates callback races', async () => {
    const ws = new Socket();
    let published = 10, reads = 0;
    attachLatestSession(ws, { readLatest: async () => { reads++; return { published, packet: Buffer.from([published]) }; } });
    ws.request(0); await tick();
    assert.equal(reads, 1);
    for (let i = 0; i < 20; i++) ws.request(0);
    assert.equal(reads, 1);
    ws.callbacks.shift()(); await tick();
    assert.equal(reads, 1);
    published = 20; ws.request(10); await tick();
    published = 30; ws.request(20);
    assert.equal(reads, 2);
    ws.callbacks.shift()(); await tick();
    assert.equal(reads, 3);
    assert.deepEqual(ws.sent.map(b => b[0]), [10, 20, 30]);
    ws.callbacks.shift()(); ws.close();
});

test('reports camera wait and source failure without an unbounded queue', async () => {
    const ws = new Socket();
    let fail = false;
    attachLatestSession(ws, { readLatest: async () => { if (fail) throw new Error('missing'); return null; } }, { waitMs: 0 });
    ws.request(0); await tick(); assert.equal(JSON.parse(ws.sent[0]).type, 'frame_wait');
    fail = true; ws.request(0); await tick(); assert.equal(JSON.parse(ws.sent[1]).type, 'frame_error');
    ws.close();
});

test('two-frame window never grants extra credit for duplicate or forged acknowledgments', async () => {
    const ws = new Socket(); let published = 0;
    attachLatestSession(ws, { readLatest: async () => ({ published: published += 10, packet: Buffer.from([published]) }) });
    ws.emit('message', Buffer.from('{"type":"frame_request","after":0,"window":2}'), false);
    await tick(); ws.callbacks.shift()(); await tick(); ws.callbacks.shift()(); await tick();
    assert.equal(ws.sent.length, 2);
    ws.request(999); ws.request(0); await tick(); assert.equal(ws.sent.length, 2);
    ws.request(10); await tick(); assert.equal(ws.sent.length, 3);
    ws.request(10); ws.callbacks.shift()(); await tick(); assert.equal(ws.sent.length, 3);
    ws.close();
});

test('responds with same-domain receive/send timestamps', () => {
    const ws = new Socket(); let now = 100;
    attachLatestSession(ws, {}, { now: () => now++ });
    ws.emit('message', Buffer.from('{"type":"latency_clock_request","request_id":5}'), false);
    assert.deepEqual(JSON.parse(ws.sent[0]), { type: 'latency_clock_response', request_id: 5, server_receive_ms: 100, server_send_ms: 101 });
    ws.close();
});

test('stamps VPF3 at send time without changing JPEG bytes', async () => {
    const ws = new Socket(), packet = Buffer.alloc(68, 42);
    packet.writeUInt32BE(0x56504633, 0);
    attachLatestSession(ws, { readLatest: async () => ({ published: 10, packet }) }, { now: () => 123.456 });
    ws.request(0); await tick();
    assert.equal(ws.sent[0].readBigUInt64BE(48), 123456n);
    assert.deepEqual([...ws.sent[0].subarray(64)], [42, 42, 42, 42]);
    ws.callbacks.shift()(); ws.close();
});

test('rejects invalid initial window and nonzero initial acknowledgment', () => {
    for (const message of [
        { after: 0, window: 0 }, { after: 0, window: 3 },
        { after: 0, window: '2' }, { after: 10, window: 1 },
    ]) {
        const ws = new Socket(); attachLatestSession(ws, {});
        ws.emit('message', Buffer.from(JSON.stringify({ type: 'frame_request', ...message })), false);
        assert.equal(ws.closeCode, 1008);
    }
});

test('rejects invalid control data and stops reads after close', async () => {
    for (const [raw, binary] of [['null', false], ['{}', false], ['bad', false], ['{}', true], [' '.repeat(1025), false]]) {
        const ws = new Socket(); attachLatestSession(ws, {});
        ws.emit('message', Buffer.from(raw), binary); assert.equal(ws.closeCode, 1008);
    }
    const ws = new Socket(); let resolve;
    attachLatestSession(ws, { readLatest: () => new Promise(r => { resolve = r; }) });
    ws.request(0); ws.close(); resolve({ published: 10, packet: Buffer.from([1]) });
    await tick(); assert.equal(ws.sent.length, 0);
});
