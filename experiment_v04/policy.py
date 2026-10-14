import cv2
import numpy as np

from semantic_video.encoder import Encoder
from semantic_video.source import mono_ms


class TaskFreshnessEncoder(Encoder):
    """Rank one enhancement by confirmed visibility, change, and byte cost."""

    def __init__(self, regions, kbps, fps):
        super().__init__("semantic", regions, kbps, fps, "single")
        self.last_piece_bytes = {}

    def priorities(self, image, now):
        ranked = []
        for region in self.regions:
            x, y, w, h = region.rect(1280, 720)
            small = cv2.resize(cv2.cvtColor(image[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY), (32, 32))
            old = self.previous.get(region.name)
            change = float(np.abs(small.astype(np.int16) - old).mean() / 255) if old is not None else 0.0
            self.previous[region.name] = small.astype(np.int16)
            age = max(0.0, now - self.refresh.get(region.name, self.started_ms))
            # Confirmed display age is a task debt, not merely time since send.
            debt = 1.0 + min(age / 250.0, 6.0)
            expected_bytes = self.last_piece_bytes.get(region.name, max(2000, int(w * h * .035)))
            score = region.weight * (1.0 + min(change * 8.0, 2.0)) * debt / expected_bytes
            ranked.append((score, region, change, age))
        return sorted(ranked, key=lambda item: (-item[0], item[1].name))

    def candidate(self, frame, level, ranked, budget):
        return super().candidate(frame, level, ranked[:1], budget)

    def visible(self, name, packet_bytes, service_ms, piece_bytes=0):
        self.control.observe(service_ms, packet_bytes)
        if name:
            self.refresh[name] = mono_ms()
            self.last_piece_bytes[name] = piece_bytes
