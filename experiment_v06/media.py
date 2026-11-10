"""Latest-only 720p track and version-pinned aiortc encoder instrumentation."""
import asyncio
from fractions import Fraction
import time

import aiortc
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from aiortc.codecs import h264
import av

from semantic_video.source import mono_ms

# 1100-byte payloads fit the 1280-byte tunnel MTU with RTP/SRTP/UDP headers.
h264.PACKET_MAX = 1100


def read_latest(source):
    for attempt in range(3):
        try:
            return source.read()
        except ValueError as error:
            if attempt == 2 or str(error) not in ("Input overwritten during read", "Uncommitted input"):
                raise
            time.sleep(.001)


class QualityEncoder(h264.H264Encoder):
    def __init__(self, kbps, fps):
        super().__init__()
        self.budget = int(kbps * 1000 * .88)
        self.fps = fps
        self.estimated_bps = kbps * 1000
        self.encode_ms = 0

    @property
    def target_bitrate(self):
        return self.budget

    @target_bitrate.setter
    def target_bitrate(self, value):
        # Preserve spatial quality. Receiver feedback reduces sampling frequency,
        # not resolution or CRF. Very poor paths can still stall (shown in UI).
        self.estimated_bps = max(100000, min(int(value), 10000000))

    @property
    def effective_fps(self):
        return max(4, min(self.fps, int(self.fps * self.estimated_bps / self.budget)))

    def _encode_frame(self, frame, force_keyframe):
        started = mono_ms()
        if (frame.width, frame.height) != (1280, 720):
            raise ValueError("Native 1280x720 required")
        if self.codec is None:
            self.codec = av.CodecContext.create("libx264", "w")
            self.codec.width, self.codec.height = 1280, 720
            self.codec.pix_fmt = "yuv420p"
            self.codec.time_base = Fraction(1, 90000)
            self.codec.framerate = Fraction(self.fps, 1)
            self.codec.thread_count = 2
            self.codec.bit_rate = self.budget
            self.codec.gop_size = self.fps
            self.codec.max_b_frames = 0
            self.codec.options = {
                "preset": "veryfast", "tune": "zerolatency", "profile": "baseline",
                "level": "3.1", "crf": "20",
                "x264-params": f"scenecut=0:rc-lookahead=0:sync-lookahead=0:vbv-maxrate={self.budget//1000}:vbv-bufsize={self.budget//5000}",
            }
        frame.pict_type = av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        data = b"".join(bytes(packet) for packet in self.codec.encode(frame))
        self.encode_ms = mono_ms() - started
        if data:
            yield from self._split_bitstream(data)


class LatestTrack(VideoStreamTrack):
    def __init__(self, source, encoder):
        super().__init__()
        self.source, self.encoder = source, encoder
        self.next_due = 0
        self.previous_id = None
        self.previous_pts = -1
        self.latest_meta = None
        self.input_errors = 0

    async def recv(self):
        failures = 0
        while self.readyState == "live":
            await asyncio.sleep(max(.001, (self.next_due - mono_ms()) / 1000))
            cycle_started = mono_ms()
            try:
                frame = await asyncio.to_thread(read_latest, self.source)
            except (OSError, ValueError):
                self.input_errors += 1
                failures += 1
                if failures >= 30:
                    raise MediaStreamError
                self.next_due = mono_ms() + 50
                continue
            if frame.identity == self.previous_id:
                self.next_due = mono_ms() + 3
                continue
            self.previous_id = frame.identity
            self.next_due = max(mono_ms(), cycle_started + 1000 / self.encoder.effective_fps)
            video = await asyncio.to_thread(av.VideoFrame.from_ndarray, frame.image, format="bgr24")
            video.pts = max(self.previous_pts + 1, round(frame.captured_ms * 90))
            video.time_base = Fraction(1, 90000)
            self.previous_pts = video.pts
            self.latest_meta = {"captured_ms": frame.captured_ms, "id": frame.identity}
            return video
        raise MediaStreamError


def instrument_sender(sender, track, encoder, emit):
    """Read RTP timestamp only AFTER the previous frame's entire send loop.

    aiortc adds a random RTP origin internally. At the next encode call it has
    finished the previous frame, so this mapping includes that origin exactly.
    No RTP packet construction, crypto, retransmission or pacing is replaced.
    The adapter is pinned and tested against aiortc 1.14.0.
    """
    if aiortc.__version__ != "1.14.0":
        raise RuntimeError("Review RTP timestamp adapter before upgrading aiortc")
    for name in ("_RTCRtpSender__encoder", "_RTCRtpSender__rtp_timestamp", "_RTCRtpSender__packet_count"):
        if not hasattr(sender, name):
            raise RuntimeError("Unsupported aiortc internals")
    sender._RTCRtpSender__encoder = encoder
    original = sender._next_encoded_frame
    pending = None
    count_before = 0

    async def next_frame(codec):
        nonlocal pending, count_before
        if pending and sender._RTCRtpSender__packet_count > count_before:
            emit({"type": "frame", **pending,
                  "rtp": sender._RTCRtpSender__rtp_timestamp,
                  "encode_ms": encoder.encode_ms,
                  "target_fps": encoder.effective_fps})
        pending = None
        encoded = await original(codec)
        if encoded is not None and encoded.payloads:
            pending = dict(track.latest_meta)
            count_before = sender._RTCRtpSender__packet_count
        return encoded

    sender._next_encoded_frame = next_frame
