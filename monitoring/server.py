"""Independent loopback-only read-only monitoring and incident evidence API."""
import argparse
import asyncio
import contextlib
import datetime
import json
import signal
import time
import uuid
from collections import deque
from pathlib import Path

from aiohttp import web

from .alarms import AlarmEngine
from .sensors import Capture, LinuxSensors
from .store import EvidenceStore


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Observer:
    def __init__(self, sensors, capture, store, interval=1):
        self.sensors, self.capture, self.store, self.interval = sensors, capture, store, interval
        self.run_id, self.alarms = str(uuid.uuid4()), AlarmEngine()
        self.latest, self.last_error, self.task = None, None, None
        self.recent, self.pending = deque(maxlen=10), {}

    async def sample(self, scheduled):
        now = time.monotonic()
        sensors = await asyncio.to_thread(self.sensors.sample)
        capture, _ = await asyncio.to_thread(self.capture.read)
        sample = {"utc": utc(), "mono_ms": now * 1000, "run_id": self.run_id, "source": "MEASURED",
                  "sampling_late_ms": max(0, (now - scheduled) * 1000), "sensors": sensors, "capture": capture}
        for identity, following in list(self.pending.items()):
            following.append(sample)
            if len(following) >= 5:
                self.store.update_context(identity, following, True)
                del self.pending[identity]
        for event in self.alarms.update(sample):
            metadata, jpeg = await asyncio.to_thread(self.capture.read, True)
            event.update(utc=sample["utc"], mono_ms=sample["mono_ms"], run_id=self.run_id,
                         sample=sample, before=list(self.recent), after=[], context_complete=False,
                         frame=metadata, correlation="PI_MONOTONIC_PUBLICATION_NOT_OPTICAL")
            event["frame_delta_ms"] = sample["mono_ms"] - metadata["published_ms"] if "published_ms" in metadata else None
            identity = self.store.event(event, jpeg)
            self.pending[identity] = []
        self.store.sample(sample)
        self.recent.append(sample)
        self.latest, self.last_error = sample, None

    async def run(self):
        scheduled = time.monotonic()
        while True:
            try:
                await self.sample(scheduled)
            except Exception as error:
                self.last_error = type(error).__name__
            scheduled += self.interval
            if scheduled < time.monotonic():
                scheduled = time.monotonic() + self.interval
            await asyncio.sleep(max(0, scheduled - time.monotonic()))

    def status(self):
        now = time.monotonic() * 1000
        age = now - self.latest["mono_ms"] if self.latest else None
        return {"source": "MEASURED", "run_id": self.run_id, "server_mono_ms": now, "utc": utc(),
                "quality": "GOOD" if age is not None and age < 2500 and not self.last_error else "STALE" if age is not None else "OFFLINE",
                "sample_age_ms": age, "error": self.last_error, "sample": self.latest,
                "active_alarms": [code for code, active in self.alarms.active.items() if active],
                "interval_ms": self.interval * 1000, "control": "READ_ONLY"}

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        for identity, after in self.pending.items():
            self.store.update_context(identity, after, False)


def application(observer):
    @web.middleware
    async def boundary(request, handler):
        if request.method != "GET":
            raise web.HTTPMethodNotAllowed(request.method, ["GET"])
        try:
            response = await handler(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid query") from None

    async def status(_):
        return web.json_response(observer.status())

    async def history(request):
        limit = int(request.query.get("limit", 300))
        if not 1 <= limit <= 600:
            raise ValueError("limit")
        return web.json_response(observer.store.history(limit))

    async def events(_):
        return web.json_response(observer.store.events())

    async def detail(request):
        value = observer.store.detail(int(request.match_info["identity"]))
        if value is None:
            raise web.HTTPNotFound()
        return web.json_response(value)

    async def snapshot(request):
        image = observer.store.image(int(request.match_info["identity"]))
        if image is None:
            raise web.HTTPNotFound(text="No retained image")
        return web.Response(body=image, content_type="image/jpeg")

    app = web.Application(middlewares=[boundary], client_max_size=1024)
    app.add_routes([web.get("/status", status), web.get("/history", history), web.get("/events", events),
                    web.get("/events/{identity:\\d+}", detail), web.get("/snapshot/{identity:\\d+}", snapshot)])
    return app


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--shm", default="/dev/shm/pi-video/video_shm")
    parser.add_argument("--database", default="monitoring/runtime/evidence.sqlite")
    args = parser.parse_args()
    Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(args.database)
    observer = Observer(LinuxSensors(), Capture(args.shm), store)
    runner = web.AppRunner(application(observer))
    try:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", args.port).start()
        observer.task = asyncio.create_task(observer.run())
        stopped = asyncio.Event()
        with contextlib.suppress(NotImplementedError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
        print(json.dumps({"service": "pi-observer", "port": args.port, "control": "READ_ONLY"}), flush=True)
        await stopped.wait()
    finally:
        await runner.cleanup()
        await observer.close()
        store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
