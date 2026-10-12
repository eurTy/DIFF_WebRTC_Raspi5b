from dataclasses import dataclass
from collections import deque
import math


@dataclass(frozen=True)
class Region:
    name: str
    x: float
    y: float
    w: float
    h: float
    weight: float

    def rect(self, width, height):
        x, y = int(self.x * width), int(self.y * height)
        return x, y, max(1, min(width - x, round(self.w * width))), max(1, min(height - y, round(self.h * height)))


DEFAULT_REGIONS = [Region("process-A", .075, .19, .275, .28, 6), Region("process-B", .70, .48, .18, .22, 4)]


def regions_from_json(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 4:
        raise ValueError("One to four task regions required")
    result, names = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"name", "x", "y", "w", "h", "weight"}:
            raise ValueError("Invalid region schema")
        name = value["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 32 or name in names:
            raise ValueError("Unique short region names required")
        numbers = [value[key] for key in ("x", "y", "w", "h", "weight")]
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
            raise ValueError("Finite region numbers required")
        x, y, w, h, weight = numbers
        if x < 0 or y < 0 or w < .02 or h < .02 or x + w > 1 or y + h > 1 or not 1 <= weight <= 10:
            raise ValueError("Region outside image or weight range")
        names.add(name)
        result.append(Region(name, x, y, w, h, weight))
    if sum(r.w * r.h for r in result) > .5:
        raise ValueError("Task regions may cover at most half the image")
    return result


@dataclass
class Frame:
    image: object
    identity: int
    captured_ms: float
    source: str
    seed: int = 0


class BudgetController:
    """ACK-service-time feedback, not a claim to estimate physical link capacity."""
    def __init__(self, kbps=1200, fps=10, feedback="single"):
        if not math.isfinite(kbps) or not 128 <= kbps <= 12000 or not math.isfinite(fps) or not 2 <= fps <= 20:
            raise ValueError("Budget or FPS outside laboratory bounds")
        if feedback not in ("single", "median"):
            raise ValueError("Unknown feedback filter")
        self.feedback = feedback
        self.history = deque(maxlen=5)
        self.fps = fps
        self.kbps = kbps
        self.period_ms = 1000 / fps
        self.ceiling = kbps * 1000 / 8 / fps
        self.budget = self.ceiling
        self.good = 0
        self.acks = 0

    def observe(self, service_ms, packet_bytes):
        if not math.isfinite(service_ms) or service_ms <= 0 or packet_bytes <= 0:
            return
        self.acks += 1
        if self.feedback == "median":
            self.history.append((service_ms, packet_bytes))
            if len(self.history) < 5:
                return
            # Retain the payload paired with the median service time, not an unrelated last size.
            service_ms, packet_bytes = sorted(self.history, key=lambda pair: pair[0])[2]
        if service_ms > self.period_ms * 1.25:
            self.budget = max(min(1400, self.ceiling), min(self.budget * .78, packet_bytes * self.period_ms / service_ms * .85))
            self.good = 0
        elif service_ms < self.period_ms * .8:
            self.good += 1
            if self.good >= 3:
                self.budget = min(self.ceiling, self.budget * 1.12 + 100)
                self.good = 0
        else:
            self.good = 0

    def bytes(self):
        return round(self.budget)
