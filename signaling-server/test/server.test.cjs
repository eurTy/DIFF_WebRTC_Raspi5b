const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const WebSocket = require('ws');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

test('real server routes both transports and resets consumed RTC sessions', { timeout: 15000 }, async t => {
    const listener = net.createServer();
    listener.listen(0, '127.0.0.1'); await once(listener, 'listening');
    const port = listener.address().port;
    await new Promise(resolve => listener.close(resolve));
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'pi-server-test-'));
    const filename = path.join(dir, 'video_shm');
    const frame = Buffer.alloc(52);
    frame.writeUInt32LE(42, 0); frame.writeUInt32LE(4, 12); frame.writeUInt32LE(2, 16);
    frame.writeBigUInt64LE(5000000000n, 40); Buffer.from([255, 216, 255, 217]).copy(frame, 48);
    await fs.writeFile(filename, frame);
    const child = spawn(process.execPath, ['server.js'], {
        cwd: path.join(__dirname, '..'), env: { ...process.env, PORT: String(port), VIDEO_SHM_PATH: filename },
        stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
    });
    let output = '';
    child.stdout.on('data', data => { output += data; });
    child.stderr.on('data', data => { output += data; });
    const exited = once(child, 'exit');
    const sockets = [];
    t.after(async () => {
        for (const socket of sockets) socket.terminate();
        child.kill(); await exited; await fs.rm(dir, { recursive: true });
    });
    let httpReady = false;
    for (let i = 0; i < 100; i++) {
        try { const response = await fetch(`http://127.0.0.1:${port}/`); await response.text(); httpReady = response.ok; } catch {}
        if (httpReady) break;
        await delay(20);
    }
    assert.ok(httpReady, 'server must listen: ' + output);
    async function connect(role) {
        const socket = new WebSocket(`ws://127.0.0.1:${port}/?role=${role}&v=20260607b`);
        socket.messages = [];
        socket.on('message', (raw, binary) => socket.messages.push(binary ? raw : JSON.parse(raw)));
        sockets.push(socket); await once(socket, 'open'); return socket;
    }
    async function message(socket) {
        for (let i = 0; i < 100; i++) {
            if (socket.messages.length) return socket.messages.shift();
            await delay(10);
        }
        assert.fail('expected protocol message');
    }
    const gateway = await connect('gateway');
    gateway.send(JSON.stringify({ type: 'offer', sdp: 'test-sdp' }));
    const viewer = await connect('viewer');
    assert.equal((await message(viewer)).sdp, 'test-sdp');
    viewer.send(JSON.stringify({ type: 'answer', sdp: 'test-answer' }));
    assert.equal((await message(gateway)).type, 'answer');
    const replacement = await connect('viewer');
    assert.equal((await message(gateway)).type, 'viewer_reset');
    assert.equal(replacement.messages.length, 0, 'must not reuse consumed offer');
    const renewedGateway = await connect('gateway');
    renewedGateway.send(JSON.stringify({ type: 'offer', sdp: 'fresh-sdp' }));
    assert.equal((await message(replacement)).sdp, 'fresh-sdp');
    const latest = await connect('latest');
    latest.send(JSON.stringify({ type: 'frame_request', after: 0 }));
    const jpeg = await message(latest);
    assert.ok(Buffer.isBuffer(jpeg), 'latest viewer must not receive SDP');
    assert.equal(jpeg.readUInt32BE(0), 0x56504632);
    assert.deepEqual([...jpeg.subarray(24)], [255, 216, 255, 217]);
    latest.send(JSON.stringify({ type: 'latency_clock_request', request_id: 7 }));
    assert.equal((await message(latest)).request_id, 7);
});
