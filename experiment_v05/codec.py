"""Native 720p H.264 and independent JPEG controls, using library codecs."""
from fractions import Fraction
import io
import json
import struct
import time

import av
import cv2

from semantic_video.encoder import jpeg


def envelope(meta, data):
    raw = json.dumps(meta, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 16384 or len(data) > 2 * 1024 * 1024:
        raise ValueError("Packet too large")
    return b"SVP5" + struct.pack("!I", len(raw)) + raw + data


class Drain(io.RawIOBase):
    def __init__(self):
        super().__init__()
        self.data = bytearray()

    def writable(self):
        return True

    def write(self, data):
        self.data.extend(data)
        return len(data)

    def take(self):
        data = bytes(self.data)
        self.data.clear()
        return data


def task_image(image, regions, kbps, semantic):
    # Pre-filter the background only. All task regions use the SAME input frame.
    # This is not a neural semantic codec or per-macroblock QP control.
    if not semantic:
        return image
    width = 640 if kbps >= 1000 else 320
    small = cv2.resize(image, (width, width * 9 // 16), interpolation=cv2.INTER_AREA)
    result = cv2.resize(small, (1280, 720), interpolation=cv2.INTER_LINEAR)
    for region in regions:
        x, y, w, h = region.rect(1280, 720)
        result[y:y+h, x:x+w] = image[y:y+h, x:x+w]
    return result


class H264Encoder:
    def __init__(self, regions, kbps, fps, semantic=True):
        self.regions, self.kbps, self.fps, self.semantic = regions, kbps, fps, semantic
        self.sink = Drain()
        self.output = av.open(self.sink, mode="w", format="mp4", options={
            "movflags": "empty_moov+default_base_moof+frag_every_frame",
            "flush_packets": "1"})
        self.stream = self.output.add_stream("libx264", rate=fps)
        context = self.stream.codec_context
        context.width, context.height, context.pix_fmt = 1280, 720, "yuv420p"
        context.time_base = Fraction(1, fps)
        context.bit_rate = int(kbps * 1000 * .88)
        context.thread_count = 2
        context.gop_size = max(2, fps // 2)
        context.max_b_frames = 0
        context.options = {"preset": "veryfast", "tune": "zerolatency", "profile": "baseline",
                           "level": "3.1", "x264-params":
                           f"scenecut=0:rc-lookahead=0:sync-lookahead=0:vbv-maxrate={int(kbps*.88)}:vbv-bufsize={max(64,int(kbps*.22))}"}
        self.output.start_encoding()
        self.init = self.sink.take()
        # avcC contains the actual profile/compatibility/level bytes for MSE.
        index = self.init.index(b"avcC")
        self.mime = 'video/mp4; codecs="avc1.' + self.init[index+5:index+8].hex() + '"'
        self.pending = None
        self.sequence = 0

    def encode(self, frame):
        started = time.perf_counter()
        image = task_image(frame.image, self.regions, self.kbps, self.semantic)
        value = av.VideoFrame.from_ndarray(image, format="bgr24")
        value.pts, value.time_base = self.sequence, Fraction(1, self.fps)
        packets = self.stream.encode(value)
        if len(packets) != 1:
            raise ValueError("Expected one zero-reorder H.264 packet")
        packets[0].duration = 1
        self.output.mux(packets[0])
        data = self.sink.take()
        metadata = {"id": frame.identity, "captured_ms": frame.captured_ms,
                    "media_time": self.sequence / self.fps, "source": frame.source,
                    "key": packets[0].is_keyframe, "width": 1280, "height": 720,
                    "encode_ms": round((time.perf_counter()-started)*1000, 3)}
        # MOV flushes the previous fragment on the next packet. Preserve its
        # timestamp instead of falsely labelling it as the newly encoded frame.
        result = (self.pending, data) if data and self.pending is not None else None
        self.pending = metadata
        self.sequence += 1
        return result

    def close(self):
        self.output.close()
        self.sink.take()


class JpegEncoder:
    def __init__(self, regions, kbps, fps, semantic=True):
        self.regions, self.kbps, self.fps, self.semantic = regions, kbps, fps, semantic
        self.init, self.mime = None, "image/jpeg"

    def encode(self, frame):
        started = time.perf_counter()
        budget = self.kbps * 1000 / 8 / self.fps
        patches = []
        if self.semantic:
            for r in self.regions:
                x, y, w, h = r.rect(1280, 720)
                patches.append(({"name": r.name, "rect": [x,y,w,h]}, jpeg(frame.image[y:y+h,x:x+w], w, 78)))
        # Preserve a usable context floor; byte pacing reduces cadence when
        # the requested budget cannot support it. Never silently shrink to 160p.
        choices = [(1280,75),(960,65),(640,50)]
        for width, quality in choices:
            base = jpeg(frame.image, width, quality)
            if len(base) + sum(len(p[1]) for p in patches) + 1200 <= budget:
                break
        parts = [({"name":"base", "rect":[0,0,1280,720]},base)] + patches
        meta = {"id":frame.identity, "captured_ms":frame.captured_ms, "source":frame.source,
                "width":width, "height":width*9//16, "parts":[p[0] for p in parts],
                "lengths":[len(p[1]) for p in parts], "encode_ms":round((time.perf_counter()-started)*1000,3)}
        return meta, b"".join(p[1] for p in parts)

    def close(self):
        pass
