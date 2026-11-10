"""Three-position inspection and simulated equipment interlock."""

from dataclasses import dataclass
import time

import cv2
import numpy as np

from semantic_video.model import Region


WIDTH, HEIGHT = 1280, 720
SLOTS = (
    Region("slot-1", .115, .345, .17, .30, 8),
    Region("slot-2", .415, .345, .17, .30, 8),
    Region("slot-3", .715, .345, .17, .30, 8),
)
SCENARIOS = ("OK", "SLOT_2_MISSING", "SLOT_3_MISPLACED", "SLOT_1_OCCLUDED")


def clock_ms():
    return time.perf_counter_ns() / 1e6


class AssemblyFixture:
    """Deterministic synthetic input; never represents measured factory accuracy."""

    def __init__(self):
        image = np.full((HEIGHT, WIDTH, 3), (57, 66, 70), dtype=np.uint8)
        cv2.rectangle(image, (42, 125), (WIDTH - 42, HEIGHT - 86), (92, 103, 105), -1)
        cv2.rectangle(image, (42, 125), (WIDTH - 42, HEIGHT - 86), (140, 149, 151), 3)
        for i, region in enumerate(SLOTS, 1):
            x, y, w, h = region.rect(WIDTH, HEIGHT)
            cv2.rectangle(image, (x, y), (x + w, y + h), (184, 190, 191), -1)
            cv2.rectangle(image, (x, y), (x + w, y + h), (36, 43, 47), 4)
            cv2.putText(image, f"{i:02d}", (x + 12, y + 36), cv2.FONT_HERSHEY_SIMPLEX,
                        .85, (67, 74, 76), 2, cv2.LINE_AA)
        self.empty = image

    def frame(self, scenario="OK"):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}")
        image = self.empty.copy()
        for i, region in enumerate(SLOTS, 1):
            if scenario == "SLOT_2_MISSING" and i == 2:
                continue
            x, y, w, h = region.rect(WIDTH, HEIGHT)
            cx, cy = x + w // 2, y + h // 2
            if scenario == "SLOT_3_MISPLACED" and i == 3:
                cx += 64
            cv2.circle(image, (cx, cy), 41, (32, 91, 171), -1, cv2.LINE_AA)
            cv2.circle(image, (cx, cy), 22, (77, 139, 213), -1, cv2.LINE_AA)
            cv2.circle(image, (cx, cy), 8, (178, 200, 221), -1, cv2.LINE_AA)
        if scenario == "SLOT_1_OCCLUDED":
            x, y, w, h = SLOTS[0].rect(WIDTH, HEIGHT)
            cv2.rectangle(image, (x + 12, y + 12), (x + w - 12, y + h - 12),
                          (73, 127, 183), -1)
        return image


@dataclass(frozen=True)
class Inspection:
    frame_id: int
    captured_ms: float
    decided_ms: float
    statuses: tuple[str, ...]
    source: str = "SYNTHETIC_ASSEMBLY_FIXTURE"

    def age_ms(self, now_ms):
        return now_ms - self.captured_ms

    def is_fresh(self, now_ms, max_age_ms=300):
        return 0 <= self.age_ms(now_ms) <= max_age_ms

    def is_ready(self, now_ms):
        return self.is_fresh(now_ms) and all(s == "PRESENT" for s in self.statuses)


