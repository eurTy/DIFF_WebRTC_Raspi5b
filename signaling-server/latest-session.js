const steadyMilliseconds = () => Number(process.hrtime.bigint()) / 1e6;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function attachLatestSession(ws, source, { now = steadyMilliseconds, waitMs = 1000, pollMs = 3 } = {}) {
    let closed = false;
    let busy = false;
    let lastSent = 0;
    let pendingAfter = null;

    const sendJson = value => {
        if (!closed && ws.readyState === 1 && ws.bufferedAmount < 65536) ws.send(JSON.stringify(value));
    };

    async function pull(after) {
        if (closed || after !== lastSent) return;
        if (busy) { pendingAfter = after; return; }
        busy = true;
        try {
            const deadline = now() + waitMs;
            while (!closed && ws.readyState === 1) {
                const frame = await source.readLatest(after);
                if (closed || ws.readyState !== 1) return;
                if (frame) {
                    lastSent = frame.published;
                    await new Promise((resolve, reject) => {
                        ws.send(frame.packet, { binary: true, compress: false }, error => error ? reject(error) : resolve());
                    });
                    return;
                }
                if (now() >= deadline) { sendJson({ type: 'frame_wait' }); return; }
                await sleep(pollMs);
            }
        } catch (error) {
            sendJson({ type: 'frame_error', message: 'camera source unavailable' });
        } finally {
            busy = false;
            const pending = pendingAfter;
            pendingAfter = null;
            if (!closed && pending !== null && pending === lastSent) void pull(pending);
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
                void pull(message.after);
            } else {
                ws.close(1008, 'invalid control message');
            }
        } catch (error) {
            ws.close(1008, 'invalid control message');
        }
    });
    ws.on('close', () => { closed = true; pendingAfter = null; });
    ws.on('error', () => { closed = true; pendingAfter = null; });
}

module.exports = { attachLatestSession, steadyMilliseconds };
