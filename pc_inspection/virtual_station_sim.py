"""Scan-level reference for the DB-only virtual conveyor/glue pilot."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StationInputs:
    simulated_source: bool = False
    vision_qualified: bool = False
    motion_observed: bool = False
    start_button: bool = False
    stop_button: bool = False
    reset_button: bool = False
    guard_closed: bool = False
    motor_ready: bool = False
    glue_ready: bool = False
    at_glue_station: bool = False
    max_move_ms: int = 1500
    glue_dwell_ms: int = 400


@dataclass(frozen=True)
class StationOutputs:
    sim_state: int
    sim_conveyor_cmd: bool
    sim_glue_cmd: bool
    sim_fault_code: int


class _OnDelay:
    def __init__(self):
        self.started_ms = None
        self.preset_ms = None

    def scan(self, enabled: bool, preset_ms: int, now_ms: int) -> bool:
        if not enabled:
            self.started_ms = None
            self.preset_ms = None
            return False
        if self.started_ms is None:
            self.started_ms = now_ms
            self.preset_ms = preset_ms
        return now_ms - self.started_ms >= self.preset_ms


class VirtualStationSim:
    IDLE = 0
    MOVING = 10
    GLUING = 20
    COMPLETE = 30
    FAULT = 90

    def __init__(self):
        self.state = self.IDLE
        self.fault = 0
        self.last_start = False
        self.last_reset = False
        self.move_timer = _OnDelay()
        self.glue_timer = _OnDelay()

    def scan(self, inputs: StationInputs, now_ms: int) -> StationOutputs:
        start_edge = inputs.start_button and not self.last_start
        reset_edge = inputs.reset_button and not self.last_reset
        self.last_start = inputs.start_button
        self.last_reset = inputs.reset_button

        move_expired = self.move_timer.scan(
            self.state == self.MOVING, inputs.max_move_ms, now_ms)
        glue_elapsed = self.glue_timer.scan(
            self.state == self.GLUING, inputs.glue_dwell_ms, now_ms)

        if not inputs.simulated_source:
            self.state, self.fault = self.FAULT, 90
        elif inputs.stop_button:
            self.state, self.fault = self.FAULT, 10
        elif not inputs.guard_closed:
            self.state, self.fault = self.FAULT, 20
        elif inputs.max_move_ms <= 0 or inputs.glue_dwell_ms <= 0:
            self.state, self.fault = self.FAULT, 70
        elif self.state in (self.MOVING, self.GLUING) and not inputs.vision_qualified:
            self.state, self.fault = self.FAULT, 30
        elif self.state == self.MOVING and not inputs.motor_ready:
            self.state, self.fault = self.FAULT, 40
        elif self.state in (self.MOVING, self.GLUING) and not inputs.glue_ready:
            self.state, self.fault = self.FAULT, 50
        elif self.state == self.IDLE:
            if (start_edge and inputs.vision_qualified and inputs.motion_observed
                    and inputs.motor_ready and inputs.glue_ready
                    and not inputs.at_glue_station):
                self.state = self.MOVING
        elif self.state == self.MOVING:
            if move_expired:
                self.state, self.fault = self.FAULT, 60
            elif inputs.at_glue_station:
                self.state = self.GLUING
        elif self.state == self.GLUING:
            if glue_elapsed:
                self.state = self.COMPLETE
        elif self.state == self.COMPLETE:
            if reset_edge:
                self.state = self.IDLE
        elif self.state == self.FAULT:
            if (reset_edge and inputs.vision_qualified and inputs.motor_ready
                    and inputs.glue_ready):
                self.state, self.fault = self.IDLE, 0
        else:
            self.state, self.fault = self.FAULT, 80

        common = (inputs.simulated_source and inputs.vision_qualified
                  and inputs.guard_closed and inputs.glue_ready
                  and not inputs.stop_button)
        return StationOutputs(
            sim_state=self.state,
            sim_conveyor_cmd=(self.state == self.MOVING and common
                              and inputs.motor_ready),
            sim_glue_cmd=self.state == self.GLUING and common,
            sim_fault_code=self.fault,
        )
