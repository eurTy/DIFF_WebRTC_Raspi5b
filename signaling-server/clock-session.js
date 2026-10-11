const { steadyMilliseconds } = require('./latest-session');

function attachClockSession(ws, now = steadyMilliseconds) {
    let windowStart = now(), count = 0;
    ws.on('message', (raw, binary) => {
        const received = now();
        if (received - windowStart >= 1000) { windowStart = received; count = 0; }
        if (binary || Buffer.byteLength(raw) > 512 || ++count > 16 || ws.bufferedAmount > 8192) {
            ws.close(1008, 'clock limits'); return;
        }
        try {
            const message = JSON.parse(raw.toString());
            if (message?.type !== 'latency_clock_request' || !Number.isSafeInteger(message.request_id) || message.request_id < 0) throw new Error();
            ws.send(JSON.stringify({ type: 'latency_clock_response', request_id: message.request_id,
                server_receive_ms: received, server_send_ms: now() }));
        } catch { ws.close(1008, 'invalid clock request'); }
    });
    ws.on('error', () => {});
}

module.exports = { attachClockSession };
