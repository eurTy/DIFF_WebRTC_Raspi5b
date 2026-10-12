"""Bounded, independent streaming server. Configuration affects only its own session."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from aiohttp import WSMsgType, web
import cv2

from . import __version__
from .encoder import Encoder
from .model import DEFAULT_REGIONS, regions_from_json
from .source import CameraSource, FixtureSource, SharedMemorySource, mono_ms

PUBLIC = Path(__file__).resolve().parent.parent / "web"
cv2.setNumThreads(1)


class Service:
    def __init__(self, source="fixture", source_path=None):
        self.source_kind = source
        self.shared = SharedMemorySource(source_path) if source == "shm" else CameraSource(source_path) if source == "camera" else None
        self.sessions = {}
        self.clock_clients = set()
        self.started = mono_ms()

    async def status(self, request):
        return web.json_response({"project": "semantic-adaptive-v2", "version": __version__, "source": self.source_kind,
                                  "sessions": len(self.sessions), "uptime_s": (mono_ms()-self.started)/1000,
                                  "control": "NO_DEVICE_COMMANDS", "legacy_services_modified": False})

    async def clock(self, request):
        check_origin(request)
        if len(self.clock_clients) >= 8:
            raise web.HTTPServiceUnavailable(text="Clock limit")
        ws = web.WebSocketResponse(max_msg_size=512, heartbeat=15)
        self.clock_clients.add(ws)
        previous = []
        try:
            await ws.prepare(request)
            async for incoming in ws:
                received = mono_ms()
                previous = [p for p in previous if received-p < 1000]
                if incoming.type != WSMsgType.TEXT or len(previous) >= 16:
                    break
                data = json.loads(incoming.data)
                if not isinstance(data, dict) or data.get("type") != "clock" or not isinstance(data.get("id"), int) or isinstance(data["id"], bool):
                    break
                previous.append(received)
                await ws.send_json({"type": "clock", "id": data["id"], "received_ms": received, "sent_ms": mono_ms()})
        except (ValueError, TypeError):
            pass
        finally:
            self.clock_clients.discard(ws)
            await ws.close()
        return ws

    async def stream(self, request):
        check_origin(request)
        if len(self.sessions) >= 2:
            raise web.HTTPServiceUnavailable(text="Two independent viewers maximum")
        ws = web.WebSocketResponse(max_msg_size=8192, heartbeat=20, compress=False)
        identity = uuid.uuid4().hex
        self.sessions[identity] = ws
        source = None
        try:
            await ws.prepare(request)
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            if not isinstance(hello, dict) or hello.get("type") != "start":
                raise ValueError("Start message required")
            mode = hello.get("mode", "semantic")
            regions = regions_from_json(hello.get("regions", [asdict(r) for r in DEFAULT_REGIONS]))
            fps, kbps = hello.get("fps", 10), hello.get("kbps", 1200)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (fps, kbps)):
                raise ValueError("Numeric budget and FPS required")
            encoder = Encoder(mode, regions, kbps, fps)
            seed = hello.get("seed", 7)
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 9999:
                raise ValueError("Invalid fixture seed")
            source = FixtureSource(seed) if self.shared is None else self.shared
            await ws.send_json({"type": "ready", "session": identity, "source": source.label,
                                "policy": mode, "regions": [asdict(r) for r in regions]})
            previous_id = -1
            failures = 0
            while not ws.closed:
                cycle = mono_ms()
                try:
                    frame = await asyncio.to_thread(source.read)
                except (ValueError, OSError) as error:
                    failures += 1
                    await ws.send_json({"type": "source_error", "reason": str(error)})
                    if failures >= 10:
                        break
                    await asyncio.sleep(.2)
                    continue
                failures = 0
                if frame.identity == previous_id:
                    await asyncio.sleep(.005)
                    continue
                packet, metadata = await asyncio.to_thread(encoder.encode, frame)
                previous_id = frame.identity
                send_started = mono_ms()
                await asyncio.wait_for(ws.send_bytes(packet), 5)
                ack = await asyncio.wait_for(ws.receive_json(), 5)
                if not isinstance(ack, dict) or ack.get("type") != "ack" or type(ack.get("id")) is not int or ack["id"] != frame.identity:
                    raise ValueError("Exactly one matching acknowledgement required")
                encoder.acknowledge(metadata, mono_ms()-send_started, len(packet))
                period = max(encoder.control.period_ms, len(packet)*8/encoder.control.kbps)
                delay = max(0, period-(mono_ms()-cycle))/1000
                if delay:
                    # Keep reading control frames while pacing so close releases its slot promptly.
                    try:
                        incoming = await asyncio.wait_for(ws.receive(), delay)
                        if incoming.type not in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING, WSMsgType.ERROR):
                            raise ValueError("Unexpected message while pacing")
                        break
                    except asyncio.TimeoutError:
                        pass
        except (ValueError, TypeError, KeyError, asyncio.TimeoutError, ConnectionError, OSError):
            await ws.close(code=1008, message=b"Invalid session or delivery deadline")
        finally:
            self.sessions.pop(identity, None)
            if source is not None and source is not self.shared:
                source.close()
            await ws.close()
        return ws

    async def cleanup(self, app):
        for ws in list(self.sessions.values()) + list(self.clock_clients):
            await ws.close(code=1001)
        if self.shared:
            await asyncio.to_thread(self.shared.close)


def check_origin(request):
    origin = request.headers.get("Origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            valid = parsed.scheme in ("http", "https") and parsed.hostname == request.url.host
        except ValueError:
            valid = False
        if not valid:
            raise web.HTTPForbidden(text="Cross-site WebSocket denied")


async def static(request):
    name = request.match_info.get("name") or "index.html"
    allowed = {"index.html", "app.js", "protocol.js", "style.css", "lucide.min.js", "lucide-LICENSE"}
    if name not in allowed:
        raise web.HTTPNotFound()
    return web.FileResponse(PUBLIC / name, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def application(source="fixture", source_path=None):
    service = Service(source, source_path)
    app = web.Application(client_max_size=8192)
    app.router.add_get("/api/status", service.status)
    app.router.add_get("/clock", service.clock)
    app.router.add_get("/stream", service.stream)
    app.router.add_get("/", static)
    app.router.add_get("/{name}", static)
    app.on_shutdown.append(service.cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8088, type=int)
    parser.add_argument("--source", choices=("fixture", "shm", "camera"), default="fixture")
    parser.add_argument("--source-path")
    args = parser.parse_args()
    if args.source != "fixture" and not args.source_path:
        parser.error("Live inputs require an explicit source path")
    web.run_app(application(args.source, args.source_path), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
