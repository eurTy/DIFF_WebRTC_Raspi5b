const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { once } = require('node:events');
const model = require('../public/monitor/model');
const { observerProxy } = require('../observer-proxy');

test('state freshness grows when the polling request stops', () => {
    const status = { quality: 'GOOD', sample_age_ms: 20, sample: { source: 'MEASURED' } };
    assert.equal(model.state(status, 100, 200).quality, 'GOOD');
    assert.equal(model.state(status, 100, 3000).quality, 'STALE');
    assert.equal(model.state(null, null, 3000).quality, 'OFFLINE');
    assert.equal(model.format(null), '--');
    assert.equal(model.format(NaN), '--');
});

test('video age and transport silence are independently stale', () => {
    const value = { sample: { state: 'ready', ageMs: 20 }, receivedAgoMs: 0 };
    assert.equal(model.videoQuality(value, 100, 200), 'GOOD');
    assert.equal(model.videoQuality(value, 100, 800), 'STALE');
    assert.equal(model.videoQuality(value, 100, 3000), 'OFFLINE');
    assert.equal(model.videoQuality({ ...value, sample: { state: 'stale' } }, 100, 110), 'UNCALIBRATED');
});

test('trend never joins histories across observer restarts', () => {
    assert.deepEqual(model.series([{ run_id: 'old', mono_ms: 5 }, { run_id: 'new', mono_ms: 10 }], 'new'), [{ run_id: 'new', mono_ms: 10 }]);
});

test('observer proxy only exposes whitelisted read routes and fails closed', async t => {
    const backend = http.createServer((req, res) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ path: req.url })); });
    backend.listen(0, '127.0.0.1'); await once(backend, 'listening');
    const port = backend.address().port;
    const front = http.createServer((req, res) => {
        const url = new URL(req.url, 'http://localhost');
        if (!observerProxy(req, res, url.pathname, url.search, port)) { res.writeHead(404); res.end(); }
    });
    front.listen(0, '127.0.0.1'); await once(front, 'listening');
    t.after(() => { front.closeAllConnections(); front.close(); backend.closeAllConnections(); backend.close(); });
    const url = 'http://127.0.0.1:' + front.address().port;
    assert.equal((await (await fetch(url + '/observe/status')).json()).path, '/status');
    assert.equal((await fetch(url + '/observe/status', { method: 'POST' })).status, 400);
    assert.equal((await fetch(url + '/observe/status?url=http://example.com')).status, 400);
    assert.equal((await fetch(url + '/observe/commands')).status, 400);
    await new Promise(resolve => backend.close(resolve));
    assert.equal((await fetch(url + '/observe/status')).status, 502);
});
