"""Scan-by-scan reference for the synthetic PLC sequence diagnostic."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SequenceResult:
    sequence_advanced_within_window: bool
    boot_tag_stable: bool


class VisionSequenceSim:
    def __init__(self):
        self._baseline = False
        self._last_seq = 0
        self._last_boot_tag = 0
        self._armed = False
        self._boot_confirmed = False
        self._timer_started_ms = None
        self._last_now_ms = None

    def scan(self, *, simulated_source: bool, link_ok: bool, sample_seq: int,
             boot_tag: int, max_stall_ms: int, now_ms: int) -> SequenceResult:
        if self._last_now_ms is not None and now_ms < self._last_now_ms:
            self._baseline = False
            self._armed = False
            self._boot_confirmed = False
            self._timer_started_ms = None
        self._last_now_ms = now_ms

        advanced = False
        if (not simulated_source or not link_ok or sample_seq <= 0
                or boot_tag <= 0 or max_stall_ms <= 0):
            self._baseline = False
            self._armed = False
            self._boot_confirmed = False
        elif not self._baseline or boot_tag != self._last_boot_tag:
            self._last_seq = sample_seq
            self._last_boot_tag = boot_tag
            self._baseline = True
            self._armed = False
            self._boot_confirmed = False
        elif sample_seq > self._last_seq:
            self._last_seq = sample_seq
            self._armed = True
            self._boot_confirmed = True
            advanced = True
        elif sample_seq < self._last_seq:
            self._armed = False

        if not self._armed or advanced:
            self._timer_started_ms = None
        elif self._timer_started_ms is None:
            self._timer_started_ms = now_ms
        elif now_ms - self._timer_started_ms >= max_stall_ms:
            self._armed = False
            self._timer_started_ms = None

        return SequenceResult(self._armed, self._boot_confirmed)
