"""Debounced diagnostic rules, not safety interlocks or machine-fault classifiers."""


class AlarmEngine:
    def __init__(self):
        self.active, self.counts = {}, {}

    def update(self, sample):
        sensors, capture = sample["sensors"], sample["capture"]
        rules = {
            "CAPTURE_STALE": (capture["quality"] != "GOOD", 2, "采集数据过期", "warning"),
            "HOST_HOT": (self.high("HOST_HOT", sensors.get("temperature_c"), 75, 70), 3, "主机温度偏高", "warning"),
            "HOST_BUSY": (self.high("HOST_BUSY", sensors.get("cpu_percent"), 90, 75), 5, "主机负载偏高", "warning"),
            "WIFI_WEAK": (self.low("WIFI_WEAK", sensors.get("wifi_signal_dbm"), -70, -65), 5, "无线信号偏弱", "warning"),
        }
        transitions = []
        for code, (condition, required, title, level) in rules.items():
            if condition is None:
                self.counts[code] = 0
                continue
            old = self.active.get(code, False)
            self.counts[code] = self.counts.get(code, 0) + 1 if condition != old else 0
            threshold = required if condition else 2
            if self.counts[code] >= threshold:
                self.active[code], self.counts[code] = condition, 0
                transitions.append({"code": code, "title": title, "phase": "ACTIVE" if condition else "RECOVERED",
                                    "level": level if condition else "info", "source": "MEASURED_RULE"})
        return transitions

    def high(self, code, value, trip, reset):
        return None if value is None else value >= (reset if self.active.get(code) else trip)

    def low(self, code, value, trip, reset):
        return None if value is None else value <= (reset if self.active.get(code) else trip)
