"""Explicit synthetic, USB and read-only legacy-boundary input adapters."""
import math
import struct
import time
import threading

import cv2
import numpy as np

from .model import DEFAULT_REGIONS, Frame


def mono_ms():
    # Linux SHM uses CLOCK_MONOTONIC; QPC avoids coarse Windows tick timestamps.
    return (time.clock_gettime_ns(time.CLOCK_MONOTONIC) if hasattr(time, "CLOCK_MONOTONIC") else time.perf_counter_ns()) / 1e6


class FixtureSource:
    label = "SYNTHETIC_TASK_FIXTURE"

    def __init__(self, seed=7):
        self.seed, self.start = seed, mono_ms()
        rng = np.random.default_rng(seed)
        noise = rng.integers(0, (8, 40, 100)[seed % 3], (720, 1280, 1), dtype=np.uint8)
        self.background = np.repeat(noise, 3, axis=2) + np.array([65, 55, 45], dtype=np.uint8)
        for x in range(0, 1280, 64):
            cv2.line(self.background, (x, 0), (x, 719), (125, 135, 135), 1)
        for y in range(0, 720, 48):
            cv2.line(self.background, (0, y), (1279, y), (135, 135, 125), 1)

    def at(self, identity, captured_ms=0):
        image = np.roll(self.background, (identity // 2) % 1280, axis=1).copy()
        cv2.rectangle(image, (36, 30), (650, 89), (30, 36, 40), -1)
        cv2.putText(image, "CONTROLLED TASK FIXTURE", (52, 68), cv2.FONT_HERSHEY_SIMPLEX, .9, (245, 245, 245), 2)
        t = identity / 30
        for i, region in enumerate(DEFAULT_REGIONS):
            x, y, w, h = region.rect(1280, 720)
            cv2.rectangle(image, (x, y), (x + w - 1, y + h - 1), (210, 215, 218), -1)
            for yy in range(y + 12, y + h - 12, 5):
                cv2.line(image, (x + 10, yy), (x + w - 11, yy), (75, 75, 75), 1)
            phase = int(t * (1 if i == 0 else 2)) % 2
            color = (30, 200, 50) if phase else (35, 45, 220)
            px = x + 20 + int((w - 40) * (.5 + .45 * math.sin(t * (1.7 + i) + self.seed)))
            py = y + 22 + int((h - 44) * (.5 + .4 * math.cos(t * 1.1 + i)))
            cv2.circle(image, (px, py), 6 if i == 0 else 4, color, -1, cv2.LINE_AA)
            cv2.putText(image, f"TASK {chr(65+i)}", (x + 12, y + h - 10), cv2.FONT_HERSHEY_SIMPLEX, .45, (15, 15, 15), 1)
        cv2.putText(image, f"FRAME {identity:06d}", (50, 670), cv2.FONT_HERSHEY_SIMPLEX, .7, (245, 245, 245), 2)
        return Frame(image, identity, captured_ms, self.label, self.seed)

    def read(self):
        now = mono_ms()
        return self.at(int((now - self.start) * .03), now)

    def close(self):
        pass


class SharedMemorySource:
    label = "LIVE_READ_ONLY_ADAPTER"

    def __init__(self, path):
        self.path = path

    def read(self):
        # This adapter implements the documented input ABI; no old Python module is imported.
        with open(self.path, "rb", buffering=0) as source:
            first = source.read(48)
            if len(first) != 48:
                raise ValueError("Short input header")
            identity, _, _, size, sequence, _, flags = struct.unpack_from("<7I", first)
            published = struct.unpack_from("<Q", first, 40)[0]
            if sequence & 1 or flags & 1 or not 4 <= size <= 8 * 1024 * 1024:
                raise ValueError("Uncommitted input")
            jpeg = source.read(size)
            source.seek(0)
            if source.read(48) != first or len(jpeg) != size:
                raise ValueError("Input overwritten during read")
        age = mono_ms() - published
        if not 0 <= age <= 500:
            raise ValueError("Input is stale or timestamp invalid")
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (720, 1280):
            raise ValueError("Expected a valid 1280x720 source")
        return Frame(image, identity, published, self.label)

    def close(self):
        pass


class CameraSource:
    label = "LIVE_USB_NEW_CAPTURE"

    def __init__(self, device):
        self.capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
        self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.capture.set(cv2.CAP_PROP_FPS, 30)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.identity = 0
        if not self.capture.isOpened():
            raise OSError("Camera unavailable; never stop another owner automatically")
        self.lock, self.latest, self.stopped = threading.Lock(), None, threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            ok, image = self.capture.read()
            now = mono_ms()
            if not ok or image.shape[:2] != (720, 1280):
                self.stopped.wait(.1)
                continue
            self.identity += 1
            with self.lock:
                self.latest = Frame(image, self.identity, now, self.label)

    def read(self):
        with self.lock:
            if self.latest is None or mono_ms()-self.latest.captured_ms > 500:
                raise OSError("Camera is not producing fresh frames")
            frame = self.latest
            return Frame(frame.image.copy(), frame.identity, frame.captured_ms, frame.source)

    def close(self):
        self.stopped.set()
        self.capture.release()
        self.thread.join(timeout=1)
