"""Read-only numeric smoke. No camera payload is persisted or printed."""
import asyncio
import io
import json
import struct
import zlib

import av
from aiohttp import ClientSession, WSMsgType
import cv2
import numpy as np


async def main():
    url = 'http://127.0.0.1:8091'
    results = []
    async with ClientSession() as client:
        for codec in ('h264', 'jpeg'):
            async with client.get(url+'/api/status') as response:
                if (await response.json())['sessions']:
                    print(json.dumps({'skipped': 'Existing viewer retained', 'results': results}))
                    return
            chunks, count, dimensions = [], 0, set()
            async with client.ws_connect(url+'/stream') as ws:
                await ws.send_json({'type': 'start', 'codec': codec, 'kbps': 2400, 'fps': 20, 'semantic': True})
                ready = await asyncio.wait_for(ws.receive_json(), 5)
                assert ready['integrity'] == 'crc32-v1'
                assert ready['delivery_policy']['max_frames'] == 4
                assert ready['delivery_policy']['byte_limit'] == 54000
                while count < 16:
                    incoming = await asyncio.wait_for(ws.receive(), 5)
                    assert incoming.type == WSMsgType.BINARY
                    packet = incoming.data
                    size = struct.unpack_from('!I', packet, 4)[0]
                    meta, payload = json.loads(packet[8:8+size]), packet[8+size:]
                    assert meta['payload_bytes'] == len(payload)
                    assert meta['payload_crc32'] == zlib.crc32(payload)
                    if codec == 'h264':
                        chunks.append(payload)
                    if meta['kind'] == 'init':
                        continue
                    if codec == 'jpeg':
                        assert sum(meta['lengths']) == len(payload)
                        offset = 0
                        for index, length in enumerate(meta['lengths']):
                            image = cv2.imdecode(np.frombuffer(payload[offset:offset+length], dtype=np.uint8), cv2.IMREAD_COLOR)
                            assert image is not None
                            expected = (meta['height'], meta['width']) if index == 0 else tuple(reversed(meta['parts'][index]['rect'][2:]))
                            assert image.shape[:2] == expected
                            offset += length
                    dimensions.add((meta['width'], meta['height']))
                    count += 1
                    await ws.send_json({'type': 'ack', 'seq': meta['seq']})
                if codec == 'h264':
                    with av.open(io.BytesIO(b''.join(chunks))) as media:
                        decoded = list(media.decode(video=0))
                        assert len(decoded) == count
                        assert all((frame.width, frame.height) == (1280, 720) for frame in decoded)
                results.append({'codec': codec, 'frames': count, 'decoded': True, 'dimensions': sorted(dimensions),
                                'integrity': ready['integrity'], 'policy': ready['delivery_policy'],
                                'source': ready['source'], 'source_sha256': ready['implementation']['source_sha256'],
                                'media_saved': False})
            await asyncio.sleep(.2)
    print(json.dumps({'scope': 'Pi loopback, not iPhone 4G latency', 'results': results}))


if __name__ == '__main__':
    asyncio.run(main())
