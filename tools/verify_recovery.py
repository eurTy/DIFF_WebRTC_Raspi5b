"""Pi read-only shared input: same-socket delayed ACK and numeric evidence only."""
import asyncio
import io
import json
import struct
import zlib

import av
from aiohttp import ClientSession, WSMsgType


async def main():
    url = "http://127.0.0.1:8091"
    async with ClientSession() as client:
        async with client.get(url+"/api/status") as response:
            if (await response.json())["sessions"]:
                print(json.dumps({"skipped": "Existing viewer retained"}))
                return
        chunks, count, generation = [], 0, 0
        async with client.ws_connect(url+"/stream") as ws:
            await ws.send_json({"type": "start", "codec": "h264", "kbps": 2400, "fps": 20,
                                "client_session_id": "pi-recovery-smoke:1"})
            ready = await ws.receive_json()
            first = True
            async with asyncio.timeout(18):
                while count < 16:
                    incoming = await ws.receive()
                    if incoming.type == WSMsgType.TEXT:
                        value = json.loads(incoming.data)
                        assert value["type"] == "reset"
                        generation = value["generation"]
                        chunks, count = [], 0
                        continue
                    assert incoming.type == WSMsgType.BINARY
                    packet = incoming.data
                    size = struct.unpack_from("!I", packet, 4)[0]
                    meta, payload = json.loads(packet[8:8+size]), packet[8+size:]
                    assert meta["payload_bytes"] == len(payload)
                    assert meta["payload_crc32"] == zlib.crc32(payload)
                    if generation:
                        chunks.append(payload)
                    if meta["kind"] == "init":
                        continue
                    if first:
                        first = False
                        await asyncio.sleep(1.6)
                    await ws.send_json({"type": "ack", "seq": meta["seq"], "rx_bytes": len(payload), "rx_crc32": zlib.crc32(payload)})
                    if generation:
                        if count == 0:
                            assert meta["key"]
                        count += 1
            with av.open(io.BytesIO(b"".join(chunks))) as media:
                frames = list(media.decode(video=0))
                assert len(frames) == 16
                assert all((f.width, f.height) == (1280, 720) for f in frames)
        await asyncio.sleep(.2)
        async with client.get(url+"/api/diagnostics") as response:
            trace = await response.json()
        rows = [e for e in trace["events"] if e["server_session_id"] == ready["server_session_id"]]
        assert any(e["event"] == "media_reset" for e in rows)
        assert not any(e["event"] == "abort" for e in rows)
        print(json.dumps({"scope": "Pi loopback; not iPhone 4G latency", "ack_delay_ms": 1600,
                          "same_socket_reset": True, "decoded_after_reset": 16, "resolution": [1280, 720],
                          "server_session_id": ready["server_session_id"], "media_saved": False,
                          "source_sha256": ready["implementation"]["source_sha256"]}))


if __name__ == "__main__":
    asyncio.run(main())
