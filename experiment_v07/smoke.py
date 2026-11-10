"""Bounded local decode probe; never saves or prints camera frames."""
import argparse
import asyncio
import io
import json
import struct
import time

from aiohttp import ClientSession, WSMsgType
import av


def unpack(packet):
    if packet[:4] != b"SVP5":
        raise ValueError("Unexpected media envelope")
    size = struct.unpack_from("!I", packet, 4)[0]
    if not 2 <= size <= 16384 or size+8 >= len(packet):
        raise ValueError("Invalid media envelope")
    return json.loads(packet[8:8+size]), packet[8+size:]


async def run(url, count):
    async with ClientSession() as client:
        async with client.get(url+"/api/status") as response:
            state = await response.json()
        if state["sessions"]:
            print(json.dumps({"skipped": "Viewer occupied; did not interrupt"}))
            return
        async with client.ws_connect(url+"/stream", heartbeat=10) as ws:
            await ws.send_json({"type": "start", "codec": "h264", "kbps": 2400, "fps": 20,
                                "semantic": True})
            ready = await ws.receive_json(timeout=5)
            if ready.get("codec") != "h264":
                raise ValueError("H264 not selected")
            init = await ws.receive(timeout=5)
            meta, data = unpack(init.data)
            if meta.get("kind") != "init":
                raise ValueError("Missing MP4 init")
            stream = bytearray(data)
            received = []
            for sequence in range(count):
                item = await ws.receive(timeout=5)
                if item.type != WSMsgType.BINARY:
                    raise ValueError("Media stream ended")
                meta, data = unpack(item.data)
                if meta.get("seq") != sequence or meta.get("width") != 1280 or meta.get("height") != 720:
                    raise ValueError("Invalid frame metadata")
                stream.extend(data)
                received.append(time.monotonic())
                await ws.send_json({"type": "ack", "seq": sequence})
        with av.open(io.BytesIO(stream)) as container:
            decoded = list(container.decode(video=0))
        result = {"source": ready.get("source"), "fragments": len(received),
                  "decoded_frames": len(decoded), "resolution": [decoded[-1].width, decoded[-1].height],
                  "fragment_fps": (len(received)-1)/(received[-1]-received[0]),
                  "scope": "Same-host shared-input decode, not phone latency"}
        print(json.dumps(result))
        if len(decoded) < count-1 or result["resolution"] != [1280, 720]:
            raise ValueError("Decode or resolution check failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--count", type=int, default=35)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.count))
