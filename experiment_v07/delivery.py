"""Validated media envelopes and a byte/time bounded feedback window."""
from collections import deque
import zlib

from experiment_v05.codec import envelope


def checked_envelope(meta, payload):
    if not isinstance(payload, bytes):
        raise ValueError("Immutable payload required")
    if "parts" in meta:
        parts, lengths = meta["parts"], meta.get("lengths")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 5 or not isinstance(lengths, list) or len(parts) != len(lengths):
            raise ValueError("Invalid JPEG parts")
        if any(type(n) is not int or n < 4 for n in lengths) or sum(lengths) != len(payload):
            raise ValueError("JPEG payload length mismatch")
        offset = 0
        for part, size in zip(parts, lengths):
            rect = part.get("rect")
            if (not isinstance(rect, list) or len(rect) != 4 or any(type(n) is not int for n in rect)
                    or min(rect[:2]) < 0 or min(rect[2:]) <= 0
                    or rect[0]+rect[2] > 1280 or rect[1]+rect[3] > 720):
                raise ValueError("Invalid JPEG rectangle")
            if payload[offset:offset+2] != b"\xff\xd8" or payload[offset+size-2:offset+size] != b"\xff\xd9":
                raise ValueError("Incomplete JPEG part")
            offset += size
    verified = dict(meta, payload_bytes=len(payload), payload_crc32=zlib.crc32(payload), integrity="crc32-v1")
    return envelope(verified, payload)


class SendWindow:
    """No codec changes: feedback limits admission and pacing, not image quality."""
    max_frames = 4

    def __init__(self, kbps, fps):
        self.ceiling = self.rate_kbps = kbps
        self.fps = fps
        self.pending = deque()
        self.in_flight_bytes = 0
        self.byte_limit = max(16384, min(135000, int(kbps*125*.18)))
        self.ack_samples = deque(maxlen=120)
        self.next_due = 0
        self.byte_deadline = 0
        self.last_ack_ms = None
        self.good = 0
        self.last_decrease_ms = float('-inf')

    @property
    def baseline_ms(self):
        return min((value for _, value in self.ack_samples), default=50)

    @property
    def soft_age_ms(self):
        return min(500, max(180, self.baseline_ms*1.5+60))

    @property
    def hard_age_ms(self):
        return min(8000, max(4000, self.baseline_ms*8))

    def oldest_age(self, now):
        return max(0, now-self.pending[0][1]) if self.pending else 0

    def ready(self, now, packet_bytes=0):
        if now < self.next_due or len(self.pending) >= self.max_frames:
            return False
        if self.pending and (self.oldest_age(now) >= self.soft_age_ms or
                             self.in_flight_bytes+packet_bytes > self.byte_limit or
                             self.in_flight_bytes >= self.byte_limit):
            return False
        # An oversized keyframe may travel alone, never with another pending frame.
        return True

    def sent(self, seq, size, now, cycle):
        self.pending.append((seq, now, size))
        self.in_flight_bytes += size
        self.byte_deadline = max(self.byte_deadline, now-50)+size*8/self.rate_kbps
        self.next_due = max(cycle+1000/self.fps, self.byte_deadline)

    def acknowledge(self, seq, now):
        if type(seq) is not int or not self.pending or self.pending[0][0] != seq:
            raise ValueError("Ordered acknowledgement required")
        _, sent, size = self.pending.popleft()
        self.in_flight_bytes -= size
        elapsed = max(.001, now-sent)
        self.last_ack_ms = elapsed
        self.ack_samples.append((now, elapsed))
        while self.ack_samples and now-self.ack_samples[0][0] > 10000:
            self.ack_samples.popleft()
        if elapsed-self.baseline_ms > 80:
            if now-self.last_decrease_ms >= max(200, self.baseline_ms*2):
                self.rate_kbps = max(self.ceiling*.35, self.rate_kbps*.85)
                self.last_decrease_ms = now
            self.good = 0
        else:
            self.good += 1
            if self.good >= 12:
                self.rate_kbps = min(self.ceiling, self.rate_kbps*1.08+24)
                self.good = 0

    def snapshot(self, now):
        return {"policy": "byte-age-v2", "max_frames": self.max_frames,
                "pending_frames": len(self.pending), "pending_bytes": self.in_flight_bytes,
                "byte_limit": self.byte_limit, "oldest_ack_ms": round(self.oldest_age(now), 2),
                "soft_age_ms": round(self.soft_age_ms, 2), "hard_age_ms": round(self.hard_age_ms, 2),
                "pacing_kbps": round(self.rate_kbps, 2)}
