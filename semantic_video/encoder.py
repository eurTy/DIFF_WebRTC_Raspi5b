from dataclasses import asdict
import time

import cv2
import numpy as np

from .model import BudgetController, DEFAULT_REGIONS
from .protocol import pack
from .source import mono_ms

MODES = ("full", "uniform", "static-roi", "semantic")
UNIFORM = [(1280, 85), (960, 65), (640, 50), (384, 35), (256, 25)]
SEMANTIC = [(640, 45, 90, 1), (480, 35, 85, 1), (320, 25, 80, 1), (240, 20, 65, .75), (160, 15, 50, .5)]


def jpeg(image, width, quality):
    if image.shape[1] != width:
        image = cv2.resize(image, (width, max(1, round(image.shape[0] * width / image.shape[1]))), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return data.tobytes()


class Encoder:
    def __init__(self, mode="semantic", regions=None, kbps=1200, fps=10):
        if mode not in MODES:
            raise ValueError("Unknown encoding policy")
        self.mode, self.regions = mode, regions or list(DEFAULT_REGIONS)
        self.control = BudgetController(kbps, fps)
        self.previous = {}
        self.refresh = {}
        self.last_level = 2
        self.count = 0
        self.started_ms = mono_ms()

    def priorities(self, image, now):
        ranked = []
        for region in self.regions:
            x, y, w, h = region.rect(1280, 720)
            small = cv2.resize(cv2.cvtColor(image[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY), (32, 32))
            old = self.previous.get(region.name)
            change = float(np.abs(small.astype(np.int16) - old).mean() / 255) if old is not None else 0
            self.previous[region.name] = small.astype(np.int16)
            age = max(0, now - self.refresh.get(region.name, self.started_ms))
            score = region.weight if self.mode == "static-roi" else region.weight * (1 + min(change * 12, 2)) + min(age / 250, 12)
            ranked.append((score, region, change, age))
        return sorted(ranked, key=lambda item: (-item[0], item[1].name))

    def candidate(self, frame, level, ranked, budget):
        if self.mode in ("full", "uniform"):
            width, quality = UNIFORM[0 if self.mode == "full" else level]
            return [{"name": "base", "rect": [0, 0, 1280, 720], "quality": quality}], [jpeg(frame.image, width, quality)]
        width, quality, roi_quality, roi_scale = SEMANTIC[level]
        parts = [{"name": "base", "rect": [0, 0, 1280, 720], "quality": quality}]
        images = [jpeg(frame.image, width, quality)]
        # Reserve bounded metadata space; wire size is checked again after packing.
        remaining = budget - len(images[0]) - 1300
        for priority, region, change, age in ranked:
            x, y, w, h = region.rect(1280, 720)
            image = jpeg(frame.image[y:y+h, x:x+w], max(16, round(w * roi_scale)), roi_quality)
            if len(image) > remaining:
                continue
            remaining -= len(image)
            parts.append({"name": region.name, "rect": [x, y, w, h], "quality": roi_quality,
                          "priority": round(priority, 3), "change": round(change, 5)})
            images.append(image)
        return parts, images

    def encode(self, frame):
        started = time.perf_counter()
        now = mono_ms()
        budget = self.control.bytes()
        ranked = self.priorities(frame.image, now)
        attempts = 0
        first = 0 if self.mode == "full" else max(0, self.last_level - (self.count % 8 == 0))
        chosen = None
        for level in range(first, 5):
            parts, images = self.candidate(frame, level, ranked, budget)
            attempts += 1
            base = {"id": frame.identity, "captured_ms": frame.captured_ms, "sent_ms": mono_ms(),
                    "width": 1280, "height": 720, "source": frame.source, "seed": frame.seed,
                    "policy": self.mode, "level": level, "budget_bytes": budget,
                    "parts": parts, "regions": [asdict(r) for r in self.regions],
                    "encode_ms": (time.perf_counter()-started)*1000, "attempts": attempts,
                    "budget_exceeded": False}
            message = pack(base, images)
            chosen = (level, base, images, message)
            priority_present = self.mode in ("full", "uniform") or any(p["name"] == ranked[0][1].name for p in parts[1:])
            if self.mode == "full" or len(message) <= budget and priority_present:
                break
        level, base, images, message = chosen
        base["encode_ms"] = round((time.perf_counter()-started)*1000, 3)
        base["sent_ms"] = round(mono_ms(), 3)
        message = pack(base, images)
        if len(message) > budget:
            base["budget_exceeded"] = True
            message = pack(base, images)
        self.last_level = level
        self.count += 1
        return message, base

    def acknowledge(self, metadata, service_ms, packet_bytes, now=None):
        now = mono_ms() if now is None else now
        self.control.observe(service_ms, packet_bytes)
        for part in metadata["parts"][1:]:
            self.refresh[part["name"]] = now
