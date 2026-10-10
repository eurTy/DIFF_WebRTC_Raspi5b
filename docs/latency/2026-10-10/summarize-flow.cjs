const fs = require('node:fs');
const zlib = require('node:zlib');

function stats(values) {
    const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
    if (!sorted.length) return { count: 0 };
    return { count: sorted.length,
        mean: sorted.reduce((sum, value) => sum + value, 0) / sorted.length,
        min: sorted[0], p50: sorted[Math.ceil(sorted.length * .5) - 1],
        p95: sorted[Math.ceil(sorted.length * .95) - 1],
        p99: sorted[Math.ceil(sorted.length * .99) - 1], max: sorted.at(-1) };
}

function summarize(filename) {
    const bytes = fs.readFileSync(filename);
    const { report, start, end, best, raw } = JSON.parse(filename.endsWith('.gz') ? zlib.gunzipSync(bytes) : bytes);
    if (!Number.isFinite(best?.offset) || !(end > start)) throw new Error('Invalid clock or interval: ' + filename);
    const frames = raw.frames.filter(f => f.decoded >= start && f.decoded < end && Number.isFinite(f.published))
        .sort((a, b) => a.decoded - b.decoded);
    if (frames.length !== report.latency.count) throw new Error('Frame count mismatch: ' + filename);
    const validDriver = f => f.timing && (f.timing.flags & 0xe000) === 0x2000 &&
        f.timing.driver > 0 && f.timing.driver <= f.timing.dequeued;
    const measured = stats(frames.map(f => f.decoded + best.offset - f.published));
    if (Math.abs(measured.mean - report.latency.mean) > .001) throw new Error('Latency mismatch: ' + filename);
    const intervals = frames.slice(1).map((f, index) => f.decoded - frames[index].decoded);
    return { ...report, latency: measured,
        fps: frames.length / ((end - start) / 1000),
        decode: stats(frames.map(f => f.decoded - f.complete)),
        frameInterval: stats(intervals),
        age: stats(raw.ages.filter(s => s.at >= start && s.at < end).map(s => s.age)),
        gapsOver200Ms: intervals.filter(value => value > 200).length,
        gapsOver1000Ms: intervals.filter(value => value > 1000).length,
        stages: {
            driverToPublish: stats(frames.map(f => validDriver(f) ? f.timing.published - f.timing.driver : NaN)),
            publishToSend: stats(frames.map(f => f.timing ? f.timing.sent - f.timing.published : NaN)),
            sendToReceive: stats(frames.map(f => f.timing ? f.received + best.offset - f.timing.sent : NaN)),
            receiveToDecode: stats(frames.map(f => f.decoded - f.received)),
            driverToDequeue: stats(frames.map(f => validDriver(f) ? f.timing.dequeued - f.timing.driver : NaN)),
            dequeueToPublish: stats(frames.map(f => f.timing ? f.timing.published - f.timing.dequeued : NaN)),
            driverToDecode: stats(frames.map(f => validDriver(f) ? f.decoded + best.offset - f.timing.driver : NaN)),
        },
        timingFlagValues: [...new Set(frames.filter(f => f.timing).map(f => f.timing.flags))],
    };
}

if (!process.argv.slice(2).length) throw new Error('Usage: node summarize-flow.cjs raw.json[.gz] ...');
const runs = process.argv.slice(2).map(summarize);
process.stdout.write(JSON.stringify({
    schemaVersion: 1, totalMeasuredSeconds: runs.reduce((sum, run) => sum + run.duration, 0),
    latencyBoundary: 'SHM publication to img.decode completion; not optical end-to-end',
    driverBoundary: 'V4L2-reported timestamp to img.decode completion; driver accuracy not externally verified',
    clockMethod: 'Per-run minimum RTT; online age samples use rolling 30-second minimum RTT',
    runs,
}, null, 2) + '\n');
