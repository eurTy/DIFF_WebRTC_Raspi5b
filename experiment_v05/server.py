import argparse
import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import asdict
import json
import logging
import math
from pathlib import Path
import socket
import time

from aiohttp import WSMsgType, web

from semantic_video.model import DEFAULT_REGIONS, BudgetController, regions_from_json
from semantic_video.server import Service, check_origin
from semantic_video.source import FixtureSource, mono_ms
from .codec import H264Encoder, JpegEncoder, envelope
from .flow import Flow, valid_ack
from .telemetry import implementation

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)


def read_latest(source):
    for retry in range(3):
        try:
            return source.read(), retry
        except ValueError as error:
            if retry == 2 or str(error) not in ("Input overwritten during read", "Uncommitted input"):
                raise
            time.sleep(.001)


class MobileService(Service):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reports = deque(maxlen=300)
        self.report_received = 0
        self.implementation = implementation("v0.5")

    async def status(self, request):
        ages = sorted(r['age_ms'] for r in self.reports if r['age_ms'] is not None)
        return web.json_response({"project":"semantic-mobile-v05", "source":self.source_kind,
                                  "sessions":len(self.sessions), "viewer_limit":1,
                                  "transports":["h264-fmp4-websocket", "jpeg-websocket"],
                                  "events":dict(self.events), "legacy_services_modified":False,
                                  "client_report":self.reports[-1] if self.reports else None,
                                  "client_report_age_ms":round(mono_ms()-self.report_received) if self.reports else None,
                                  "client_samples":len(self.reports),
                                  "client_age_p95_ms":ages[int((len(ages)-1)*.95)] if ages else None})

    async def stream(self, request):
        check_origin(request)
        if self.sessions:
            raise web.HTTPServiceUnavailable(text="One experiment viewer maximum")
        ws = web.WebSocketResponse(max_msg_size=8192, heartbeat=15, compress=False, timeout=1)
        self.sessions[id(ws)] = ws
        encoder = source = receiver = None
        try:
            await ws.prepare(request)
            self.reports.clear()
            transport = request.transport
            transport.set_write_buffer_limits(high=32768, low=8192)
            sock = transport.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            if not isinstance(hello, dict) or hello.get("type") != "start":
                raise ValueError("Start required")
            fps, kbps = hello.get("fps",20), hello.get("kbps",2400)
            if type(fps) is not int or type(kbps) is not int:
                raise ValueError("Integer settings required")
            BudgetController(kbps, fps)
            if fps < 4 or kbps < 384 or kbps > 6000:
                raise ValueError("Mobile bounds: 4-20 fps, 384-6000 kbps")
            codec = hello.get("codec","h264")
            if codec not in ("h264","jpeg") or type(hello.get("semantic",True)) is not bool:
                raise ValueError("Invalid codec or task mode")
            regions = regions_from_json(hello.get("regions",[asdict(r) for r in DEFAULT_REGIONS]))
            source = self.shared if self.shared is not None else FixtureSource(7)
            encoder_type = H264Encoder if codec == "h264" else JpegEncoder
            encoder = await asyncio.to_thread(encoder_type, regions, kbps, fps, hello.get("semantic",True))
            flow = Flow(kbps,fps)
            await ws.send_json({"type":"ready", "codec":codec, "mime":encoder.mime,
                                "implementation":self.implementation,
                                "config":{"codec":codec,"kbps":kbps,"fps":fps,"semantic":hello.get("semantic",True)},
                                "source":source.label, "regions":[asdict(r) for r in regions]})
            if encoder.init:
                await asyncio.wait_for(ws.send_bytes(envelope({"kind":"init"},encoder.init)), 2)
            pending, sequence, previous_id, next_send, failures = deque(), 0, -1, 0, 0
            receiver = asyncio.create_task(ws.receive())
            while not ws.closed:
                now = mono_ms()
                if receiver.done():
                    incoming = receiver.result()
                    if incoming.type in (WSMsgType.CLOSE,WSMsgType.CLOSING,WSMsgType.CLOSED,WSMsgType.ERROR):
                        break
                    if incoming.type != WSMsgType.TEXT:
                        raise ValueError("Text acknowledgement required")
                    ack = json.loads(incoming.data)
                    if isinstance(ack,dict) and ack.get('type') == 'display':
                        keys = ('age_ms','latency_ms','fps','buffer_ms')
                        if any(ack.get(k) is not None and (type(ack[k]) not in (int,float)
                               or not math.isfinite(ack[k]) or not 0 <= ack[k] <= 60000) for k in keys):
                            raise ValueError('Invalid display report')
                        if now-self.report_received >= 500:
                            self.reports.append({k:ack.get(k) for k in keys})
                            self.report_received = now
                        receiver = asyncio.create_task(ws.receive())
                        continue
                    if not valid_ack(ack,pending):
                        raise ValueError("Ordered acknowledgement required")
                    _, sent = pending.popleft()
                    flow.observe(now-sent,now)
                    self.events["acked"] += 1
                    self.events["last_ack_ms"] = round(now-sent,1)
                    self.events["pacing_kbps"] = round(flow.kbps)
                    receiver = asyncio.create_task(ws.receive())
                    continue
                if pending and now-pending[0][1] > 1800:
                    raise asyncio.TimeoutError
                if len(pending) < flow.window and now >= next_send:
                    try:
                        frame,retries = await asyncio.to_thread(read_latest,source)
                        self.events["input_retries"] += retries
                    except (ValueError,OSError):
                        failures += 1
                        self.events["input_failures"] += 1
                        if failures >= 10:
                            raise OSError("Input unavailable")
                        next_send = now+50
                        continue
                    failures = 0
                    if frame.identity == previous_id:
                        next_send = now+5
                        continue
                    previous_id = frame.identity
                    result = await asyncio.to_thread(encoder.encode,frame)
                    if result is None:
                        next_send = now+1000/fps
                        continue
                    meta,data = result
                    meta.update(kind="frame",seq=sequence,window=flow.window,pacing_kbps=round(flow.kbps))
                    meta["server_sent_ms"] = mono_ms()
                    packet = envelope(meta,data)
                    started = mono_ms()
                    await asyncio.wait_for(ws.send_bytes(packet),1.5)
                    pending.append((sequence,started))
                    sequence += 1
                    self.events["sent_frames"] += 1
                    self.events["sent_bytes"] += len(packet)
                    self.events["max_pending"] = max(self.events["max_pending"],len(pending))
                    self.events["last_encode_ms"] = meta["encode_ms"]
                    # Never catch up by bursting old frames after a stall.
                    next_send = flow.next_send(now,started,len(packet))
                    continue
                wake = pending[0][1]+1800 if pending else now+100
                if len(pending) < flow.window:
                    wake = min(wake,next_send)
                await asyncio.wait([receiver],timeout=max(.001,(wake-mono_ms())/1000))
        except asyncio.TimeoutError:
            self.events["timeouts"] += 1
            await ws.close(code=1013,message=b"Freshness timeout")
        except (ValueError,TypeError,KeyError):
            self.events["invalid"] += 1
            await ws.close(code=1008,message=b"Invalid session")
        except (ConnectionError,OSError):
            self.events["input_or_connection_error"] += 1
            await ws.close(code=1013,message=b"Input or connection unavailable")
        except Exception:
            LOG.exception("Mobile experiment failed")
            await ws.close(code=1011,message=b"Encoder error")
        finally:
            if receiver:
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
            if encoder:
                await asyncio.to_thread(encoder.close)
            if source is not None and source is not self.shared:
                source.close()
            self.sessions.pop(id(ws),None)
            await ws.close()
        return ws


async def static(request):
    name = request.match_info.get("name") or "index.html"
    if name not in ("index.html","app.js","telemetry.js","style.css","mobile.css","protocol.js","lucide.min.js"):
        raise web.HTTPNotFound()
    path = ROOT.parent / "web" / name if name in ("protocol.js","lucide.min.js") else ROOT / "web" / name
    return web.FileResponse(path,headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})


def application(source="fixture",source_path=None):
    service = MobileService(source,source_path)
    app = web.Application(client_max_size=8192)
    app.router.add_get("/api/status",service.status)
    app.router.add_get("/clock",service.clock)
    app.router.add_get("/stream",service.stream)
    app.router.add_get("/",static)
    app.router.add_get("/{name}",static)
    app.on_shutdown.append(service.cleanup)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8090)
    parser.add_argument("--source",choices=("fixture","shm"),default="fixture")
    parser.add_argument("--source-path")
    args = parser.parse_args()
    if args.source == "shm" and not args.source_path:
        parser.error("Live input requires --source-path")
    web.run_app(application(args.source,args.source_path),host=args.host,port=args.port,access_log=None)


if __name__ == "__main__":
    main()
