const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { LatestFrameSource, MAX_FRAME_BYTES, TIMING_OFFSET } = require('../latest-source');
const { parseFragment } = require('../public/video-frames');

async function fixture(t) {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'pi-latest-test-'));
    const filename = path.join(dir, 'video_shm');
    const source = new LatestFrameSource(filename);
    t.after(async () => { await source.close(); await fs.rm(dir, { recursive: true }); });
    const jpeg = Buffer.alloc(90000, 42);
    jpeg[0] = 0xff; jpeg[1] = 0xd8; jpeg[jpeg.length - 2] = 0xff; jpeg[jpeg.length - 1] = 0xd9;
    const header = Buffer.alloc(48);
    header.writeUInt32LE(42, 0); header.writeUInt32LE(jpeg.length, 12);
    header.writeUInt32LE(2, 16); header.writeBigUInt64LE(5000000000n, 40);
    const write = () => fs.writeFile(filename, Buffer.concat([header, jpeg]));
    await write();
    return { source, filename, header, jpeg, write };
}

test('reads an unchanged native JPEG and emits one complete VPF2 message', async t => {
    const { source, jpeg } = await fixture(t);
    const frame = await source.readLatest();
    const p = frame.packet;
    const parsed = parseFragment(p.buffer.slice(p.byteOffset, p.byteOffset + p.byteLength));
    assert.equal(parsed.id, 42); assert.equal(parsed.total, 1);
    assert.equal(parsed.published, 5000000000);
    assert.deepEqual(Buffer.from(parsed.payload), jpeg);
    assert.equal(await source.readLatest(frame.published), null);
});

test('rejects in-progress, skipped, oversized and truncated SHM frames', async t => {
    const { source, header, filename, write } = await fixture(t);
    header.writeUInt32LE(3, 16); await write(); assert.equal(await source.readLatest(), null);
    header.writeUInt32LE(2, 16); header.writeUInt32LE(1, 24);
    await write(); assert.equal(await source.readLatest(), null);
    header.writeUInt32LE(0, 24); header.writeUInt32LE(MAX_FRAME_BYTES + 1, 12);
    await write(); assert.equal(await source.readLatest(), null);
    header.writeUInt32LE(90000, 12);
    await fs.writeFile(filename, header); assert.equal(await source.readLatest(), null);
});

test('rejects a frame overwritten while the JPEG is being copied', async t => {
    const { source, header } = await fixture(t);
    const file = await source.handle();
    const read = file.read.bind(file);
    file.read = async (...args) => {
        const result = await read(...args);
        if (args[3] === 48) {
            const writer = await fs.open(source.filename, 'r+');
            header.writeUInt32LE(4, 16);
            await writer.write(header, 0, header.length, 0); await writer.close();
        }
        return result;
    };
    assert.equal(await source.readLatest(), null);
});

test('retries opening a source created after the service starts', async t => {
    const { source, filename, write } = await fixture(t);
    await fs.unlink(filename);
    await assert.rejects(source.readLatest(), { code: 'ENOENT' });
    await write(); assert.ok(await source.readLatest());
});

test('VPF3 preserves JPEG and validates timing sidecar identity', async t => {
    const { source, header, jpeg, filename, write } = await fixture(t);
    header.writeUInt32LE(2, 24); await write();
    const timing = Buffer.alloc(64);
    timing.writeUInt32LE(0x314d4954, 0); timing.writeUInt32LE(42, 4);
    timing.writeUInt32LE(60, 8); timing.writeUInt32LE(0x2000, 12);
    timing.writeBigUInt64LE(4999999970000n, 16); timing.writeBigUInt64LE(4999999999000n, 24);
    timing.writeBigUInt64LE(5000000000000n, 32);
    const file = await fs.open(filename, 'r+');
    try {
        await file.write(timing, 0, 64, TIMING_OFFSET);
        const { packet } = await source.readLatest();
        const parsed = parseFragment(packet.buffer.slice(packet.byteOffset, packet.byteOffset + packet.byteLength));
        assert.equal(parsed.timing.driver, 4999999970);
        assert.equal(parsed.timing.sequence, 60);
        assert.deepEqual(Buffer.from(parsed.payload), jpeg);
        timing.writeUInt32LE(43, 4); await file.write(timing, 0, 64, TIMING_OFFSET);
        assert.equal(await source.readLatest(), null);
    } finally { await file.close(); }
});