class SlotDetector:
    def __init__(self, empty_image, settle_frames=3):
        if empty_image.shape != (HEIGHT, WIDTH, 3):
            raise ValueError("Expected a 1280x720 BGR empty reference")
        if settle_frames < 1:
            raise ValueError("settle_frames must be positive")
        self.empty = empty_image.copy()
        self.settle_frames = settle_frames
        self.candidates = [None] * len(SLOTS)
        self.counts = [0] * len(SLOTS)
        self.confirmed = ["UNKNOWN"] * len(SLOTS)

    def _raw(self, image, region):
        x, y, w, h = region.rect(WIDTH, HEIGHT)
        current = image[y:y + h, x:x + w]
        reference = self.empty[y:y + h, x:x + w]
        diff = cv2.absdiff(current, reference)
        gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 35, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        foreground_ratio = cv2.countNonZero(mask) / (w * h)
        if foreground_ratio > .40:
            return "UNKNOWN"
        components, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        if components < 2:
            return "MISSING"
        index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 1300:
            return "MISSING"
        if area > w * h * .40:
            return "UNKNOWN"
        cx, cy = centroids[index]
        if abs(cx - w / 2) > w * .18 or abs(cy - h / 2) > h * .18:
            return "MISPLACED"
        return "PRESENT"

    def inspect(self, image, frame_id, captured_ms, now_ms=None):
        if image.shape != (HEIGHT, WIDTH, 3):
            raise ValueError("Expected a 1280x720 BGR frame")
        now_ms = clock_ms() if now_ms is None else now_ms
        if not 0 <= now_ms - captured_ms <= 300:
            self.confirmed = ["UNKNOWN"] * len(SLOTS)
            self.candidates = [None] * len(SLOTS)
            self.counts = [0] * len(SLOTS)
            return Inspection(frame_id, captured_ms, now_ms, tuple(self.confirmed))
        for i, region in enumerate(SLOTS):
            raw = self._raw(image, region)
            if raw == "UNKNOWN":
                self.confirmed[i] = "UNKNOWN"
                self.candidates[i] = None
                self.counts[i] = 0
                continue
            if raw == self.candidates[i]:
                self.counts[i] += 1
            else:
                self.candidates[i], self.counts[i] = raw, 1
            if self.counts[i] >= self.settle_frames:
                self.confirmed[i] = raw
            elif raw != self.confirmed[i]:
                self.confirmed[i] = "UNKNOWN"
        return Inspection(frame_id, captured_ms, now_ms, tuple(self.confirmed))


@dataclass(frozen=True)
class EquipmentOutput:
    state: str
    conveyor: bool
    glue: bool
    fault: bool
    reason: str


class SimulatedCell:
    """Software-only sequence. No GPIO, fieldbus, or physical output."""

    def __init__(self, move_ms=800, glue_ms=400):
        self.move_ms = move_ms
        self.glue_ms = glue_ms
        self.state = "BLOCKED"
        self.stage_started_ms = 0.0
        self.reason = "Waiting for stable inspection"

    def tick(self, inspection, now_ms, *, start=False, reset=False, stop=False,
             guard_closed=True):
        if stop:
            self.state, self.reason = "FAULT", "Operator stop"
        elif not guard_closed:
            self.state, self.reason = "FAULT", "Guard open"
        elif not inspection.is_fresh(now_ms):
            self.state = "FAULT" if self.state in ("MOVE", "GLUE") else "BLOCKED"
            self.reason = "Inspection expired"
        elif not inspection.is_ready(now_ms):
            self.state = "FAULT" if self.state in ("MOVE", "GLUE") else "BLOCKED"
            self.reason = ", ".join(f"{SLOTS[i].name}: {value}"
                                    for i, value in enumerate(inspection.statuses)
                                    if value != "PRESENT")
        elif self.state == "FAULT":
            if reset:
                self.state, self.reason = "READY", ""
        elif self.state == "BLOCKED":
            self.state, self.reason = "READY", ""
        elif self.state == "COMPLETE":
            if reset:
                self.state, self.reason = "READY", ""
        elif self.state == "READY" and start:
            self.state, self.stage_started_ms = "MOVE", now_ms
        elif self.state == "MOVE" and now_ms - self.stage_started_ms >= self.move_ms:
            self.state, self.stage_started_ms = "GLUE", now_ms
        elif self.state == "GLUE" and now_ms - self.stage_started_ms >= self.glue_ms:
            self.state = "COMPLETE"
        return EquipmentOutput(self.state, self.state == "MOVE", self.state == "GLUE",
                               self.state == "FAULT", self.reason)
