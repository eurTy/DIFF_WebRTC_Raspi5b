"""Planning-only 100-point I/O schedule for a simulated three-station cell."""

import csv
from dataclasses import asdict, dataclass
from pathlib import Path


STATION_SIGNALS = {
    "A": {
        "DI": (
            "infeed_photoeye", "part_present", "magazine_low", "clamp_extended",
            "clamp_retracted", "pusher_extended", "pusher_retracted", "lift_up",
            "lift_down", "vacuum_ok", "motor_ready", "jam_detected",
        ),
        "DO": (
            "infeed_motor", "clamp_extend", "clamp_retract", "pusher_extend",
            "pusher_retract", "lift_raise", "lift_lower", "station_lamp",
        ),
        "AI": (
            ("motor_current", "A"), ("clamp_pressure", "bar"),
            ("vacuum_level", "kPa"), ("feed_speed", "m/min"),
        ),
    },
    "B": {
        "DI": (
            "transfer_photoeye", "part_present", "fixture_closed", "fixture_open",
            "nozzle_up", "nozzle_down", "valve_open_feedback", "glue_level_low",
            "pressure_ok", "exhaust_ok", "motor_ready", "jam_detected",
        ),
        "DO": (
            "transfer_motor", "fixture_close", "fixture_open", "nozzle_raise",
            "nozzle_lower", "glue_valve", "purge_valve", "station_lamp",
        ),
        "AI": (
            ("glue_pressure", "bar"), ("glue_temperature", "degC"),
            ("glue_flow", "mL/min"), ("motor_current", "A"),
        ),
    },
    "C": {
        "DI": (
            "transfer_photoeye", "part_present", "vision_trigger_ack", "camera_ready",
            "reject_bin_full", "accept_bin_full", "diverter_accept_feedback",
            "diverter_reject_feedback", "ejector_extended", "ejector_retracted",
            "motor_ready", "jam_detected",
        ),
        "DO": (
            "transfer_motor", "camera_trigger", "inspection_light",
            "diverter_accept", "diverter_reject", "ejector_extend",
            "ejector_retract", "station_lamp",
        ),
        "AI": (
            ("light_intensity", "lux"), ("motor_current", "A"),
            ("reject_bin_weight", "kg"), ("accept_bin_weight", "kg"),
        ),
    },
}

COMMON_SIGNALS = {
    "DI": (
        "start_button", "stop_button", "reset_button", "guard_door_closed",
        "safety_chain_feedback", "air_supply_ok", "network_link_ok",
        "controller_heartbeat", "upstream_ready", "downstream_ready",
        "glue_pump_ready", "emergency_stop_feedback",
    ),
    "DO": (
        "main_conveyor", "stacklight_red", "stacklight_yellow", "stacklight_green",
        "buzzer", "air_enable_valve", "upstream_request", "downstream_ready_signal",
    ),
    "AI": (
        ("main_air_pressure", "bar"), ("cabinet_temperature", "degC"),
        ("power_bus_voltage", "V"), ("main_motor_current", "A"),
    ),
    "AO": (
        ("conveyor_speed_reference", "m/min"),
        ("glue_pressure_reference", "bar"),
        ("inspection_light_reference", "pct"),
        ("exhaust_speed_reference", "pct"),
    ),
}


@dataclass(frozen=True)
class Point:
    number: int
    kind: str
    address: str
    module: str
    channel: int
    station: str
    name: str
    data_type: str
    unit: str
    implementation: str = "PLANNED_SIMULATION"


def build_points():
    points = []

    def add(kind, station, name, unit=""):
        index = sum(p.kind == kind for p in points)
        prefix = {"DI": "I", "DO": "Q", "AI": "IW", "AO": "QW"}[kind]
        address = (f"{prefix}{index // 8}.{index % 8}" if kind in ("DI", "DO")
                   else f"{prefix}{64 + 2 * index}")
        channels = 16 if kind in ("DI", "DO") else 8 if kind == "AI" else 4
        points.append(Point(len(points) + 1, kind, address,
                            f"{kind}-{index // channels + 1}", index % channels,
                            station, f"{station}_{kind}_{name}",
                            "Bool" if kind in ("DI", "DO") else "Int", unit))

    # Each 16-channel DI module holds one station's 12 inputs and four shared inputs.
    for station_index, station in enumerate("ABC"):
        for name in STATION_SIGNALS[station]["DI"]:
            add("DI", station, name)
        for name in COMMON_SIGNALS["DI"][station_index * 4:(station_index + 1) * 4]:
            add("DI", "CELL", name)
    for station in "ABC":
        for name in STATION_SIGNALS[station]["DO"]:
            add("DO", station, name)
    for name in COMMON_SIGNALS["DO"]:
        add("DO", "CELL", name)
    for station in "ABC":
        for name, unit in STATION_SIGNALS[station]["AI"]:
            add("AI", station, name, unit)
    for name, unit in COMMON_SIGNALS["AI"]:
        add("AI", "CELL", name, unit)
    for name, unit in COMMON_SIGNALS["AO"]:
        add("AO", "CELL", name, unit)
    validate_points(points)
    return tuple(points)


def validate_points(points):
    counts = {kind: sum(p.kind == kind for p in points) for kind in ("DI", "DO", "AI", "AO")}
    if counts != {"DI": 48, "DO": 32, "AI": 16, "AO": 4} or len(points) != 100:
        raise ValueError(f"Expected 100 points with 48/32/16/4 split, got {counts}")
    if len({p.name for p in points}) != 100:
        raise ValueError("Signal names must be unique")
    if len({(p.kind, p.address) for p in points}) != 100:
        raise ValueError("Signal addresses must be unique within each I/O class")
    for point in points:
        if point.kind in ("DI", "DO") and point.channel >= 16:
            raise ValueError(f"Digital channel out of range: {point}")
        if point.kind == "AI" and point.channel >= 8:
            raise ValueError(f"AI channel out of range: {point}")
        if point.kind == "AO" and point.channel >= 4:
            raise ValueError(f"AO channel out of range: {point}")


def export_csv(path):
    points = build_points()
    with Path(path).open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=Point.__dataclass_fields__)
        writer.writeheader()
        writer.writerows(asdict(point) for point in points)
    return len(points)


if __name__ == "__main__":
    target = Path(__file__).with_name("io_map_100.csv")
    print(f"Exported {export_csv(target)} planned points to {target}")
