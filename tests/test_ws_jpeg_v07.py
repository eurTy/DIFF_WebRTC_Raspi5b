import asyncio
import json
import struct
import unittest

import cv2
import numpy as np
from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError, web

from experiment_v07.server import application, encode, settings
from semantic_video.source import FixtureSource


def unpack(packet):
    assert packet[:4] == b"SVJ7"
    size = struct.unpack_from("!I", packet, 4)[0]
    return json.loads(packet[8:8+size]), packet[8+size:]


class JpegCodecTests(unittest.TestCase):
    def test_native_jpeg_decodes_and_preserves_metadata(self):
        frame = FixtureSource(7).at(1)
        packet, meta = encode(frame, 9)
        decoded_meta, image = unpack(packet)
        decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape[:2], (720, 1280))
        self.assertEqual(decoded_meta["id"], frame.identity)
        self.assertEqual(decoded_meta["seq"], 9)
        self.assertEqual(decoded_meta, meta)

    def test_settings_are_bounded(self):
        self.assertEqual(settings({"type": "start"}), (2400, 20))
        for value in ({"type": "start", "fps": True}, {"type": "start", "kbps": 0},
                      {"type": "start", "fps": 21}, {"type": "ack"}):
            with self.assertRaises(ValueError):
                settings(value)


class JpegServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runner = web.AppRunner(application())
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_one_in_flight_latest_full_frame(self):
        async with self.client.ws_connect(self.url+"/jpeg-probe") as ws:
            await ws.send_json({"type": "start", "fps": 20, "kbps": 2400})
            ready = await ws.receive_json()
            self.assertEqual((ready["width"], ready["height"]), (1280, 720))
            first = await asyncio.wait_for(ws.receive(), 3)
            self.assertEqual(first.type, WSMsgType.BINARY)
            meta, image = unpack(first.data)
            self.assertEqual((meta["width"], meta["height"]), (1280, 720))
            self.assertGreater(len(image), 1000)
            pending = asyncio.create_task(ws.receive())
            await asyncio.sleep(.15)
            self.assertFalse(pending.done())
            await ws.send_json({"type": "ack", "seq": meta["seq"]})
            second = await asyncio.wait_for(pending, 3)
            next_meta, _ = unpack(second.data)
            self.assertEqual(next_meta["seq"], meta["seq"]+1)
            self.assertGreater(next_meta["id"], meta["id"])
            async with self.client.get(self.url+"/api/status") as response:
                state = await response.json()
                self.assertEqual(state["events"]["max_in_flight"], 1)

    async def test_second_viewer_and_cross_origin_rejected(self):
        async with self.client.get(self.url+"/jpeg-probe", headers={"Origin": "http://untrusted.invalid"}) as response:
            self.assertEqual(response.status, 403)
        async with self.client.ws_connect(self.url+"/jpeg-probe"):
            with self.assertRaises(WSServerHandshakeError) as caught:
                await self.client.ws_connect(self.url+"/jpeg-probe")
            self.assertEqual(caught.exception.status, 503)

    async def test_invalid_ack_closes_session(self):
        async with self.client.ws_connect(self.url+"/jpeg-probe") as ws:
            await ws.send_json({"type": "start"})
            await ws.receive_json()
            await ws.receive()
            await ws.send_json({"type": "ack", "seq": 999})
            closed = await asyncio.wait_for(ws.receive(), 3)
            self.assertEqual((closed.type, closed.data), (WSMsgType.CLOSE, 1008))


if __name__ == "__main__":
    unittest.main()
