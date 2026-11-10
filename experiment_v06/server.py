import argparse
import asyncio
import base64
from collections import deque
from contextlib import suppress
import json
import hashlib
import hmac
import logging
import math
import os
from pathlib import Path
import time

from aiohttp import web, WSMsgType
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription, RTCRtpSender
from aiortc.sdp import candidate_from_sdp, SessionDescription

from semantic_video.server import Service, check_origin
from semantic_video.source import FixtureSource, mono_ms
from .media import LatestTrack, QualityEncoder, instrument_sender

ROOT = Path(__file__).parent
LOG = logging.getLogger(__name__)
REPORT_KEYS = ("age_ms", "latency_ms", "fps", "jitter_buffer_ms", "rtt_ms", "mbps", "width", "height", "lost", "freeze_count")
DIAGNOSTIC_KEYS = ("event", "message", "secure", "webrtc", "ice", "stage", "route",
                   "connection", "ice_connection", "host", "srflx", "relay", "error_code")


def private_answer(sdp, host):
    if not host:
        return sdp
    description = SessionDescription.parse(sdp)
    for media in description.media:
        media.ice_candidates = [c for c in media.ice_candidates if c.ip == host and c.protocol == "udp"]
        if not media.ice_candidates:
            raise ValueError("Private VPN interface not ready")
        media.host, media.port = host, media.ice_candidates[0].port
    return str(description)


def settings(data):
    kbps, fps = data.get("kbps", 2400), data.get("fps", 20)
    if type(kbps) is not int or not 1200 <= kbps <= 6000 or type(fps) is not int or not 10 <= fps <= 20:
        raise ValueError("Settings out of bounds")
    if not isinstance(data.get("sdp"), str) or len(data["sdp"]) > 60000:
        raise ValueError("Invalid SDP")
    lines = data["sdp"].splitlines()
    if sum(line.startswith("m=video ") for line in lines) != 1 or any(line.startswith("m=audio ") for line in lines):
        raise ValueError("One receive-only video track required")
    return kbps, fps


