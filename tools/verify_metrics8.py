"""Short numerical smoke check. No media payloads are persisted or printed."""
import argparse
import asyncio
import json
import struct

from aiohttp import ClientSession, WSMsgType


async def verify(base):
    async with ClientSession() as client:
        async with client.get(base + "/api/status") as response:
            response.raise_for_status()
            status = await response.json()
        if status["sessions"]:
            print(json.dumps({"skipped": "Existing viewer retained"}))
            return
        async with client.get(base + "/telemetry.js") as response:
            response.raise_for_status()
            assert "mobile-metrics-8.1" in await response.text()
        async with client.get(base) as response:
            response.raise_for_status()
            assert 'id="test-network"' in await response.text()
        async with client.ws_connect(base + "/stream") as ws:
            await ws.send_json({"type": "start", "codec": "h264", "kbps": 2400, "fps": 20, "semantic": True})
            ready = await asyncio.wait_for(ws.receive_json(), 5)
            assert ready["implementation"]["app_version"] == "v0.7"
            assert ready["config"]["kbps"] == 2400
            count = 0
            while count < 12:
                packet = await asyncio.wait_for(ws.receive(), 5)
                assert packet.type == WSMsgType.BINARY
                size = struct.unpack_from("!I", packet.data, 4)[0]
                meta = json.loads(packet.data[8:8+size])
                if meta["kind"] != "frame":
                    continue
                assert (meta["width"], meta["height"]) == (1280, 720)
                assert meta["server_sent_ms"] >= meta["captured_ms"]
                assert meta["window"] == 8
                await ws.send_json({"type": "ack", "seq": meta["seq"]})
                count += 1
            print(json.dumps({"frames": count, "source": ready["source"], "resolution": [1280, 720],
                              "window": 8, "config": ready["config"],
                              "implementation": ready["implementation"], "media_saved": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    args = parser.parse_args()
    asyncio.run(verify(args.url.rstrip("/")))
