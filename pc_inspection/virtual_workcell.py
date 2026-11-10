"""Closed-loop synthetic camera, controller reference, and virtual plant.

This module has no PLC, camera, network, GPIO, or physical I/O adapter.
"""

from dataclasses import dataclass

from .vision_sequence_sim import VisionSequenceSim
from .vision_sim import VisionSimDiagnostics, VisionSimInputs, evaluate
from .virtual_station_sim import StationInputs, StationOutputs, VirtualStationSim


@dataclass(frozen=True)
class SimulationControls:
    camera_link: bool = True
    freeze_frames: bool = False
    guard_closed: bool = True
    motor_ready: bool = True
    glue_ready: bool = True
    arrival_sensor: bool = True


@dataclass(frozen=True)
class WorkcellSnapshot:
    now_ms: int
    sample_seq: int
    frame_age_ms: int
    vision: VisionSimDiagnostics
    station_inputs: StationInputs
    station: StationOutputs
    part_position: float | None
    glue_applied: bool
    at_glue_station: bool
    belt_phase: float


class VirtualPlant:
    ENTRY = 0.11
    GLUE_STATION = 0.69
    SPEED_PER_MS = 0.00050

    def __init__(self):
        self.part_position = None
        self.glue_applied = False
        self.glue_exposure_ms = 0
        self.belt_phase = 0.0

    @property
    def at_glue_station(self):
        return (self.part_position is not None
                and self.part_position >= self.GLUE_STATION)

    def place_part(self):
        if self.part_position is not None:
            return False
        self.part_position = self.ENTRY
        self.glue_applied = False
        self.glue_exposure_ms = 0
        return True

    def clear_part(self):
        self.part_position = None
        self.glue_applied = False
        self.glue_exposure_ms = 0

    def advance(self, output: StationOutputs, delta_ms: int):
        if delta_ms < 0:
            raise ValueError("simulation time cannot go backwards")
        if output.sim_conveyor_cmd and output.sim_glue_cmd:
            raise ValueError("virtual conveyor and glue cannot run together")
        if output.sim_conveyor_cmd:
            self.belt_phase = (self.belt_phase + delta_ms * self.SPEED_PER_MS) % 1.0
            if self.part_position is not None:
                self.part_position = min(
                    self.GLUE_STATION,
                    self.part_position + delta_ms * self.SPEED_PER_MS,
                )
        if output.sim_glue_cmd and self.at_glue_station:
            self.glue_exposure_ms += delta_ms
            self.glue_applied = self.glue_exposure_ms >= 80


class VirtualWorkcell:
    FRAME_INTERVAL_MS = 80
    MAX_SCAN_GAP_MS = 250
    BOOT_TAG = 1

    def __init__(self):
        self.plant = VirtualPlant()
        self.sequence = VisionSequenceSim()
        self.station = VirtualStationSim()
        self.sample_seq = 0
        self.last_frame_ms = None
        self.last_tick_ms = None
        self.last_output = StationOutputs(0, False, False, 0)

    def place_part(self):
        if self.station.state != VirtualStationSim.IDLE:
            return False
        return self.plant.place_part()

    def tick(self, now_ms: int, *, controls: SimulationControls = SimulationControls(),
             start: bool = False, stop: bool = False,
             reset: bool = False) -> WorkcellSnapshot:
        if self.last_tick_ms is not None and now_ms < self.last_tick_ms:
            raise ValueError("simulation time cannot go backwards")
        delta_ms = 0 if self.last_tick_ms is None else now_ms - self.last_tick_ms
        self.last_tick_ms = now_ms
        scan_gap = delta_ms > self.MAX_SCAN_GAP_MS
        if not scan_gap:
            self.plant.advance(self.last_output, delta_ms)
        link_ok = controls.camera_link and not scan_gap

        if (link_ok and not controls.freeze_frames
                and (self.last_frame_ms is None
                     or now_ms - self.last_frame_ms >= self.FRAME_INTERVAL_MS)):
            self.sample_seq += 1
            self.last_frame_ms = now_ms
        frame_age_ms = (65535 if self.last_frame_ms is None
                        else now_ms - self.last_frame_ms)

        freshness = self.sequence.scan(
            simulated_source=True,
            link_ok=link_ok,
            sample_seq=self.sample_seq,
            boot_tag=self.BOOT_TAG,
            max_stall_ms=500,
            now_ms=now_ms,
        )
        vision = evaluate(VisionSimInputs(
            simulated_source=True,
            link_ok=link_ok,
            quality_code=1,
            camera_ready=link_ok,
            roi_verified=True,
            frame_age_ms=frame_age_ms,
            sequence_advanced_within_window=(
                freshness.sequence_advanced_within_window),
            boot_tag_stable=freshness.boot_tag_stable,
            motion_candidate=(self.plant.part_position is not None
                              and not self.plant.at_glue_station),
        ))
        station_inputs = StationInputs(
            simulated_source=True,
            vision_qualified=vision.sim_sample_qualified,
            motion_observed=vision.sim_motion_observed,
            start_button=start,
            stop_button=stop,
            reset_button=reset,
            guard_closed=controls.guard_closed,
            motor_ready=controls.motor_ready,
            glue_ready=controls.glue_ready,
            at_glue_station=(self.plant.at_glue_station
                             and controls.arrival_sensor),
        )
        previous_state = self.station.state
        output = self.station.scan(station_inputs, now_ms)
        if (previous_state in (VirtualStationSim.COMPLETE, VirtualStationSim.FAULT)
                and output.sim_state == VirtualStationSim.IDLE):
            self.plant.clear_part()
        self.last_output = output
        return WorkcellSnapshot(
            now_ms=now_ms,
            sample_seq=self.sample_seq,
            frame_age_ms=frame_age_ms,
            vision=vision,
            station_inputs=station_inputs,
            station=output,
            part_position=self.plant.part_position,
            glue_applied=self.plant.glue_applied,
            at_glue_station=self.plant.at_glue_station,
            belt_phase=self.plant.belt_phase,
        )