class RtcService(Service):
    def __init__(self, *args):
        super().__init__(*args)
        self.reports = deque(maxlen=300)
        self.report_time = 0
        self.peer = None
        self.track = None
        self.diagnostic = None
        self.diagnostic_time = 0
        self.last_failure = None
        self.diagnostic_history = deque(maxlen=40)
        self.last_server_failure = None
        self.signal_stage = "idle"
        self.remote_candidates = {}

    async def status(self, request):
        return web.json_response({"project": "semantic-rtc-v06", "source": self.source_kind,
            "sessions": len(self.sessions), "viewer_limit": 1, "resolution": [1280, 720],
            "transport": "WebRTC/DTLS-SRTP/UDP", "legacy_services_modified": False,
            "connection": self.peer.connectionState if self.peer else "idle",
            "ice": self.peer.iceConnectionState if self.peer else "idle",
            "encode_ms": round(self.track.encoder.encode_ms, 2) if self.track else None,
            "effective_fps": self.track.encoder.effective_fps if self.track else None,
            "input_errors": self.track.input_errors if self.track else None,
            "client_report": self.reports[-1] if self.reports else None,
            "client_report_age_ms": round(mono_ms()-self.report_time) if self.reports else None,
            "client_samples": len(self.reports), "events": dict(self.events),
            "client_diagnostic": self.diagnostic,
            "last_client_failure": self.last_failure,
            "diagnostic_history": list(self.diagnostic_history),
            "last_server_failure": self.last_server_failure,
            "signal_stage": self.signal_stage, "remote_candidates": self.remote_candidates,
            "diagnostic_age_ms": round(mono_ms()-self.diagnostic_time) if self.diagnostic else None})

    async def diagnose(self, request):
        check_origin(request)
        data = await request.json()
        if not isinstance(data, dict) or len(json.dumps(data)) > 2048:
            raise web.HTTPBadRequest()
        diagnostic = {k: str(data.get(k, ""))[:240] for k in DIAGNOSTIC_KEYS}
        if data.get("event") == "failure":
            self.last_failure = diagnostic
        if diagnostic != self.diagnostic or mono_ms()-self.diagnostic_time > 1000:
            self.diagnostic = diagnostic
            self.diagnostic_time = mono_ms()
            self.diagnostic_history.append({"at_ms": round(self.diagnostic_time), **diagnostic})
        return web.json_response({"ok": True})

    async def ice_config(self, request):
        check_origin(request)
        secret, host = os.environ.get("RTC_TURN_SECRET"), os.environ.get("RTC_TAILNET_IP")
        servers = []
        if secret and host:
            username = f"{int(time.time())+3600}:viewer"
            credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
            servers = [{"urls": [f"turn:{host}:3478?transport=udp", f"turn:{host}:3478?transport=tcp"],
                        "username": username, "credential": credential}]
        return web.json_response({"iceServers": servers}, headers={"Cache-Control": "no-store"})

    async def signal(self, request):
        check_origin(request)
        if self.sessions:
            raise web.HTTPServiceUnavailable(text="Experiment viewer already active")
        ws = web.WebSocketResponse(max_msg_size=65536, heartbeat=10, timeout=1, compress=False)
        self.sessions[id(ws)] = ws
        pc = track = None
        watchdog = None
        try:
            self.signal_stage = "await-offer"
            self.remote_candidates = {}
            await ws.prepare(request)
            data = await asyncio.wait_for(ws.receive_json(), 10)
            if not isinstance(data, dict) or data.get("type") != "offer":
                raise ValueError("Offer required")
            kbps, fps = settings(data)
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            self.peer = pc
            encoder = QualityEncoder(kbps, fps)
            track = LatestTrack(self.shared if self.shared else FixtureSource(7), encoder)
            self.track = track
            channel = None
            self.reports.clear()
            self.report_time = 0
            last_frame = mono_ms()

            def emit(meta):
                nonlocal last_frame
                last_frame = mono_ms()
                self.events["sent_frames"] += 1
                if channel and channel.readyState == "open" and channel.bufferedAmount < 4096:
                    channel.send(json.dumps(meta))
                else:
                    self.events["metadata_skipped"] += 1

            @pc.on("datachannel")
            def on_channel(opened):
                nonlocal channel
                if opened.label != "timing" or channel is not None:
                    opened.close()
                    return
                channel = opened

            @pc.on("connectionstatechange")
            async def on_state():
                self.signal_stage = pc.connectionState
                if pc.connectionState == "failed":
                    self.last_server_failure = {"stage": "connection", "error": "PeerConnectionFailed"}
                    await ws.close(code=1013, message=b"ICE or media connection failed")

            sender = pc.addTrack(track)
            codecs = [c for c in RTCRtpSender.getCapabilities("video").codecs
                      if c.mimeType.lower() == "video/h264" and c.parameters.get("packetization-mode") == "1"]
            if not codecs:
                raise ValueError("H264 unavailable")
            for transceiver in pc.getTransceivers():
                if transceiver.kind == "video":
                    transceiver.setCodecPreferences(codecs)
                    transceiver.direction = "sendonly"
            # Preferences must precede remote negotiation; otherwise a VP8-first
            # offer can bind VP8 payload types to our pinned H264 encoder.
            self.signal_stage = "remote-offer"
            await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
            instrument_sender(sender, track, encoder, emit)
            self.signal_stage = "local-answer"
            await asyncio.wait_for(pc.setLocalDescription(await pc.createAnswer()), 10)
            answer = private_answer(pc.localDescription.sdp, os.environ.get("RTC_TAILNET_IP"))
            await ws.send_json({"type": "answer", "sdp": answer,
                                "source": track.source.label, "kbps": kbps, "fps": fps})
            self.signal_stage = "await-media"

            async def monitor():
                started = mono_ms()
                while not ws.closed:
                    await asyncio.sleep(1)
                    if mono_ms()-started > 20000 and (pc.connectionState != "connected" or mono_ms()-last_frame > 5000):
                        self.last_server_failure = {"stage": "await-media", "error": "NoFreshMedia",
                                                    "connection": pc.connectionState, "ice": pc.iceConnectionState}
                        await ws.close(code=1013, message=b"No fresh media")
                        return
            watchdog = asyncio.create_task(monitor())
            candidate_count = 0
            async for incoming in ws:
                if incoming.type != WSMsgType.TEXT:
                    break
                report = json.loads(incoming.data)
                if isinstance(report, dict) and report.get("type") == "candidate":
                    candidate_count += 1
                    if candidate_count > 64:
                        raise ValueError("Too many ICE candidates")
                    value = report.get("candidate")
                    if value is None:
                        self.remote_candidates["complete"] = True
                        await pc.addIceCandidate(None)
                    else:
                        if not isinstance(value, dict) or not isinstance(value.get("candidate"), str) or len(value["candidate"]) > 2048:
                            raise ValueError("Invalid ICE candidate")
                        if not value["candidate"]:
                            self.remote_candidates["complete"] = True
                            await pc.addIceCandidate(None)
                        else:
                            candidate = candidate_from_sdp(value["candidate"].removeprefix("candidate:"))
                            kind = candidate.type if candidate.type in ("host", "srflx", "prflx", "relay") else "other"
                            self.remote_candidates[kind] = self.remote_candidates.get(kind, 0)+1
                            candidate.sdpMid = value.get("sdpMid")
                            candidate.sdpMLineIndex = value.get("sdpMLineIndex")
                            await pc.addIceCandidate(candidate)
                    continue
                if not isinstance(report, dict) or report.get("type") != "display":
                    raise ValueError("Display feedback required")
                if mono_ms()-self.report_time < 400:
                    # TCP may deliver several 1 Hz reports together after a gap.
                    # They must not tear down a healthy, separate UDP media path.
                    continue
                if any(report.get(k) is not None and (type(report[k]) not in (int, float)
                       or not math.isfinite(report[k]) or not 0 <= report[k] <= 1000000) for k in REPORT_KEYS):
                    raise ValueError("Invalid numeric feedback")
                self.reports.append({k: report.get(k) for k in REPORT_KEYS})
                self.report_time = mono_ms()
        except (ValueError, TypeError, KeyError) as error:
            self.events["invalid"] += 1
            self.last_server_failure = {"stage": self.signal_stage, "error": type(error).__name__}
            await ws.close(code=1008, message=b"Invalid WebRTC session")
        except asyncio.TimeoutError:
            self.last_server_failure = {"stage": self.signal_stage, "error": "TimeoutError"}
            await ws.close(code=1013, message=b"Signaling timeout")
        except (OSError, ConnectionError):
            self.last_server_failure = {"stage": self.signal_stage, "error": "ConnectionUnavailable"}
            await ws.close(code=1013, message=b"Connection unavailable")
        except Exception:
            self.last_server_failure = {"stage": self.signal_stage, "error": "InternalError"}
            LOG.exception("WebRTC session failed")
            await ws.close(code=1011, message=b"WebRTC error")
        finally:
            if watchdog:
                watchdog.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog
            if track:
                track.stop()
            if pc:
                # Windows Proactor UDP close can wait indefinitely for
                # connection_lost after ICMP errors. Bound session release.
                try:
                    await asyncio.wait_for(pc.close(), 2)
                except asyncio.TimeoutError:
                    self.events["close_timeouts"] += 1
            self.peer = self.track = None
            self.signal_stage = "idle"
            self.sessions.pop(id(ws), None)
            await ws.close()
        return ws


async def static(request):
    name = request.match_info.get("name") or "index.html"
    if name not in ("index.html", "app.js", "style.css", "protocol.js", "lucide.min.js"):
        raise web.HTTPNotFound()
    path = ROOT.parent / "web" / name if name in ("protocol.js", "lucide.min.js") else ROOT / "web" / name
    return web.FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def application(source="fixture", source_path=None):
    service = RtcService(source, source_path)
    app = web.Application(client_max_size=65536)
    app.router.add_get("/api/status", service.status)
    app.router.add_post("/api/client", service.diagnose)
    app.router.add_get("/api/ice", service.ice_config)
    app.router.add_get("/clock", service.clock)
    app.router.add_get("/signal", service.signal)
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
