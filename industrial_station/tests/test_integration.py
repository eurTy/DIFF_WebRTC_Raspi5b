import asyncio
import logging
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from pymodbus.client import AsyncModbusTcpClient

from industrial_station.gateway import Gateway, RequestError, application
from industrial_station.model import Station
from industrial_station.protocol import Action, Fault, Reason, State, STATUS_COUNT, command
from industrial_station.simulator import running
from industrial_station.store import Store

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


async def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Condition not reached")
        await asyncio.sleep(.01)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "events.sqlite"))
        self.model = Station(move_ms=100, timeout_ms=350, lease_ms=700, fault_ms=1500)
        self.context = running(self.model, port=0)
        self.server = await self.context.__aenter__()
        self.port = self.server.transport.sockets[0].getsockname()[1]
        self.gateway = Gateway("127.0.0.1", self.port, self.store, poll_ms=20, timeout=.1)
        self.tokens = {"operator": "o" * 40, "observer": "v" * 40}
        self.http = TestClient(TestServer(application(self.gateway, self.tokens)))
        await self.http.start_server()
        await self.gateway.start()
        await until(lambda: self.gateway.status()["quality"] == "GOOD")
        self.counter = 0

    async def asyncTearDown(self):
        await self.http.close()
        await self.gateway.close()
        await self.context.__aexit__(None, None, None)
        self.store.close()
        self.tmp.cleanup()

    async def send(self, action, parameter=0, command_id=None, wait=True):
        self.counter += 1
        command_id = command_id or "test-" + str(self.counter)
        record = await self.gateway.submit({"command_id": command_id, "action": action, "parameter": int(parameter)})
        if wait:
            await until(lambda: self.store.get(command_id)["status"] not in ("CREATED", "SENT", "ACCEPTED"))
            record = self.store.get(command_id)
        return record

    async def test_real_modbus_read_only_status_and_atomic_command(self):
        client = AsyncModbusTcpClient("127.0.0.1", port=self.port, timeout=.1, retries=0)
        await client.connect()
        try:
            status = await client.read_holding_registers(0, count=STATUS_COUNT, device_id=1)
            self.assertEqual(status.registers[0], 2)
            self.assertTrue((await client.write_register(1, 999, device_id=1)).isError())
            self.assertTrue((await client.write_registers(100, [1], device_id=1)).isError())
            self.assertEqual(self.model.motion_starts, 0)
            result = await client.write_registers(100, command(Action.ARM, 1, 0, self.model.boot, 123, 1000), device_id=1)
            self.assertFalse(result.isError())
            self.assertEqual(self.model.owner, 123)
        finally:
            client.close()

    async def test_new_session_cannot_complete_from_previous_session_result(self):
        await self.send("ARM")
        await self.send("EXTEND")
        self.assertEqual(self.model.finished[0], 2)
        self.gateway.fail("TEST_SESSION_LOSS")
        await until(lambda: self.model.owner == 0)
        await self.send("ARM")
        result = await self.send("RETRACT", wait=False)
        self.assertEqual(result["sequence"], 2)
        self.assertEqual(result["status"], "ACCEPTED")
        await until(lambda: self.store.get(result["command_id"])["status"] == "COMPLETED")
        self.assertEqual(self.model.state, State.IDLE)

    async def test_authentication_observer_and_rejected_request_audit(self):
        self.assertEqual((await self.http.get("/api/status")).status, 401)
        self.assertEqual((await self.http.get("/health")).status, 200)
        headers = {"Authorization": "Bearer " + self.tokens["observer"]}
        self.assertEqual((await self.http.get("/api/status", headers=headers)).status, 200)
        self.assertEqual((await self.http.post("/api/commands", headers=headers, json={"action": "ARM"})).status, 403)
        headers = {"Authorization": "Bearer " + self.tokens["operator"]}
        response = await self.http.post("/api/commands", headers=headers, json={"command_id": "bad", "action": "EXTEND"})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.model.motion_starts, 0)
        self.assertEqual(sum(e["kind"] == "access_denied" for e in self.store.events()), 2)

    async def test_duplicate_id_and_busy_rejection_never_create_extra_motion(self):
        await self.send("ARM")
        first = await self.send("EXTEND", command_id="same", wait=False)
        for _ in range(10):
            again = await self.send("EXTEND", command_id="same", wait=False)
            self.assertEqual(first["sequence"], again["sequence"])
        with self.assertRaises(RequestError):
            await self.send("RETRACT", command_id="same")
        with self.assertRaises(RequestError):
            await self.send("RETRACT")
        await until(lambda: self.store.get("same")["status"] == "COMPLETED")
        self.assertEqual(self.model.motion_starts, 1)

    async def test_stop_reconciles_interrupted_motion_separately(self):
        await self.send("ARM")
        await self.send("EXTEND", command_id="motion", wait=False)
        await asyncio.sleep(.03)
        stop = await self.send("STOP")
        self.assertEqual(stop["status"], "COMPLETED")
        self.assertEqual(self.store.get("motion")["status"], "FAILED")
        self.assertEqual(self.store.get("motion")["reason"], "STOPPED")
        self.assertEqual(self.model.state, State.STOPPED)

    async def test_limit_timeout_and_reset(self):
        await self.send("ARM")
        await self.send("INJECT", Fault.TARGET_LIMIT_MISSING)
        result = await self.send("EXTEND")
        self.assertEqual((result["status"], result["reason"]), ("FAILED", "ACTION_TIMEOUT"))
        self.assertEqual((await self.send("RESET"))["status"], "REJECTED")
        await self.send("INJECT", 0)
        await self.send("RESET")
        self.assertIsNone(self.model.active)
        self.assertEqual(self.model.motion_starts, 1)

    async def test_communication_loss_revokes_authority_and_local_watchdog_stops(self):
        await self.send("ARM")
        self.model.move_ms = 2000
        self.model.timeout_ms = 5000
        await self.send("EXTEND", command_id="lost", wait=False)
        self.model.fault = Fault.COMM_SILENT
        self.model.fault_until = self.model.now_ms() + 1500
        await until(lambda: self.gateway.status()["quality"] == "OFFLINE")
        await until(lambda: self.model.owner == 0)
        self.assertEqual(self.model.alarm, Reason.COMM_TIMEOUT)
        self.assertEqual(self.store.get("lost")["status"], "UNKNOWN")
        await until(lambda: self.gateway.status()["quality"] == "GOOD")
        self.assertFalse(self.gateway.armed)
        with self.assertRaises(RequestError):
            await self.send("EXTEND")
        self.assertEqual(self.model.motion_starts, 1)

    async def test_frozen_heartbeat_is_not_good_data(self):
        await self.send("ARM")
        await self.send("INJECT", Fault.FROZEN_HEARTBEAT)
        await until(lambda: self.gateway.status()["quality"] == "OFFLINE")
        self.assertFalse(self.gateway.armed)
        await until(lambda: self.gateway.status()["quality"] == "GOOD")
        self.assertFalse(self.gateway.armed)

    async def test_device_restart_is_detected_without_replay(self):
        await self.send("ARM")
        await self.send("EXTEND", command_id="before-reboot", wait=False)
        await self.context.__aexit__(None, None, None)
        self.model = Station(move_ms=100)
        self.context = running(self.model, port=self.port)
        self.server = await self.context.__aenter__()
        await until(lambda: self.gateway.sample["boot_id"] == self.model.boot)
        self.assertFalse(self.gateway.armed)
        self.assertEqual(self.store.get("before-reboot")["status"], "UNKNOWN")
        self.assertEqual(self.model.motion_starts, 0)

    async def test_ten_cycles_and_recomputable_report(self):
        self.model.move_ms = 20
        await self.send("ARM")
        await self.send("MODE", 2)
        for index in range(10):
            result = await self.send("CYCLE", command_id="cycles-" + str(index))
            self.assertEqual(result["status"], "COMPLETED")
        report = self.store.report("cycles-")
        self.assertEqual(report["cycle_results"]["COMPLETED"], 10)
        self.assertEqual(report["cycle_results"]["FAILED"], 0)
        durations = [c["device_duration_ms"] for c in report["commands"]]
        self.assertEqual(report["cycle_duration_ms"]["mean"], sum(durations) / 10)
        self.assertEqual(self.model.cycles, 10)


class StoreTests(unittest.TestCase):
    def test_restart_preserves_id_and_marks_unconfirmed_command_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "events.sqlite")
            store = Store(filename)
            store.put({"command_id": "durable", "status": "SENT", "reason": "NONE", "created_mono_ms": 9999999999})
            store.close()
            store = Store(filename)
            record = store.get("durable")
            self.assertEqual(record["status"], "UNKNOWN")
            self.assertNotIn("confirmation_ms", record)
            store.close()


if __name__ == "__main__":
    unittest.main()
