const http = require('node:http');

function observerProxy(req, res, pathname, search, port = 8092) {
    if (!pathname.startsWith('/observe/')) return false;
    const allowed = /^\/observe\/(status|history|events|events\/\d+|snapshot\/\d+)$/.test(pathname);
    if (req.method !== 'GET' || !allowed || (search && !/^\?limit=\d{1,3}$/.test(search))) {
        res.writeHead(400); res.end('Invalid read-only observer request'); return true;
    }
    let done = false;
    const finishError = code => {
        if (done) return;
        done = true;
        if (!res.headersSent) res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        res.end(JSON.stringify({ error: 'observer unavailable', quality: 'OFFLINE' }));
    };
    const upstream = http.get({ host: '127.0.0.1', port, path: pathname.slice(8) + search, timeout: 1500 }, response => {
        const chunks = [];
        let size = 0;
        response.on('data', chunk => {
            size += chunk.length;
            if (size > 9 * 1024 * 1024) { upstream.destroy(); finishError(502); }
            else chunks.push(chunk);
        });
        response.on('error', () => finishError(502));
        response.on('end', () => {
            if (done) return;
            done = true;
            res.writeHead(response.statusCode, { 'Content-Type': response.headers['content-type'] || 'application/json',
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
            res.end(Buffer.concat(chunks));
        });
    });
    upstream.on('timeout', () => { upstream.destroy(); finishError(504); });
    upstream.on('error', () => finishError(502));
    res.on('close', () => { done = true; upstream.destroy(); });
    return true;
}

module.exports = { observerProxy };
