"""Linux counters and coherent capture metadata, with explicit missing values."""
import os
import pathlib
import struct
import time

HEADER_SIZE = 48
TIMING_OFFSET = 128 * 1024 * 1024 - 64
MAX_JPEG = 8 * 1024 * 1024


def text(path):
    try:
        return pathlib.Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return None


def number(path, divisor=1):
    try:
        return float(text(path)) / divisor
    except (ValueError, TypeError):
        return None


class Capture:
    def __init__(self, filename):
        self.filename = filename

    def read(self, with_image=False, now_ms=None):
        now_ms = time.monotonic() * 1000 if now_ms is None else now_ms
        try:
            # Reopen by name so replaced shared-memory inodes are not pinned forever.
            with open(self.filename, "rb", buffering=0) as source:
                header = source.read(HEADER_SIZE)
                if len(header) != HEADER_SIZE:
                    return {"quality": "UNAVAILABLE", "reason": "SHORT_HEADER"}, None
                frame, _, _, size, sequence, _, flags = struct.unpack_from("<7I", header)
                published = struct.unpack_from("<Q", header, 40)[0]
                if sequence & 1 or flags & 1 or not 4 <= size <= MAX_JPEG or published <= 0:
                    return {"quality": "UNAVAILABLE", "reason": "INCOMPLETE"}, None
                jpeg = source.read(size) if with_image else None
                timing = None
                if flags & 2:
                    source.seek(TIMING_OFFSET)
                    raw = source.read(64)
                    if len(raw) == 64:
                        magic, identity, driver_seq, driver_flags, driver, dequeued, precise = struct.unpack_from("<4I3Q", raw)
                        if magic == 0x314d4954 and identity == frame and precise // 1000 == published:
                            timing = {"driver_sequence": driver_seq, "driver_flags": driver_flags,
                                      "driver_us": driver, "dequeued_us": dequeued, "published_us": precise}
                source.seek(0)
                if source.read(HEADER_SIZE) != header:
                    return {"quality": "UNAVAILABLE", "reason": "CONCURRENT_WRITE"}, None
                if with_image and (len(jpeg) != size or jpeg[:2] != b"\xff\xd8"):
                    return {"quality": "UNAVAILABLE", "reason": "INCOMPLETE_JPEG"}, None
                age = now_ms - published
                if age < 0:
                    return {"quality": "INVALID", "reason": "FUTURE_TIMESTAMP"}, None
                return {"quality": "GOOD" if age <= 500 else "STALE", "frame_id": frame,
                        "published_ms": published, "age_ms": age, "bytes": size, "timing": timing}, jpeg
        except OSError as error:
            return {"quality": "UNAVAILABLE", "reason": type(error).__name__}, None


class LinuxSensors:
    def __init__(self, root="/", interface="wlan0"):
        self.root, self.interface = pathlib.Path(root), interface
        self.previous_cpu = self.previous_net = self.previous_time = None
        self.boot_id = text(self.root / "proc/sys/kernel/random/boot_id")

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        cpu = None
        stat = text(self.root / "proc/stat") or ""
        line = next((line for line in stat.splitlines() if line.startswith("cpu ")), "")
        try:
            parts = list(map(int, line.split()[1:9]))
            total, idle = sum(parts), parts[3] + parts[4]
            if self.previous_cpu:
                dt, di = total - self.previous_cpu[0], idle - self.previous_cpu[1]
                if dt > 0 and 0 <= di <= dt:
                    cpu = 100 * (dt - di) / dt
            self.previous_cpu = (total, idle)
        except (ValueError, IndexError):
            pass
        mem = {}
        for line in (text(self.root / "proc/meminfo") or "").splitlines():
            key, _, rest = line.partition(":")
            try:
                mem[key] = int(rest.split()[0]) * 1024
            except (ValueError, IndexError):
                continue
        used = 100 * (1 - mem["MemAvailable"] / mem["MemTotal"]) if mem.get("MemTotal") and "MemAvailable" in mem else None
        net = None
        for line in (text(self.root / "proc/net/dev") or "").splitlines():
            name, _, values = line.partition(":")
            if name.strip() == self.interface:
                try:
                    values = list(map(int, values.split()))
                    net = (values[0], values[8])
                except (ValueError, IndexError):
                    pass
        rx = tx = None
        if net and self.previous_net and self.previous_time is not None and now > self.previous_time:
            delta = [a - b for a, b in zip(net, self.previous_net)]
            if min(delta) >= 0:
                rx, tx = [value * 8 / (now - self.previous_time) / 1e6 for value in delta]
        self.previous_net, self.previous_time = net, now
        signal = None
        for line in (text(self.root / "proc/net/wireless") or "").splitlines():
            if line.split(":", 1)[0].strip() == self.interface:
                try:
                    signal = float(line.split(":", 1)[1].split()[2])
                except (ValueError, IndexError):
                    pass
        return {"source": "LINUX_MEASURED", "boot_id": self.boot_id, "cpu_percent": cpu,
                "memory_percent": used, "memory_available_bytes": mem.get("MemAvailable"),
                "temperature_c": number(self.root / "sys/class/thermal/thermal_zone0/temp", 1000),
                "frequency_mhz": number(self.root / "sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq", 1000),
                "wifi_signal_dbm": signal, "wifi_rx_mbps": rx, "wifi_tx_mbps": tx,
                "memory_cgroup": "memory" in (text(self.root / "sys/fs/cgroup/cgroup.controllers") or "").split()}
