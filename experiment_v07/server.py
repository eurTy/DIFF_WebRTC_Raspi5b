"""Independent-frame transport, isolated from the working 8089/8090 viewers."""
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
import struct
import time
import zlib

from aiohttp import web, WSMsgType
import cv2

from semantic_video.server import Service, check_origin
from semantic_video.model import DEFAULT_REGIONS, regions_from_json
from semantic_video.source import FixtureSource, mono_ms
from experiment_v05.codec import H264Encoder, JpegEncoder
from experiment_v05.telemetry import implementation
from experiment_v07.delivery import SendWindow, checked_envelope
from experiment_v07.diagnostics import TraceBook

ROOT = Path(__file__).parent
LOG = logging.getLogger(__name__)
MAGIC = b"SVJ7"
MAX_PENDING = SendWindow.max_frames


async def send_complete(ws, packet, transport, timeout=2.5):
    try:
        await asyncio.wait_for(ws.send_bytes(packet), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Do not queue a Close frame behind a cancelled/blocked media write.
        transport.abort()
        raise


class ViewerClosed(Exception):
    pass


async def receive_json(ws, timeout):
    incoming = await asyncio.wait_for(ws.receive(), timeout)
    if incoming.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
        raise ViewerClosed
    if incoming.type != WSMsgType.TEXT:
        raise ValueError("Text feedback required")
    return json.loads(incoming.data)


def read_latest(source):
    for attempt in range(3):
        try:
            return source.read()
        except ValueError as error:
            if attempt == 2 or str(error) not in ("Input overwritten during read", "Uncommitted input"):
                raise
            time.sleep(.001)


def settings(value):
    if not isinstance(value, dict) or value.get("type") != "start":
        raise ValueError("Start required")
    kbps, fps = value.get("kbps", 2400), value.get("fps", 20)
    if type(kbps) is not int or not 1200 <= kbps <= 6000 or type(fps) is not int or not 10 <= fps <= 20:
        raise ValueError("Settings out of bounds")
    return kbps, fps


def encode(frame, sequence, quality=78):
    started = mono_ms()
    if frame.image.shape[:2] != (720, 1280):
        raise ValueError("Native 1280x720 input required")
    ok, image = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok or len(image) > 2 * 1024 * 1024:
        raise ValueError("JPEG encoding failed")
    meta = {"seq": sequence, "id": frame.identity, "captured_ms": frame.captured_ms,
            "width": 1280, "height": 720, "encode_ms": round(mono_ms()-started, 2),
            "sent_ms": mono_ms()}
    raw = json.dumps(meta, separators=(",", ":"), allow_nan=False).encode("ascii")
    return MAGIC + struct.pack("!I", len(raw)) + raw + image.tobytes(), meta


class FreshService(Service):
    def __init__(self, *args):
        super().__init__(*args)
        self.last_frame = None
        self.last_ack_ms = None
        self.client_report = None
        self.report_received_ms = None
        self.implementation = implementation("v0.7")
        self.delivery_trace = deque(maxlen=96)
        self.delivery_state = None
        self.trace = TraceBook()

    async def diagnostics(self, request):
        check_origin(request)
        if request.method == "POST":
            try:
                self.trace.report(await request.json())
            except (ValueError, TypeError):
                raise web.HTTPBadRequest(text="Invalid numeric diagnostic")
            return web.json_response({"ok": True})
        return web.json_response(self.trace.snapshot(compact=True), headers={"Cache-Control": "no-store"})

    async def status(self, request):
        return web.json_response({"project": "semantic-ws-v07", "source": self.source_kind,
            "sessions": len(self.sessions), "viewer_limit": 1, "resolution": [1280, 720],
            "transport": "WebSocket/H264-fMP4-or-JPEG/TCP", "last_frame": self.last_frame,
            "last_ack_ms": self.last_ack_ms, "client_report": self.client_report,
            "client_report_age_ms": (round(mono_ms()-self.report_received_ms)
                                     if self.report_received_ms is not None else None),
            "max_pending": MAX_PENDING,
            "delivery": self.delivery_state, "delivery_trace": list(self.delivery_trace),
            "events": dict(self.events),
            "legacy_services_modified": False})

    async def media_stream(self, request):
        check_origin(request)
        if self.sessions:
            raise web.HTTPServiceUnavailable(text="Experiment viewer already active")
        ws = web.WebSocketResponse(max_msg_size=8192, heartbeat=15, timeout=1, compress=False, writer_limit=16384)
        self.sessions[id(ws)] = ws
        source = self.shared if self.shared else FixtureSource(7)
        encoder = receiver = diagnostic = None
        end_reason = "peer_closed"
        try:
            await ws.prepare(request)
            transport = request.transport
            transport.set_write_buffer_limits(high=32768, low=8192)
            sock = transport.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
            hello = await receive_json(ws, 5)
            kbps, fps = settings(hello)
            codec = hello.get("codec", "h264")
            if codec not in ("h264", "jpeg") or type(hello.get("semantic", True)) is not bool:
                raise ValueError("Codec and task mode required")
            diagnostic = self.trace.start(hello.get("client_session_id"), codec)
            regions = regions_from_json(hello.get("regions", [asdict(r) for r in DEFAULT_REGIONS]))
            encoder_type = H264Encoder if codec == "h264" else JpegEncoder
            encoder = await asyncio.to_thread(encoder_type, regions, kbps, fps, hello.get("semantic", True))
            flow = SendWindow(kbps, fps)
            self.delivery_state = flow.snapshot(mono_ms())
            await ws.send_json({"type": "ready", "source": source.label, "codec": codec, "mime": encoder.mime,
                                "implementation": self.implementation,
                                "config": {"codec": codec, "kbps": kbps, "fps": fps, "semantic": hello.get("semantic", True)},
                                "regions": [asdict(r) for r in regions], "delivery_policy": self.delivery_state,
                                "integrity": "crc32-v1", "server_session_id": diagnostic["server_session_id"],
                                "boot_id": self.trace.boot_id, "generation": 0})
            if encoder.init:
                await send_complete(ws, checked_envelope({"kind": "init"}, encoder.init), transport)
            sequence, previous_id, next_due, failures = 0, -1, 0, 0
            prepared = None
            generation, expected = 0, {}
            receiver = asyncio.create_task(ws.receive())
            while not ws.closed:
                now = mono_ms()
                if receiver.done():
                    incoming = receiver.result()
                    if incoming.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                        self.trace.add(diagnostic, "peer_close", close_code=ws.close_code)
                        break
                    if incoming.type != WSMsgType.TEXT:
                        raise ValueError("Text feedback required")
                    ack = json.loads(incoming.data)
                    if not isinstance(ack, dict):
                        raise ValueError("Invalid feedback")
                    if ack.get("type") == "display":
                        keys = ("age_ms", "latency_ms", "fps", "buffer_ms")
                        if any(ack.get(key) is not None and
                               (type(ack[key]) not in (int, float) or not math.isfinite(ack[key]) or
                                not 0 <= ack[key] <= 60000) for key in keys):
                            raise ValueError("Invalid display feedback")
                        self.client_report = {key: ack.get(key) for key in keys}
                        self.report_received_ms = now
                    elif ack.get("type") == "ack":
                        outcome = ack.get("outcome", "accepted")
                        if outcome not in ("accepted", "integrity_rejected", "superseded") or (codec != "jpeg" and outcome != "accepted"):
                            raise ValueError("Invalid delivery outcome")
                        flow.acknowledge(ack.get("seq"), now)
                        sent_evidence = expected.pop(ack["seq"], {})
                        received = {k: ack[k] for k in ("rx_bytes", "rx_crc32") if type(ack.get(k)) is int and 0 <= ack[k] < 2**32}
                        self.trace.add(diagnostic, "ack", seq=ack["seq"], ack_ms=round(flow.last_ack_ms, 2),
                                       outcome=outcome, **sent_evidence, **received)
                        self.last_ack_ms = round(flow.last_ack_ms, 2)
                        self.events["acked"] += 1
                        if outcome != "accepted":
                            self.events[outcome] += 1
                        self.delivery_state = flow.snapshot(now)
                        self.delivery_trace.append({"event": "ack", "seq": ack["seq"], "at_ms": now,
                                                    "ack_ms": self.last_ack_ms, "outcome": outcome,
                                                    "pending_bytes": flow.in_flight_bytes})
                    else:
                        raise ValueError("Ordered frame acknowledgement required")
                    receiver = asyncio.create_task(ws.receive())
                    continue
                if flow.oldest_age(now) > flow.hard_age_ms:
                    end_reason = "ack_deadline"
                    self.events["ack_deadline"] += 1
                    self.trace.add(diagnostic, "abort", reason=end_reason, **flow.snapshot(now))
                    self.delivery_trace.append({"event": "ack_deadline", "at_ms": now, **flow.snapshot(now)})
                    transport.abort()
                    raise asyncio.TimeoutError
                if now < next_due or not flow.ready(now):
                    await asyncio.wait([receiver], timeout=.01)
                    continue
                if prepared is None:
                    cycle = now
                    try:
                        frame = await asyncio.to_thread(read_latest, source)
                    except (OSError, ValueError):
                        failures += 1
                        self.events["input_errors"] += 1
                        if failures >= 10:
                            raise OSError("Input unavailable")
                        next_due = mono_ms()+50
                        continue
                    failures = 0
                    if frame.identity == previous_id:
                        next_due = mono_ms()+3
                        continue
                    previous_id = frame.identity
                    result = await asyncio.to_thread(encoder.encode, frame)
                    if result is None:
                        next_due = cycle+1000/fps
                        continue
                    prepared = (*result, cycle)
                meta, image, cycle = prepared
                if mono_ms()-meta["captured_ms"] > 1000:
                    if codec == "jpeg":
                        self.events["unsent_stale_jpeg"] += 1
                        prepared = None
                        continue
                    # Only discard a reference chain after all transmitted fragments
                    # are acknowledged. Reset the mux/decoder, not the TCP connection.
                    if flow.pending:
                        await asyncio.wait([receiver], timeout=.01)
                        continue
                    self.events["stale_h264_reset"] += 1
                    generation += 1
                    self.trace.add(diagnostic, "media_reset", generation=generation, reason="unsent_stale_h264", seq=sequence)
                    await asyncio.to_thread(encoder.close)
                    encoder = await asyncio.to_thread(encoder_type, regions, kbps, fps, hello.get("semantic", True))
                    await ws.send_json({"type": "reset", "generation": generation, "mime": encoder.mime})
                    await send_complete(ws, checked_envelope({"kind": "init", "generation": generation}, encoder.init), transport)
                    prepared = None
                    continue
                meta.update(kind="frame", seq=sequence, window=MAX_PENDING, pacing_kbps=round(flow.rate_kbps, 2),
                            delivery_policy=flow.snapshot(mono_ms()), generation=generation,
                            server_session_id=diagnostic["server_session_id"])
                meta["server_sent_ms"] = mono_ms()
                packet = checked_envelope(meta, image)
                if not flow.ready(mono_ms(), len(packet)):
                    await asyncio.wait([receiver], timeout=.01)
                    continue
                sent = mono_ms()
                trace = {"event": "write", "seq": sequence, "at_ms": sent, "packet_bytes": len(packet),
                         "payload_bytes": len(image), "status": "started"}
                self.delivery_trace.append(trace)
                evidence = {"expected_bytes": len(image), "expected_crc32": zlib.crc32(image)}
                self.trace.add(diagnostic, "write_start", seq=sequence, generation=generation,
                               frame_id=meta["id"], packet_bytes=len(packet), **evidence)
                try:
                    await send_complete(ws, packet, transport)
                except asyncio.TimeoutError:
                    end_reason = "write_timeout"
                    self.trace.add(diagnostic, "abort", reason=end_reason, seq=sequence)
                    trace["status"] = "write_timeout"
                    self.events["write_timeout"] += 1
                    raise
                trace.update(status="buffered", write_ms=round(mono_ms()-sent, 2),
                             transport_buffer_bytes=transport.get_write_buffer_size())
                flow.sent(sequence, len(packet), sent, cycle)
                expected[sequence] = evidence
                self.trace.add(diagnostic, "write_buffered", seq=sequence, write_ms=trace["write_ms"],
                               transport_buffer_bytes=trace["transport_buffer_bytes"])
                self.delivery_state = flow.snapshot(mono_ms())
                prepared = None
                self.last_frame = {"seq": sequence, "bytes": len(packet), "encode_ms": meta["encode_ms"],
                                   "captured_ms": meta["captured_ms"]}
                self.events["sent_frames"] += 1
                self.events["sent_bytes"] += len(packet)
                self.events["max_in_flight"] = max(self.events["max_in_flight"], len(flow.pending))
                self.events["max_in_flight_bytes"] = max(self.events["max_in_flight_bytes"], flow.in_flight_bytes)
                sequence += 1
                next_due = 0
        except ViewerClosed:
            end_reason = "peer_closed_before_start"
        except asyncio.TimeoutError:
            if end_reason == "peer_closed":
                end_reason = "start_or_init_write_timeout"
            self.events["timeouts"] += 1
            if not request.transport or not request.transport.is_closing():
                await ws.close(code=1013, message=b"Freshness timeout", drain=False)
        except (ValueError, TypeError, KeyError):
            end_reason = "invalid_feedback_or_envelope"
            self.events["invalid"] += 1
            await ws.close(code=1008, message=b"Invalid session")
        except ConnectionError as error:
            end_reason = "connection_error"
            if diagnostic:
                self.trace.add(diagnostic, "connection_error", exception_type=type(error).__name__)
            self.events["input_or_connection_error"] += 1
        except OSError as error:
            end_reason = "input_error"
            if diagnostic:
                self.trace.add(diagnostic, "input_error", exception_type=type(error).__name__)
            self.events["input_or_connection_error"] += 1
            await ws.close(code=1013, message=b"Input or connection unavailable")
        except asyncio.CancelledError:
            end_reason = "shutdown_cancelled"
            raise
        except Exception:
            end_reason = "server_error"
            LOG.exception("Media experiment failed")
            await ws.close(code=1011, message=b"Server error")
        finally:
            if diagnostic:
                self.trace.end(diagnostic, end_reason)
            if receiver:
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
            if encoder:
                await asyncio.to_thread(encoder.close)
            self.sessions.pop(id(ws), None)
            if source is not self.shared:
                source.close()
            with suppress(ConnectionError, OSError):
                await ws.close(drain=False)
        return ws

    async def stream(self, request):
        check_origin(request)
        if self.sessions:
            raise web.HTTPServiceUnavailable(text="Experiment viewer already active")
        ws = web.WebSocketResponse(max_msg_size=8192, heartbeat=15, timeout=1, compress=False)
        self.sessions[id(ws)] = ws
        source = self.shared if self.shared else FixtureSource(7)
        try:
            await ws.prepare(request)
            transport = request.transport
            transport.set_write_buffer_limits(high=32768, low=8192)
            sock = transport.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
            kbps, fps = settings(await receive_json(ws, 5))
            await ws.send_json({"type": "ready", "source": source.label, "width": 1280, "height": 720,
                                "quality": 78, "mode": "one-in-flight"})
            sequence, previous_id, next_due, failures = 0, -1, 0, 0
            while not ws.closed:
                await asyncio.sleep(max(.001, (next_due-mono_ms())/1000))
                cycle = mono_ms()
                try:
                    frame = await asyncio.to_thread(read_latest, source)
                except (OSError, ValueError):
                    failures += 1
                    self.events["input_errors"] += 1
                    if failures >= 10:
                        raise OSError("Input unavailable")
                    next_due = mono_ms()+50
                    continue
                failures = 0
                if frame.identity == previous_id:
                    next_due = mono_ms()+3
                    continue
                previous_id = frame.identity
                packet, meta = await asyncio.to_thread(encode, frame, sequence)
                sent = mono_ms()
                await asyncio.wait_for(ws.send_bytes(packet), 2)
                ack = await receive_json(ws, 3)
                if not isinstance(ack, dict) or ack.get("type") != "ack" or type(ack.get("seq")) is not int or ack["seq"] != sequence:
                    raise ValueError("Ordered frame acknowledgement required")
                self.last_ack_ms = round(mono_ms()-sent, 2)
                self.last_frame = {"seq": sequence, "bytes": len(packet), "encode_ms": meta["encode_ms"],
                                   "captured_ms": meta["captured_ms"]}
                self.events["sent_frames"] += 1
                self.events["sent_bytes"] += len(packet)
                self.events["max_in_flight"] = 1
                sequence += 1
                # Read the next source frame only after delivery credit returns.
                next_due = max(cycle+1000/fps, sent+len(packet)*8/kbps)
        except ViewerClosed:
            pass
        except asyncio.TimeoutError:
            self.events["timeouts"] += 1
            await ws.close(code=1013, message=b"Freshness timeout")
        except (ValueError, TypeError, KeyError):
            self.events["invalid"] += 1
            await ws.close(code=1008, message=b"Invalid session")
        except (OSError, ConnectionError):
            self.events["input_or_connection_error"] += 1
            await ws.close(code=1013, message=b"Input or connection unavailable")
        except Exception:
            LOG.exception("JPEG experiment failed")
            await ws.close(code=1011, message=b"Server error")
        finally:
            self.sessions.pop(id(ws), None)
            if source is not self.shared:
                source.close()
            await ws.close()
        return ws


async def static(request):
    name = request.match_info.get("name") or "index.html"
    if name not in ("index.html", "player.js", "app.js", "integrity.js", "telemetry.js", "style.css", "mobile.css", "protocol.js", "lucide.min.js"):
        raise web.HTTPNotFound()
    if name == "index.html":
        path = ROOT / "web" / "player.html"
    elif name in ("player.js", "app.js", "integrity.js"):
        path = ROOT / "web" / name
    elif name in ("protocol.js", "lucide.min.js"):
        path = ROOT.parent / "web" / name
    else:
        path = ROOT.parent / "experiment_v05" / "web" / name
    return web.FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def application(source="fixture", source_path=None):
    service = FreshService(source, source_path)
    app = web.Application(client_max_size=8192)
    app.router.add_get("/api/status", service.status)
    app.router.add_get("/api/diagnostics", service.diagnostics)
    app.router.add_post("/api/diagnostics", service.diagnostics)
    app.router.add_get("/clock", service.clock)
    app.router.add_get("/stream", service.media_stream)
    app.router.add_get("/jpeg-probe", service.stream)
    app.router.add_get("/", static)
    app.router.add_get("/{name}", static)
    app.on_shutdown.append(service.cleanup)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--source", choices=("fixture", "shm"), default="fixture")
    parser.add_argument("--source-path")
    args = parser.parse_args()
    if args.source == "shm" and not args.source_path:
        parser.error("Live input requires --source-path")
    web.run_app(application(args.source, args.source_path), host=args.host, port=args.port, access_log=None)
