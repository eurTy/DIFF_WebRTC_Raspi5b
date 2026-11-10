"""Bounded feedback pipeline; propagation RTT is not bandwidth."""
from collections import deque
import math


class Flow:
    def __init__(self, kbps, fps):
        self.ceiling = self.kbps = kbps
        self.fps = fps
        self.samples = deque(maxlen=120)
        self.window = 4
        self.good = 0
        self.byte_deadline = 0

    def observe(self, elapsed, now):
        if not math.isfinite(elapsed) or elapsed <= 0:
            return
        self.samples.append((now, elapsed))
        while self.samples and now - self.samples[0][0] > 10000:
            self.samples.popleft()
        baseline = min(sample for _, sample in self.samples)
        queue = max(0, elapsed-baseline)
        if queue > max(80, baseline*.5):
            self.window = 2
            self.kbps = max(min(384, self.ceiling), self.kbps*.85)
            self.good = 0
        else:
            self.window = max(2, min(8, math.ceil(baseline*self.fps/1000)+2))
            self.good += 1
            if self.good >= 20:
                self.kbps = min(self.ceiling, self.kbps*1.06+24)
                self.good = 0

    def interval(self, packet_bytes):
        return max(1000/self.fps, packet_bytes*8/self.kbps)

    def next_send(self, cycle, encoded, packet_bytes):
        # Carry at most 100 ms of unused byte credit across small P-frames.
        # Otherwise every large I-frame adds a permanent cadence penalty even
        # when the average stream is comfortably inside the configured budget.
        self.byte_deadline = max(self.byte_deadline, encoded-100) + packet_bytes*8/self.kbps
        return max(cycle+1000/self.fps, self.byte_deadline)


def valid_ack(ack, pending):
    return (isinstance(ack, dict) and ack.get("type") == "ack"
            and type(ack.get("seq")) is int and pending and ack["seq"] == pending[0][0])
