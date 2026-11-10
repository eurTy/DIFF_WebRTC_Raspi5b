import asyncio
import json
import struct
import unittest
import zlib

from aiohttp import ClientSession, WSMsgType, web

from experiment_v07.server import application


def unpack(packet):
    assert packet[:4] == b"SVP5"
    size = struct.unpack_from("!I", packet, 4)[0]
    return json.loads(packet[8:8+size]), packet[8+size:]


class H264ServerTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_h264_bounded_pipeline_and_ordered_ack(self):
        async with self.client.ws_connect(self.url+"/stream") as ws:
            await ws.send_json({"type": "start", "codec": "h264", "kbps": 2400, "fps": 20})
            ready = await ws.receive_json()
            self.assertIn("avc1.", ready["mime"])
            self.assertEqual(ready["implementation"]["app_version"], "v0.7")
            self.assertEqual(len(ready["implementation"]["source_sha256"]), 64)
            self.assertEqual(ready["config"]["kbps"], 2400)
            first = await asyncio.wait_for(ws.receive(), 3)
            self.assertEqual(unpack(first.data)[0]["kind"], "init")
            frame = await asyncio.wait_for(ws.receive(), 3)
            meta, payload = unpack(frame.data)
            self.assertEqual((meta["width"], meta["height"]), (1280, 720))
            self.assertEqual(meta["window"], 4)
            self.assertGreaterEqual(meta["server_sent_ms"], meta["captured_ms"])
            self.assertTrue(payload)
            self.assertEqual(meta["payload_crc32"], zlib.crc32(payload))
            metas = [meta]
            await asyncio.sleep(.25)
            async with self.client.get(self.url+"/api/status") as response:
                state = await response.json()
            count = state["delivery"]["pending_frames"]
            self.assertLessEqual(count, 4)
            self.assertLessEqual(state["delivery"]["pending_bytes"], state["delivery"]["byte_limit"])
            for _ in range(count-1):
                item = await asyncio.wait_for(ws.receive(), 3)
                next_meta, _ = unpack(item.data)
                metas.append(next_meta)
            self.assertEqual([m["seq"] for m in metas], list(range(count)))
            pending = asyncio.create_task(ws.receive())
            await asyncio.sleep(.2)
            self.assertFalse(pending.done())
            await ws.send_json({"type": "display", "age_ms": 90, "fps": 15})
            for item in metas:
                await ws.send_json({"type": "ack", "seq": item["seq"]})
            next_frame = await asyncio.wait_for(pending, 3)
            next_meta, _ = unpack(next_frame.data)
            self.assertEqual(next_meta["seq"], count)
            async with self.client.get(self.url+"/api/status") as response:
                state = await response.json()
                self.assertEqual(state["client_report"]["age_ms"], 90)
                self.assertLessEqual(state["events"]["max_in_flight"], 4)
        await asyncio.sleep(.1)
        async with self.client.get(self.url+"/api/status") as response:
            state = await response.json()
            self.assertEqual(state["events"].get("invalid", 0), 0)

    async def test_jpeg_comparison_uses_v05_packet_schema(self):
        async with self.client.ws_connect(self.url+"/stream") as ws:
            await ws.send_json({"type": "start", "codec": "jpeg", "kbps": 2400, "fps": 20})
            ready = await ws.receive_json()
            self.assertEqual(ready["codec"], "jpeg")
            frame = await asyncio.wait_for(ws.receive(), 3)
            meta, payload = unpack(frame.data)
            self.assertEqual(meta["kind"], "frame")
            self.assertEqual(meta["width"]*9//16, meta["height"])
            self.assertEqual(sum(meta["lengths"]), len(payload))
            self.assertEqual(meta["payload_bytes"], len(payload))
            self.assertEqual(meta["payload_crc32"], zlib.crc32(payload))
            await ws.send_json({"type": "ack", "seq": meta["seq"], "outcome": "integrity_rejected"})
            next_meta, _ = unpack((await asyncio.wait_for(ws.receive(), 3)).data)
            self.assertEqual(next_meta["seq"], meta["seq"]+1)
            async with self.client.get(self.url+"/api/status") as response:
                state = await response.json()
            self.assertEqual(state["events"]["integrity_rejected"], 1)

    async def test_no_feedback_aborts_bounded_backlog_and_releases_viewer(self):
        async with self.client.ws_connect(self.url+"/stream") as ws:
            await ws.send_json({"type": "start", "codec": "jpeg", "kbps": 2400, "fps": 20})
            await ws.receive_json()
            async with asyncio.timeout(6):
                while (await ws.receive()).type == WSMsgType.BINARY:
                    pass
        await asyncio.sleep(.05)
        async with self.client.get(self.url+"/api/status") as response:
            state = await response.json()
        self.assertEqual(state["events"]["ack_deadline"], 1)
        self.assertEqual(state["sessions"], 0)
        self.assertLessEqual(state["events"]["max_in_flight"], 4)
        async with self.client.ws_connect(self.url+"/stream") as recovered:
            await recovered.send_json({"type": "start", "codec": "jpeg", "kbps": 2400, "fps": 20})
            self.assertEqual((await recovered.receive_json())["type"], "ready")

    async def test_delayed_ack_resets_h264_on_same_connection_with_correlated_trace(self):
        async with self.client.ws_connect(self.url+"/stream") as ws:
            await ws.send_json({"type": "start", "codec": "h264", "kbps": 2400, "fps": 20,
                                "client_session_id": "test-run:1"})
            ready = await ws.receive_json()
            server_id = ready["server_session_id"]
            self.assertEqual(unpack((await ws.receive()).data)[0]["kind"], "init")
            first, payload = unpack((await ws.receive()).data)
            await asyncio.sleep(1.6)
            await ws.send_json({"type": "ack", "seq": first["seq"], "rx_bytes": len(payload), "rx_crc32": zlib.crc32(payload)})
            reset = False
            async with asyncio.timeout(5):
                while True:
                    message = await ws.receive()
                    if message.type == WSMsgType.TEXT:
                        self.assertEqual(json.loads(message.data)["type"], "reset")
                        reset = True
                        continue
                    self.assertEqual(message.type, WSMsgType.BINARY)
                    meta, payload = unpack(message.data)
                    if meta["kind"] == "init":
                        continue
                    await ws.send_json({"type": "ack", "seq": meta["seq"]})
                    if reset and meta["generation"] == 1:
                        self.assertTrue(meta["key"])
                        break
            async with self.client.post(self.url+"/api/diagnostics", json={"server_session_id": server_id,
                                        "event": "integrity_rejected", "seq": first["seq"],
                                        "actual_bytes": len(payload)}) as response:
                self.assertEqual(response.status, 200)
        async with self.client.get(self.url+"/api/diagnostics") as response:
            trace = await response.json()
        self.assertEqual(len(trace["sessions"]), 1)
        self.assertEqual(trace["sessions"][0]["client_session_id"], "test-run:1")
        self.assertTrue(any(e["event"] == "media_reset" for e in trace["events"]))
        ack = next(e for e in trace["events"] if e["event"] == "ack" and e["seq"] == first["seq"])
        self.assertEqual(ack["expected_bytes"], ack["rx_bytes"])
        self.assertEqual(ack["expected_crc32"], ack["rx_crc32"])
        self.assertFalse(any(e["event"] == "abort" for e in trace["events"]))


if __name__ == "__main__":
    unittest.main()
