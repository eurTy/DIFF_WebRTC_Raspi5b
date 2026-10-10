"""Authenticated simulation gateway. Not a physical machine controller."""
import argparse
import asyncio
import contextlib
import json
import re
import secrets
import signal
import time
import uuid
from pathlib import Path

from aiohttp import web
from pymodbus.client import AsyncModbusTcpClient

from .protocol import Action, COMMAND_ADDRESS, DEVICE_ID, KEEPALIVE_ADDRESS, STATUS_COUNT, command, decode_status, words
from .store import Store, utc


class RequestError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Gateway:
    def __init__(self, host, port, store, poll_ms=50, timeout=.2):
        self.client = AsyncModbusTcpClient(host, port=port, timeout=timeout, retries=0, reconnect_delay=0)
        self.store, self.poll_ms = store, poll_ms
        self.run_id = str(uuid.uuid4())
        self.session = secrets.randbelow(0xffffffff) + 1
        self.sequence = self.keepalive_counter = self.failures = 0
        self.armed = False
        self.sample = None
        self.last_good = self.last_heartbeat_change = None
        self.quality, self.error = "OFFLINE", "WAITING_FOR_DEVICE"
        self.io_lock, self.command_lock = asyncio.Lock(), asyncio.Lock()
        self.last_semantic = None
        self.task = None
        self.stopping = False

    async def start(self):
        self.stopping = False
        self.task = asyncio.create_task(self.poll())

    async def close(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.client.close()
        self.armed = False
        for pending in self.store.pending():
            self.store.update(pending["command_id"], "UNKNOWN", "GATEWAY_STOP")

    def status(self):
        age = (time.monotonic() - self.last_good) * 1000 if self.last_good is not None else None
        quality = self.quality
        if age is None or age > 750:
            quality = "OFFLINE"
        elif age > 300 and quality == "GOOD":
            quality = "STALE"
        return {"source": "SIMULATED", "gateway_run_id": self.run_id, "quality": quality,
                "armed": self.armed and quality == "GOOD", "sample_age_ms": age,
                "error": self.error, "sample": self.sample}

    def fail(self, reason):
        if self.failures == 0:
            self.armed = False
            self.session = secrets.randbelow(0xffffffff) + 1
            self.sequence = self.keepalive_counter = 0
            for pending in self.store.pending():
                self.store.update(pending["command_id"], "UNKNOWN", reason)
        self.failures += 1
        quality = "OFFLINE" if self.failures >= 3 else "STALE"
        if (quality, reason) != (self.quality, self.error):
            self.store.event("communication", {"quality": quality, "reason": reason, "run_id": self.run_id})
        self.quality, self.error = quality, reason
        if self.failures >= 3:
            self.client.close()

    @staticmethod
    def checked(response):
        if response.isError():
            raise ConnectionError("MODBUS_EXCEPTION_" + str(response.exception_code))
        return response

    async def read(self):
        if not self.client.connected and not await self.client.connect():
            raise ConnectionError("CONNECT_FAILED")
        response = self.checked(await self.client.read_holding_registers(0, count=STATUS_COUNT, device_id=DEVICE_ID))
        return decode_status(response.registers)

    def observe(self, sample):
        now = time.monotonic()
        changed_boot = self.sample is not None and sample["boot_id"] != self.sample["boot_id"]
        if changed_boot:
            self.fail("DEVICE_RESTART")
        if self.armed and sample["owner_session"] != self.session:
            self.fail("SESSION_LOST")
        if self.sample is None or changed_boot or sample["heartbeat"] != self.sample["heartbeat"]:
            self.last_heartbeat_change = now
        elif now - self.last_heartbeat_change > .4:
            raise ConnectionError("FROZEN_HEARTBEAT")
        self.sample, self.last_good = sample, now
        if self.quality != "GOOD":
            self.store.event("communication", {"quality": "GOOD", "run_id": self.run_id, "armed": self.armed})
        self.quality, self.error, self.failures = "GOOD", None, 0
        semantic = tuple(sample[key] for key in ("state", "mode", "home_limit", "extended_limit", "alarm",
                                                "cycles", "injected_fault", "owner_session"))
        if semantic != self.last_semantic:
            self.store.event("device_state", {"run_id": self.run_id, **sample})
            self.last_semantic = semantic
        for pending in self.store.pending():
            if pending["boot_id"] != sample["boot_id"] or pending["session"] != self.session:
                continue
            seq = pending["sequence"]
            if seq == sample["finished_sequence"]:
                self.store.update(pending["command_id"], sample["finished_result"], sample["finished_reason"],
                                  sample["finished_duration_ms"])
            elif seq == sample["ack_sequence"]:
                self.store.update(pending["command_id"], sample["ack_result"], sample["ack_reason"])

    async def poll(self):
        while not self.stopping:
            try:
                async with self.io_lock:
                    self.observe(await self.read())
                    if self.armed:
                        self.keepalive_counter += 1
                        payload = [*words(self.sample["boot_id"]), *words(self.session), *words(self.keepalive_counter)]
                        self.checked(await self.client.write_registers(KEEPALIVE_ADDRESS, payload, device_id=DEVICE_ID))
            except (ConnectionError, OSError, ValueError, asyncio.TimeoutError) as error:
                self.fail(str(error))
            except Exception as error:
                # Library disconnect exceptions also revoke ownership, never retry a write.
                self.fail(type(error).__name__)
            if self.stopping:
                return
            await asyncio.sleep(.25 if self.failures >= 3 else self.poll_ms / 1000)

    async def submit(self, payload, actor="operator"):
        if not isinstance(payload, dict) or set(payload) - {"command_id", "action", "parameter", "validity_ms"}:
            raise RequestError("Invalid command object", 400)
        command_id = payload.get("command_id")
        if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", command_id):
            raise RequestError("Invalid command_id", 400)
        try:
            action = Action[payload.get("action", "")]
        except (KeyError, TypeError):
            raise RequestError("Unknown action", 400) from None
        parameter, validity = payload.get("parameter", 0), payload.get("validity_ms", 1000)
        if type(parameter) is not int or not 0 <= parameter <= 65535 or type(validity) is not int or not 100 <= validity <= 2000:
            raise RequestError("Invalid parameter or validity_ms", 400)
        fingerprint = dict(action=action.name, parameter=parameter, validity_ms=validity, actor=actor)
        previous = self.store.get(command_id)
        if previous:
            if previous["request"] != fingerprint:
                raise RequestError("command_id already used for a different request")
            return previous
        if self.command_lock.locked():
            raise RequestError("Another submission is in progress; no queue")
        arrived = time.monotonic()
        async with self.command_lock, self.io_lock:
            if (time.monotonic() - arrived) * 1000 >= validity:
                raise RequestError("Command expired before dispatch")
            if self.status()["quality"] != "GOOD":
                raise RequestError("Device data is not fresh", 503)
            if action != Action.ARM and not self.armed:
                raise RequestError("Explicit ARM required")
            if self.store.pending() and action != Action.STOP:
                raise RequestError("Motion is pending; only STOP is permitted")
            self.sequence += 1
            boot = self.sample["boot_id"]
            elapsed = (time.monotonic() - self.last_good) * 1000
            remaining = validity - int((time.monotonic() - arrived) * 1000)
            # Last sampled device uptime gives a conservative deadline, not a wall-clock guess.
            deadline = self.sample["device_uptime_ms"] + remaining
            if elapsed >= remaining or deadline > 0xffffffff:
                raise RequestError("Insufficient freshness or uptime range")
            record = {"command_id": command_id, "request": fingerprint, "action": action.name,
                      "actor": actor, "source": "SIMULATED", "run_id": self.run_id,
                      "boot_id": boot, "session": self.session, "sequence": self.sequence,
                      "status": "CREATED", "reason": "NONE", "created_utc": utc(),
                      "created_mono_ms": time.monotonic() * 1000}
            self.store.put(record)
            try:
                self.store.update(command_id, "SENT", "NONE")
                self.checked(await self.client.write_registers(COMMAND_ADDRESS,
                    command(action, self.sequence, parameter, boot, self.session, deadline), device_id=DEVICE_ID))
                sample = await self.read()
                self.observe(sample)
                if action == Action.ARM and sample["ack_sequence"] == self.sequence and sample["ack_result"] == "COMPLETED":
                    self.armed = sample["owner_session"] == self.session
            except Exception as error:
                self.fail(type(error).__name__)
            return self.store.get(command_id)


def application(gateway, tokens):
    if set(tokens) != {"operator", "observer"} or any(not isinstance(v, str) or len(v) < 32 for v in tokens.values()) or len(set(tokens.values())) != 2:
        raise ValueError("Distinct operator/observer tokens of at least 32 characters required")

    @web.middleware
    async def authorization(request, handler):
        if request.path == "/health" and request.method == "GET":
            return web.json_response({"service": "industrial-station", "source": "SIMULATED", "quality": gateway.status()["quality"]})
        credential = request.headers.get("Authorization", "")
        role = next((role for role, token in tokens.items() if secrets.compare_digest(credential.encode(), ("Bearer " + token).encode())), None)
        if role is None or (request.method != "GET" and role != "operator"):
            gateway.store.event("access_denied", {"method": request.method, "path": request.path[:128], "role": role})
            raise web.HTTPUnauthorized() if role is None else web.HTTPForbidden()
        try:
            return await handler(request)
        except RequestError as error:
            gateway.store.event("request_rejected", {"role": role, "reason": str(error)})
            return web.json_response({"error": str(error)}, status=error.status)
        except (ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text="Invalid JSON or query") from None

    async def status(_):
        return web.json_response(gateway.status())

    async def submit(request):
        record = await gateway.submit(await request.json(), "operator")
        return web.json_response(record, status=202 if record["status"] in ("CREATED", "SENT", "ACCEPTED", "UNKNOWN") else 200)

    async def lookup(request):
        record = gateway.store.get(request.match_info["command_id"])
        if not record:
            raise web.HTTPNotFound()
        return web.json_response(record)

    async def events(request):
        after, limit = int(request.query.get("after", "0")), int(request.query.get("limit", "100"))
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("Invalid pagination")
        return web.json_response(gateway.store.events(after, limit))

    async def report(request):
        return web.json_response(gateway.store.report(request.query.get("prefix", "")[:96]))

    app = web.Application(middlewares=[authorization], client_max_size=8192)
    app.add_routes([web.get("/health", status), web.get("/api/status", status),
                    web.post("/api/commands", submit), web.get("/api/commands/{command_id}", lookup),
                    web.get("/api/events", events), web.get("/api/report", report)])
    return app


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-host", default="127.0.0.1")
    parser.add_argument("--device-port", type=int, default=5020)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--database", default="industrial_station/runtime/events.sqlite")
    parser.add_argument("--access", default="industrial_station/runtime/access.json")
    args = parser.parse_args()
    credentials = json.loads(Path(args.access).read_text(encoding="utf-8"))
    Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.database)
    gateway = Gateway(args.device_host, args.device_port, store)
    runner = web.AppRunner(application(gateway, credentials["tokens"]))
    try:
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        await gateway.start()
        print(json.dumps({"source": "SIMULATED", "host": args.host, "port": args.port}), flush=True)
        stopped = asyncio.Event()
        with contextlib.suppress(NotImplementedError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
        await stopped.wait()
    finally:
        await runner.cleanup()
        await gateway.close()
        store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
