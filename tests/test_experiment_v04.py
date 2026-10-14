import asyncio
import unittest

from aiohttp import ClientSession, WSMsgType, web

from experiment_v04.pieces import pack_piece, unpack_piece
from experiment_v04.policy import TaskFreshnessEncoder
from experiment_v04.server import application
from semantic_video.model import DEFAULT_REGIONS
from semantic_video.source import FixtureSource, mono_ms


class PieceAndPolicy(unittest.TestCase):
    def test_piece_round_trip_and_corruption(self):
        image = b"\xff\xd8\xff\xd9"
        packet = pack_piece({"kind": "base", "id": 1}, image)
        self.assertEqual(unpack_piece(packet), ({"kind": "base", "id": 1}, image))
        with self.assertRaises(ValueError):
            unpack_piece(b"NOPE" + packet[4:])
        with self.assertRaises(ValueError):
            pack_piece({"kind": "base"}, b"not jpeg")

    def test_only_confirmed_patch_reduces_task_debt(self):
        encoder = TaskFreshnessEncoder(DEFAULT_REGIONS, 2400, 20)
        frame = FixtureSource(7).at(1, mono_ms())
        first = encoder.priorities(frame.image, mono_ms())
        self.assertEqual({item[1].name for item in first}, {"process-A", "process-B"})
        encoder.visible(None, 5000, 20)
        self.assertFalse(encoder.refresh)
        encoder.visible("process-A", 5000, 20, 2000)
        self.assertIn("process-A", encoder.refresh)
        self.assertNotIn("process-B", encoder.refresh)
        self.assertEqual(encoder.last_piece_bytes["process-A"], 2000)
        packet, metadata = encoder.encode(frame)
        self.assertLessEqual(len(metadata["parts"]), 2)
        self.assertTrue(packet)


class ExperimentServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runner = web.AppRunner(application())
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.url = f"http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}"
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_progressive_base_first_and_next_frame_after_ack(self):
        async with self.client.get(self.url + "/api/status") as response:
            self.assertEqual((await response.json())["transport"], "websocket-tcp")
        async with self.client.ws_connect(self.url + "/stream") as ws:
            await ws.send_json({"type": "start", "fps": 20, "kbps": 2400})
            ready = await asyncio.wait_for(ws.receive_json(), 5)
            self.assertEqual(ready["type"], "ready")
            first, _ = unpack_piece((await asyncio.wait_for(ws.receive(), 3)).data)
            self.assertEqual(first["kind"], "base")
            self.assertEqual(first["rect"], [0, 0, 1280, 720])
            if first["patch_name"]:
                patch, _ = unpack_piece((await asyncio.wait_for(ws.receive(), 3)).data)
                self.assertEqual((patch["kind"], patch["id"], patch["name"]),
                                 ("patch", first["id"], first["patch_name"]))
            await ws.send_json({"type": "ack", "id": first["id"], "base": True,
                                "patch": bool(first["patch_name"]), "wait_ms": 5})
            second, _ = unpack_piece((await asyncio.wait_for(ws.receive(), 3)).data)
            self.assertEqual(second["kind"], "base")
            self.assertGreater(second["id"], first["id"])

    async def test_false_patch_confirmation_rejected(self):
        async with self.client.ws_connect(self.url + "/stream") as ws:
            await ws.send_json({"type": "start"})
            await asyncio.wait_for(ws.receive_json(), 5)
            meta, _ = unpack_piece((await asyncio.wait_for(ws.receive(), 5)).data)
            if meta["patch_name"]:
                await asyncio.wait_for(ws.receive(), 5)
            await ws.send_json({"type": "ack", "id": meta["id"], "base": False,
                                "patch": True, "wait_ms": 0})
            response = await asyncio.wait_for(ws.receive(), 5)
            self.assertEqual((response.type, response.data), (WSMsgType.CLOSE, 1008))


if __name__ == "__main__":
    unittest.main()
