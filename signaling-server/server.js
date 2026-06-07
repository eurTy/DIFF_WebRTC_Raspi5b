const fs = require('fs');
const http = require('http');
const path = require('path');
const { URL } = require('url');
const WebSocket = require('ws');

const PORT = Number(process.env.PORT || 8080);
const PUBLIC_DIR = path.join(__dirname, 'public');
const CLIENT_VERSION = '20260606b';

const MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
};

const server = http.createServer((req, res) => {
    const urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
    const requestPath = urlPath === '/' ? '/index.html' : urlPath;
    const filePath = path.normalize(path.join(PUBLIC_DIR, requestPath));

    if (!filePath.startsWith(PUBLIC_DIR)) {
        res.writeHead(403);
        res.end('Forbidden');
        return;
    }

    fs.readFile(filePath, (err, data) => {
        if (err) {
            res.writeHead(404);
            res.end('Not found');
            return;
        }

        res.writeHead(200, {
            'Content-Type': MIME_TYPES[path.extname(filePath)] || 'application/octet-stream',
            'Cache-Control': 'no-store',
        });
        res.end(data);
    });
});

const wss = new WebSocket.Server({ server });
let gatewaySocket = null;
let lastOffer = null;
let lastGatewayCandidates = [];
let viewerSocket = null;

function getRole(req) {
    try {
        const url = new URL(req.url || '/', `ws://${req.headers.host || '127.0.0.1'}`);
        return (url.searchParams.get('role') || 'viewer').toLowerCase();
    } catch (error) {
        console.error('[Signaling] Failed to parse role:', error.message);
        return 'viewer';
    }
}

function safeSend(ws, message) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(message);
        return true;
    }
    return false;
}

function broadcastToViewers(message) {
    safeSend(viewerSocket, message);
}

function normalizePeerIp(address) {
    if (!address) {
        return '';
    }
    if (address.startsWith('::ffff:')) {
        return address.slice('::ffff:'.length);
    }
    return address;
}

function normalizeViewerCandidate(message, ws) {
    try {
        const parsed = JSON.parse(message);
        if (!parsed.candidate || !parsed.candidate.includes('.local')) {
            return message;
        }

        const peerIp = normalizePeerIp(ws.peerAddress);
        if (!/^\d+\.\d+\.\d+\.\d+$/.test(peerIp)) {
            return message;
        }

        const parts = parsed.candidate.trim().split(/\s+/);
        if (parts.length >= 6 && parts[4].endsWith('.local')) {
            const oldAddress = parts[4];
            parts[4] = peerIp;
            parsed.candidate = parts.join(' ');
            console.log(`[Signaling] Rewrote viewer mDNS candidate ${oldAddress} -> ${peerIp}:${parts[5]}`);
            return JSON.stringify(parsed);
        }
    } catch (error) {
        console.error('[Signaling] Failed to normalize viewer candidate:', error.message);
    }
    return message;
}


function rememberGatewayMessage(message) {
    try {
        const parsed = JSON.parse(message);
        if (parsed.type === 'offer' && parsed.sdp) {
            lastOffer = message;
            lastGatewayCandidates = [];
        } else if (parsed.candidate) {
            lastGatewayCandidates.push(message);
            if (lastGatewayCandidates.length > 32) {
                lastGatewayCandidates.shift();
            }
        }
    } catch (error) {
        console.error('[Signaling] Invalid gateway JSON:', error.message);
    }
}

function removeSocket(ws) {
    if (ws === gatewaySocket) {
        gatewaySocket = null;
        lastOffer = null;
        lastGatewayCandidates = [];
    }
    if (ws === viewerSocket) {
        viewerSocket = null;
    }
}

console.log(`Signaling server running on http://0.0.0.0:${PORT}`);
console.log(`WebSocket signaling running on ws://0.0.0.0:${PORT}`);

wss.on('connection', (ws, req) => {
    const role = getRole(req);
    const clientIp = req.socket.remoteAddress;
    ws.role = role;
    ws.peerAddress = clientIp;

    console.log(`[Signaling] ${role} connected: ${clientIp}`);

    if (role !== 'gateway') {
        try {
            const url = new URL(req.url || '/', `ws://${req.headers.host || '127.0.0.1'}`);
            const version = url.searchParams.get('v') || '';
            if (version !== CLIENT_VERSION) {
                safeSend(ws, JSON.stringify({ type: 'server_warning', message: 'client page outdated, hard refresh required' }));
                ws.close(4001, 'client page outdated');
                return;
            }
        } catch (error) {
            ws.close(4001, 'invalid viewer url');
            return;
        }
    }

    if (role === 'gateway') {
        if (gatewaySocket && gatewaySocket !== ws) {
            gatewaySocket.close(4000, 'Gateway replaced');
        }
        gatewaySocket = ws;
    } else {
        if (viewerSocket && viewerSocket !== ws) {
            viewerSocket.close(4002, 'viewer replaced');
        }
        viewerSocket = ws;
        if (lastOffer) {
            safeSend(ws, lastOffer);
            for (const candidate of lastGatewayCandidates) {
                safeSend(ws, candidate);
            }
        }
    }

    ws.on('message', (raw) => {
        const message = Buffer.isBuffer(raw) ? raw.toString('utf8') : String(raw);
        const preview = message.substring(0, 100);
        console.log(`[Signaling] ${role} -> ${preview}`);

        if (ws.role === 'gateway') {
            rememberGatewayMessage(message);
            broadcastToViewers(message);
            return;
        }

        if (ws !== viewerSocket) {
            return;
        }
        const forwardMessage = normalizeViewerCandidate(message, ws);
        if (!safeSend(gatewaySocket, forwardMessage)) {
            safeSend(ws, JSON.stringify({ type: 'server_warning', message: 'gateway offline' }));
        }
    });

    ws.on('close', (code, reason) => {
        const reasonText = reason ? reason.toString() : '';
        console.log(`[Signaling] ${role} disconnected: ${clientIp}, code=${code}, reason=${reasonText}`);
        removeSocket(ws);
    });

    ws.on('error', (error) => {
        console.error(`[Signaling] ${role} error:`, error.message);
        removeSocket(ws);
    });
});

server.listen(PORT);
