const steadyMilliseconds = () => Number(process.hrtime.bigint()) / 1e6;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function attachLatestSession(ws, source, { now = steadyMilliseconds, waitMs = 1000, pollMs = 3 } = {}) {
    let closed = false;
    let busy = false;
    let lastSent = 0, window = 1, started = false;
    const inflight = new Set();

    const sendJson = value => {
        if (!closed && ws.readyState === 1 && ws.bufferedAmount < 65536) ws.send(JSON.stringify(value));
    };

    async function pump() {
        if (closed || busy) return;
        busy = true;
        try {
            let deadline = now() + waitMs;
            while (!closed && ws.readyState === 1 && inflight.size < window) {
                const frame = await source.readLatest(lastSent);
                if (closed || ws.readyState !== 1) return;
                if (frame) {
                    lastSent = frame.published;
                    inflight.add(lastSent);
                    if (frame.packet.length >= 64 && frame.packet.readUInt32BE(0) === 0x56504633)
                        frame.packet.writeBigUInt64BE(BigInt(Math.round(now() * 1000)), 48);
                    await new Promise((resolve, reject) => {
                        ws.send(frame.packet, { binary: true, compress: false }, error => error ? reject(error) : resolve());
                    });
                    deadline = now() + waitMs;
                } else {
                    if (now() >= deadline) { sendJson({ type: 'frame_wait' }); return; }
                    await sleep(pollMs);
                }
            }
        } catch (error) {
            sendJson({ type: 'frame_error', message: 'camera source unavailable' });
        } finally {
            busy = false;
        }
    }

    ws.on('message', (raw, isBinary) => {
        const received = now();
        if (closed) return;
        if (isBinary || Buffer.byteLength(raw) > 1024) { ws.close(1008, 'invalid control message'); return; }
        try {
            const message = JSON.parse(raw.toString());
            if (!message || typeof message !== 'object') throw new Error('invalid message');
            if (message.type === 'latency_clock_request' && Number.isSafeInteger(message.request_id)) {
                sendJson({ type: 'latency_clock_response', request_id: message.request_id,
                    server_receive_ms: received, server_send_ms: now() });
            } else if (message.type === 'frame_request' && Number.isSafeInteger(message.after) && message.after >= 0) {
                if (!started) {
                    if (message.after !== 0 || ![1, 2].includes(message.window ?? 1)) throw new Error('invalid initial credit');
                    window = message.window ?? 1;
                    started = true;
                } else if (!inflight.delete(message.after) && !(inflight.size === 0 && message.after === lastSent)) {
                    return; // Duplicate or forged acknowledgments never create credit.
                }
                void pump();
            } else {
                ws.close(1008, 'invalid control message');
            }
        } catch (error) {
            ws.close(1008, 'invalid control message');
        }
    });
    ws.on('close', () => { closed = true; inflight.clear(); });
    ws.on('error', () => { closed = true; inflight.clear(); });
}

module.exports = { attachLatestSession, steadyMilliseconds };
