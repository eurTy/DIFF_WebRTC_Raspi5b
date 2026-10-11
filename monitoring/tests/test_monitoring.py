import asyncio
import json
import struct
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from monitoring.alarms import AlarmEngine
from monitoring.sensors import Capture, LinuxSensors
from monitoring.server import Observer, application
from monitoring.store import EvidenceStore


def sample(now=1000, age=5, temp=50, cpu=10, signal=-50):
    return {"utc": "2026-10-11T00:00:00+00:00", "run_id": "test", "mono_ms": now,
            "source": "MEASURED", "sensors": {"temperature_c": temp, "cpu_percent": cpu, "wifi_signal_dbm": signal},
            "capture": {"quality": "STALE" if age > 500 else "GOOD", "age_ms": age}}


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_missing_values_are_not_fabricated_zero(self):
        values = LinuxSensors(self.root).sample()
        for key in ("cpu_percent", "temperature_c", "memory_percent", "wifi_tx_mbps", "wifi_signal_dbm"):
            self.assertIsNone(values[key])

    def test_cpu_network_and_memory_deltas(self):
        self.write("proc/stat", "cpu 10 0 10 80 0 0 0 0 0 0\n")
        self.write("proc/meminfo", "MemTotal: 1000 kB\nMemAvailable: 800 kB\n")
        self.write("proc/net/dev", "wlan0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n")
        self.write("proc/net/wireless", " wlan0: 0000 70. -45. -256. 0 0\n")
        sensors = LinuxSensors(self.root)
        self.assertIsNone(sensors.sample(now=1)["cpu_percent"])
        self.write("proc/stat", "cpu 30 0 20 150 0 0 0 0 0 0\n")
        self.write("proc/net/dev", "wlan0: 1000000 0 0 0 0 0 0 0 2000000 0 0 0 0 0 0 0\n")
        values = sensors.sample(now=2)
        self.assertAlmostEqual(values["cpu_percent"], 30)
        self.assertAlmostEqual(values["memory_percent"], 20)
        self.assertEqual(values["wifi_tx_mbps"], 16)
        self.assertEqual(values["wifi_signal_dbm"], -45)

    def test_shared_memory_quality_and_snapshot_consistency(self):
        path = self.root / "shm"
        header = bytearray(48)
        struct.pack_into("<7I", header, 0, 9, 1, 4, 4, 2, 0, 0)
        struct.pack_into("<Q", header, 40, 1000)
        path.write_bytes(header + b"\xff\xd8\xff\xd9")
        capture = Capture(path)
        meta, image = capture.read(True, now_ms=1100)
        self.assertEqual(meta["quality"], "GOOD")
        self.assertEqual(image, b"\xff\xd8\xff\xd9")
        self.assertEqual(capture.read(now_ms=2000)[0]["quality"], "STALE")
        self.assertEqual(capture.read(now_ms=900)[0]["quality"], "INVALID")
        header[16] = 3
        path.write_bytes(header + image)
        self.assertEqual(capture.read(now_ms=1100)[0]["quality"], "UNAVAILABLE")


class AlarmTests(unittest.TestCase):
    def test_capture_debounce_recovery_and_no_alarm_flood(self):
        alarms = AlarmEngine()
        self.assertEqual(alarms.update(sample(age=1000)), [])
        self.assertEqual(alarms.update(sample(age=1000))[0]["phase"], "ACTIVE")
        self.assertEqual(alarms.update(sample(age=1000)), [])
        self.assertEqual(alarms.update(sample()), [])
        self.assertEqual(alarms.update(sample())[0]["phase"], "RECOVERED")

    def test_temperature_hysteresis_and_missing_value_not_recovery(self):
        alarms = AlarmEngine()
        for _ in range(3):
            result = alarms.update(sample(temp=80))
        self.assertEqual(result[0]["code"], "HOST_HOT")
        self.assertEqual(alarms.update(sample(temp=None)), [])
        self.assertTrue(alarms.active["HOST_HOT"])
        self.assertEqual(alarms.update(sample(temp=72)), [])
        alarms.update(sample(temp=69))
        self.assertEqual(alarms.update(sample(temp=69))[0]["phase"], "RECOVERED")


class StoreTests(unittest.TestCase):
    def test_bounded_images_metadata_and_context(self):
        store = EvidenceStore(":memory:", event_limit=3, image_limit_bytes=6)
        self.addCleanup(store.close)
        identities = [store.event({"code": "TEST", "before": [], "sample": {}}, b"abcd") for _ in range(4)]
        self.assertIsNone(store.detail(identities[0]))
        self.assertIsNone(store.image(identities[1]))
        self.assertEqual(store.image(identities[-1]), b"abcd")
        store.update_context(identities[-1], [sample()], True)
        self.assertTrue(store.detail(identities[-1])["context_complete"])
        self.assertEqual(len(store.detail(identities[-1])["image_sha256"]), 64)
        self.assertEqual(len(store.events()), 3)

    def test_history_retention_and_order(self):
        store = EvidenceStore(":memory:", sample_limit=3)
        self.addCleanup(store.close)
        for i in range(60):
            store.sample(sample(now=i))
        self.assertEqual([s["mono_ms"] for s in store.history()], [57, 58, 59])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_readonly_api_and_stale_sample(self):
        store = EvidenceStore(":memory:")
        observer = Observer(None, None, store)
        http = TestClient(TestServer(application(observer)))
        await http.start_server()
        try:
            self.assertEqual((await (await http.get("/status")).json())["quality"], "OFFLINE")
            observer.latest = sample(now=time.monotonic() * 1000 - 3000)
            self.assertEqual((await (await http.get("/status")).json())["quality"], "STALE")
            self.assertEqual((await http.post("/status", json={})).status, 405)
            self.assertEqual((await http.get("/history?limit=601")).status, 400)
            self.assertEqual((await http.get("/snapshot/1")).status, 404)
        finally:
            await http.close()
            store.close()

    async def test_event_context_finishes_after_five_following_samples(self):
        class Sensors:
            def sample(self):
                return sample()["sensors"]
        class Camera:
            age = 2000
            def read(self, with_image=False):
                return {"quality": "STALE", "age_ms": self.age, "published_ms": time.monotonic() * 1000 - self.age}, b"jpeg" if with_image else None
        store = EvidenceStore(":memory:")
        observer = Observer(Sensors(), Camera(), store)
        try:
            for _ in range(7):
                await observer.sample(time.monotonic())
            event = store.detail(1)
            self.assertEqual(len(event["before"]), 1)
            self.assertEqual(len(event["after"]), 5)
            self.assertTrue(event["context_complete"])
            self.assertGreater(event["frame_delta_ms"], 1900)
        finally:
            await observer.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
