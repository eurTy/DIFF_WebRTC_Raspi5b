// Requires Playwright and an installed Edge browser. Captures numbers, never camera images.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const base = process.argv[2] || 'http://192.168.3.19:8080';
const out = path.resolve(process.argv[3] || 'benchmark-output');
const seconds = Math.max(30, Number(process.argv[4]) || 120);
const soak = Math.max(30, Number(process.argv[5]) || 600);
const single = process.argv[6];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
fs.mkdirSync(out, { recursive: true });

function stats(values) {
  const a = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!a.length) return { n: 0 };
  const percentile = p => a[Math.ceil(a.length * p) - 1];
  return { n: a.length, mean: a.reduce((s, x) => s + x, 0) / a.length,
    min: a[0], p50: percentile(.5), p95: percentile(.95), p99: percentile(.99), max: a.at(-1) };
}

async function run(browser, label, mode, duration) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const url = mode === 'hmi' ? base + '/monitor/' : base + '/?transport=ws&flow=decode&clock=' + mode;
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    if (mode === 'hmi') await page.waitForSelector('#video');
    const target = mode === 'hmi' ? await (await page.$('#video')).contentFrame() : page;
    await target.waitForFunction(() => typeof latency !== 'undefined' && latency.snapshot().state === 'ready' && frameCount >= 30, null, { timeout: 60000 });
    await delay(15000);
    await target.evaluate(() => {
      const b = window.__experiment = { frames: [], clocks: [], ages: [] };
      const decoded = latency.noteDecoded.bind(latency);
      latency.noteDecoded = (id, at) => {
        decoded(id, at);
        const f = latency.frames.get(id);
        const valid = latency.clocks.length >= 3 && latency.lastReply !== null && performance.now() - latency.lastReply <= 10000;
        const best = valid ? latency.clocks.reduce((a, c) => a.rtt < c.rtt ? a : c) : null;
        b.frames.push({ id, decoded: f.decoded, published: f.published, received: f.received, timing: f.timing,
          offset: best?.offset ?? null, margin: best ? best.rtt / 2 + 1 : null });
      };
      const accept = latency.acceptClock.bind(latency);
      latency.acceptClock = value => { const ok = accept(value); if (ok) b.clocks.push({ ...latency.clocks.at(-1) }); return ok; };
      b.timer = setInterval(() => { const v = latency.snapshot(); b.ages.push({ at: performance.now(), age: v.ageMs, state: v.state }); }, 100);
      b.start = performance.now();
    });
    const measuredAt = new Date().toISOString();
    const observations = [];
    console.log(JSON.stringify({ label, state: 'collecting', seconds: duration }));
    for (let n = 0; n < duration; n++) {
      const before = Date.now();
      try {
        const response = await fetch(base + '/observe/status', { signal: AbortSignal.timeout(1800) });
        observations.push({ at: new Date().toISOString(), http: response.status, state: await response.json() });
      } catch (error) { observations.push({ at: new Date().toISOString(), error: String(error) }); }
      await delay(Math.max(0, 1000 - (Date.now() - before)));
      if ((n + 1) % 30 === 0) console.log(JSON.stringify({ label, elapsed: n + 1 }));
    }
    const raw = await target.evaluate(() => {
      clearInterval(window.__experiment.timer);
      return { ...window.__experiment, end: performance.now(), timer: undefined,
        width: videoEl.naturalWidth, height: videoEl.naturalHeight, status: statusEl.textContent };
    });
    const frames = raw.frames;
    const calibrated = frames.filter(f => Number.isFinite(f.offset) && Number.isFinite(f.published));
    const latencyValues = calibrated.map(f => f.decoded + f.offset - f.published);
    const intervals = frames.slice(1).map((f, i) => f.decoded - frames[i].decoded);
    const status = observations.filter(o => o.state?.sample);
    const driver = calibrated.filter(f => f.timing && (f.timing.flags & 0xe000) === 0x2000 && f.timing.driver > 0 && f.timing.driver <= f.timing.dequeued);
    const report = { label, mode, measuredAt, duration: (raw.end - raw.start) / 1000,
      frameCount: frames.length, calibratedCount: calibrated.length, fps: frames.length / ((raw.end - raw.start) / 1000),
      width: raw.width, height: raw.height, latency: stats(latencyValues), driverToDecode: stats(driver.map(f => f.decoded + f.offset - f.timing.driver)),
      capture: stats(driver.map(f => f.timing.published - f.timing.driver)),
      dispatch: stats(calibrated.map(f => f.timing ? f.timing.sent - f.timing.published : NaN)),
      network: stats(calibrated.map(f => f.timing ? f.received + f.offset - f.timing.sent : NaN)),
      decode: stats(frames.map(f => f.decoded - f.received)), age: stats(raw.ages.map(a => a.age)),
      ageInvalid: raw.ages.filter(a => !Number.isFinite(a.age)).length,
      interval: stats(intervals), gaps200: intervals.filter(x => x > 200).length, gaps1000: intervals.filter(x => x > 1000).length,
      negativeLatency: latencyValues.filter(x => x < 0).length, clockRtt: stats(raw.clocks.map(c => c.rtt)),
      clockMargin: stats(calibrated.map(f => f.margin)), statusRequests: observations.length,
      statusBad: observations.filter(o => o.state?.quality !== 'GOOD').length,
      cpu: stats(status.map(o => o.state.sample.sensors.cpu_percent)),
      temperature: stats(status.map(o => o.state.sample.sensors.temperature_c)),
      samplingLate: stats(status.map(o => o.state.sample.sampling_late_ms)), errors };
    fs.writeFileSync(path.join(out, label + '.json.gz'), zlib.gzipSync(JSON.stringify({ report, raw, observations }), { level: 9 }));
    fs.writeFileSync(path.join(out, label + '-summary.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ state: 'complete', report }));
    if (errors.length || raw.width !== 1280 || raw.height !== 720 || !frames.length) throw new Error('Invalid browser experiment');
    return report;
  } finally { await context.close(); }
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const reports = [];
  try {
    const plan = single ? [[single, 'separate', seconds]] : [['A1-shared', 'shared', seconds], ['B1-separate', 'separate', seconds],
      ['B2-separate', 'separate', seconds], ['A2-shared', 'shared', seconds], ['C-hmi-soak', 'hmi', soak]];
    for (const [label, mode, duration] of plan) {
      reports.push(await run(browser, label, mode, duration));
      fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(reports, null, 2));
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
