"""Isolated progressive video experiment on a separate port."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path

from aiohttp import WSMsgType, web

from semantic_video.model import DEFAULT_REGIONS, regions_from_json
from semantic_video.protocol import unpack
from semantic_video.server import Service as BaseService, check_origin
from semantic_video.source import FixtureSource, mono_ms
from .pieces import pack_piece
from .policy import TaskFreshnessEncoder

PUBLIC = Path(__file__).resolve().parent / "web"
LEGACY_WEB = Path(__file__).resolve().parent.parent / "web"
PATCH_DEADLINE_MS = 80


class ExperimentService(BaseService):
    async def status(self, request):
        return web.json_response({"project": "semantic-adaptive-v04-experiment", "source": self.source_kind,
                                  "sessions": len(self.sessions), "control": "NO_DEVICE_COMMANDS",
                                  "legacy_services_modified": False, "transport": "websocket-tcp"})

    async def stream(self, request):
        check_origin(request)
        if self.sessions:
            raise web.HTTPServiceUnavailable(text="One viewer maximum")
        ws = web.WebSocketResponse(max_msg_size=8192, heartbeat=20, compress=False, timeout=1)
        self.sessions[id(ws)] = ws
        source = None
        try:
            await ws.prepare(request)
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            if not isinstance(hello, dict) or hello.get("type") != "start":
                raise ValueError("Start required")
            regions = regions_from_json(hello.get("regions", [asdict(r) for r in DEFAULT_REGIONS]))
            fps, kbps = hello.get("fps", 10), hello.get("kbps", 1200)
            if any(type(value) not in (int, float) for value in (fps, kbps)):
                raise ValueError("Numeric budget required")
            encoder = TaskFreshnessEncoder(regions, kbps, fps)
            seed = hello.get("seed", 7)
            if type(seed) is not int or not 0 <= seed <= 9999:
                raise ValueError("Invalid fixture seed")
            source = self.shared if self.shared is not None else FixtureSource(seed)
            await ws.send_json({"type": "ready", "source": source.label,
                                "regions": [asdict(region) for region in regions],
                                "patch_deadline_ms": PATCH_DEADLINE_MS})
            previous_id, failures = -1, 0
            while not ws.closed:
                cycle = mono_ms()
                try:
                    frame = await asyncio.to_thread(source.read)
                except (ValueError, OSError) as error:
                    failures += 1
                    await ws.send_json({"type": "source_error", "reason": str(error)})
                    if failures >= 10:
                        raise OSError("Input unavailable")
                    await asyncio.sleep(.2)
                    continue
                failures = 0
                if frame.identity == previous_id:
                    await asyncio.sleep(.005)
                    continue
                packet, meta = await asyncio.to_thread(encoder.encode, frame)
                previous_id = frame.identity
                meta, images = unpack(packet)
                patch_name = meta["parts"][1]["name"] if len(images) > 1 else None
                pieces = []
                for index, image in enumerate(images):
                    part = meta["parts"][index]
                    piece_meta = {"kind": "base" if index == 0 else "patch", "id": meta["id"],
                                  "captured_ms": meta["captured_ms"], "rect": part["rect"],
                                  "name": part["name"], "patch_name": patch_name,
                                  "level": meta["level"], "encode_ms": meta["encode_ms"],
                                  "budget_bytes": meta["budget_bytes"], "source": meta["source"]}
                    pieces.append(pack_piece(piece_meta, image))
                started = mono_ms()
                for piece in pieces:
                    await asyncio.wait_for(ws.send_bytes(piece), 2)
                ack = await asyncio.wait_for(ws.receive_json(), 2)
                if (not isinstance(ack, dict) or ack.get("type") != "ack" or type(ack.get("id")) is not int
                        or ack["id"] != frame.identity or type(ack.get("base")) is not bool
                        or type(ack.get("patch")) is not bool or (ack["patch"] and (not ack["base"] or not patch_name))
                        or type(ack.get("wait_ms")) not in (int, float)
                        or not 0 <= ack["wait_ms"] <= PATCH_DEADLINE_MS + 20):
                    raise ValueError("Invalid display acknowledgement")
                service_ms = max(1.0, mono_ms() - started - ack["wait_ms"])
                encoder.visible(patch_name if ack["patch"] else None, sum(map(len, pieces)), service_ms,
                                len(images[1]) if patch_name and ack["patch"] else 0)
                self.events["base_displayed" if ack["base"] else "base_rejected"] += 1
                if ack["patch"]:
                    self.events["patch_displayed"] += 1
                period = max(encoder.control.period_ms, sum(map(len, pieces)) * 8 / encoder.control.kbps)
                delay = max(0, period - (mono_ms() - cycle)) / 1000
                if delay:
                    try:
                        incoming = await asyncio.wait_for(ws.receive(), delay)
                        if incoming.type not in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                            raise ValueError("Unexpected message")
                        break
                    except asyncio.TimeoutError:
                        pass
        except asyncio.TimeoutError:
            await ws.close(code=1013, message=b"Delivery timeout")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            await ws.close(code=1008, message=b"Invalid session")
        except (ConnectionError, OSError):
            await ws.close(code=1013, message=b"Input unavailable")
        finally:
            self.sessions.pop(id(ws), None)
            if source is not None and source is not self.shared:
                source.close()
            await ws.close()
        return ws


async def static(request):
    name = request.match_info.get("name") or "index.html"
    path = (LEGACY_WEB if name in ("protocol.js", "lucide.min.js") else PUBLIC) / name
    if name not in ("index.html", "app.js", "piece.js", "style.css", "protocol.js", "lucide.min.js"):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def application(source="fixture", source_path=None):
    service = ExperimentService(source, source_path)
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
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--source", choices=("fixture", "shm"), default="fixture")
    parser.add_argument("--source-path")
    args = parser.parse_args()
    if args.source == "shm" and not args.source_path:
        parser.error("Live input requires --source-path")
    web.run_app(application(args.source, args.source_path), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
