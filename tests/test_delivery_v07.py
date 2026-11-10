import asyncio
import json
import struct
import unittest
from unittest.mock import Mock
import zlib

from experiment_v07.delivery import SendWindow, checked_envelope
from experiment_v07.server import send_complete
from experiment_v07.diagnostics import TraceBook


class DeliveryTests(unittest.TestCase):
    def test_envelope_length_crc_and_jpeg_boundaries(self):
        payload = b'\xff\xd8test\xff\xd9'
        meta = {"parts": [{"rect": [0, 0, 1280, 720]}], "lengths": [len(payload)]}
        packet = checked_envelope(meta, payload)
        size = struct.unpack_from('!I', packet, 4)[0]
        header = json.loads(packet[8:8+size])
        self.assertEqual(packet[8+size:], payload)
        self.assertEqual(header['payload_bytes'], len(payload))
        self.assertEqual(header['payload_crc32'], zlib.crc32(payload))
        self.assertNotIn('payload_crc32', meta)
        for data in [payload[:-1], b'badheader', bytearray(payload)]:
            with self.assertRaises(ValueError):
                checked_envelope(meta, data)
        with self.assertRaises(ValueError):
            checked_envelope(dict(meta, lengths=[True]), payload)
        with self.assertRaises(ValueError):
            checked_envelope(dict(meta, parts=[{"rect": [0, 0, 1281, 720]}]), payload)

    def test_byte_limit_and_oversized_keyframe_alone(self):
        f = SendWindow(2400, 20)
        self.assertEqual(f.byte_limit, 54000)
        f.sent(0, 30000, 0, 0)
        self.assertFalse(f.ready(100, 30000))
        self.assertTrue(f.ready(100, 10000))
        f.acknowledge(0, 110)
        self.assertTrue(f.ready(110, 100000))
        f.sent(1, 100000, 110, 110)
        self.assertFalse(f.ready(400, 1))

    def test_soft_age_stops_admission_before_hard_deadline(self):
        f = SendWindow(2400, 20)
        f.sent(0, 1000, 0, 0)
        self.assertTrue(f.ready(100, 1000))
        self.assertFalse(f.ready(181, 1000))
        self.assertLess(f.soft_age_ms, f.hard_age_ms)
        f.acknowledge(0, 300)
        self.assertEqual(f.in_flight_bytes, 0)
        self.assertTrue(f.ready(300, 1000))
        with self.assertRaises(ValueError):
            f.acknowledge(0, 310)

    def test_count_bound_and_ordered_feedback(self):
        f = SendWindow(2400, 20)
        for seq in range(4):
            f.sent(seq, 1000, seq*50, seq*50)
        self.assertFalse(f.ready(201, 1))
        with self.assertRaises(ValueError):
            f.acknowledge(1, 201)
        with self.assertRaises(ValueError):
            f.acknowledge(False, 201)
        self.assertEqual(f.in_flight_bytes, 4000)

    def test_feedback_changes_pacing_not_encoder_budget(self):
        f = SendWindow(2400, 20)
        f.sent(0, 1000, 0, 0)
        f.acknowledge(0, 40)
        f.sent(1, 1000, 50, 50)
        f.acknowledge(1, 300)
        self.assertLess(f.rate_kbps, 2400)
        self.assertEqual((f.ceiling, f.fps, f.byte_limit), (2400, 20, 54000))

    def test_delayed_ack_batch_does_not_multiply_decrease(self):
        f = SendWindow(2400, 20)
        f.sent(0, 1000, 0, 0)
        f.acknowledge(0, 40)
        for seq in range(1, 4):
            f.sent(seq, 1000, 50*seq, 50*seq)
        for seq in range(1, 4):
            f.acknowledge(seq, 1000+seq)
        self.assertEqual(f.rate_kbps, 2400*.85)
        self.assertGreaterEqual(f.hard_age_ms, 4000)

    def test_trace_bounds_and_numeric_report_allowlist(self):
        book = TraceBook(limit=2)
        s = book.start("run:1", "jpeg")
        book.report({"server_session_id": s["server_session_id"], "event": "integrity_rejected",
                     "actual_bytes": 100, "password": "must-not-be-stored"})
        book.end(s, "peer_closed")
        snapshot = book.snapshot()
        self.assertEqual(snapshot["discarded"], 1)
        self.assertEqual(len(snapshot["events"]), 2)
        self.assertNotIn("password", json.dumps(snapshot))
        with self.assertRaises(ValueError):
            book.report({"server_session_id": s["server_session_id"], "event": "parse_error", "seq": float('nan')})


class SendCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_write_aborts_without_queuing_close(self):
        transport = Mock()
        class Blocked:
            async def send_bytes(self, packet):
                await asyncio.sleep(10)
        with self.assertRaises(asyncio.TimeoutError):
            await send_complete(Blocked(), b'test', transport, timeout=.01)
        transport.abort.assert_called_once()

    async def test_cancelled_write_aborts(self):
        transport = Mock()
        started = asyncio.Event()
        class Blocked:
            async def send_bytes(self, packet):
                started.set()
                await asyncio.sleep(10)
        task = asyncio.create_task(send_complete(Blocked(), b'test', transport))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        transport.abort.assert_called_once()
