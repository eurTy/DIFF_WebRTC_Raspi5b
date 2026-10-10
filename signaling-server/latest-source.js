const fs = require('node:fs/promises');

const HEADER_BYTES = 48;
const MAX_FRAME_BYTES = 8 * 1024 * 1024;

class LatestFrameSource {
    constructor(filename) {
        this.filename = filename;
        this.opening = null;
    }

    async handle() {
        if (!this.opening) {
            this.opening = fs.open(this.filename, 'r').catch(error => {
                this.opening = null;
                throw error;
            });
        }
        return this.opening;
    }

    async readLatest(after = 0) {
        const file = await this.handle();
        const header = Buffer.alloc(HEADER_BYTES);
        if ((await file.read(header, 0, header.length, 0)).bytesRead !== HEADER_BYTES) return null;
        const sequence = header.readUInt32LE(16);
        const size = header.readUInt32LE(12);
        const published = Number(header.readBigUInt64LE(40));
        if ((sequence & 1) || (header.readUInt32LE(24) & 1) || size < 4 || size > MAX_FRAME_BYTES ||
            !Number.isSafeInteger(published) || published <= after) return null;

        // Reuse VPF2 framing, but carry one complete native JPEG per WebSocket message.
        const packet = Buffer.allocUnsafe(24 + size);
        if ((await file.read(packet, 24, size, HEADER_BYTES)).bytesRead !== size) return null;
        const verified = Buffer.alloc(HEADER_BYTES);
        if ((await file.read(verified, 0, verified.length, 0)).bytesRead !== HEADER_BYTES ||
            !header.equals(verified) || packet[24] !== 0xff || packet[25] !== 0xd8) return null;
        packet.writeUInt32BE(0x56504632, 0);
        packet.writeUInt32BE(header.readUInt32LE(0), 4);
        packet.writeUInt16BE(0, 8);
        packet.writeUInt16BE(1, 10);
        packet.writeUInt32BE(size, 12);
        packet.writeBigUInt64BE(BigInt(published), 16);
        return { packet, published };
    }

    async close() {
        const opening = this.opening;
        this.opening = null;
        if (opening) await (await opening).close();
    }
}

module.exports = { LatestFrameSource, HEADER_BYTES, MAX_FRAME_BYTES };
