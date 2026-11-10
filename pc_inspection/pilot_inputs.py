"""Reference logic for the first 16 simulated PLC inputs, not machine control."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PilotInputs:
    infeed_photoeye: bool = False
    part_present: bool = False
    magazine_low: bool = False
    clamp_extended: bool = False
    clamp_retracted: bool = False
    pusher_extended: bool = False
    pusher_retracted: bool = False
    lift_up: bool = False
    lift_down: bool = False
    vacuum_ok: bool = False
    motor_ready: bool = False
    jam_detected: bool = False
    start_button: bool = False
    stop_button: bool = False
    reset_button: bool = False
    guard_door_closed: bool = False


@dataclass(frozen=True)
class PilotDiagnostics:
    sensor_conflict: bool
    home_position_ok: bool
    feed_ready: bool
    pick_ready: bool
    start_request_accepted: bool
    reset_request_observed: bool
    part_at_infeed: bool
    alarm_code: int


def evaluate(inputs: PilotInputs) -> PilotDiagnostics:
    """Evaluate advisory status only; no output is an actuator or safety command."""
    conflict = ((inputs.clamp_extended and inputs.clamp_retracted)
                or (inputs.pusher_extended and inputs.pusher_retracted)
                or (inputs.lift_up and inputs.lift_down))
    home = (inputs.clamp_retracted and inputs.pusher_retracted
            and inputs.lift_down and not inputs.clamp_extended
            and not inputs.pusher_extended and not inputs.lift_up)
    feed_ready = (not conflict and home and inputs.guard_door_closed
                  and not inputs.stop_button and not inputs.jam_detected
                  and inputs.motor_ready and not inputs.magazine_low)

    if conflict:
        alarm = 10
    elif not inputs.guard_door_closed:
        alarm = 20
    elif inputs.stop_button:
        alarm = 30
    elif inputs.jam_detected:
        alarm = 40
    elif not inputs.motor_ready:
        alarm = 50
    elif inputs.magazine_low:
        alarm = 60
    elif not home:
        alarm = 70
    else:
        alarm = 0

    return PilotDiagnostics(
        sensor_conflict=conflict,
        home_position_ok=home,
        feed_ready=feed_ready,
        pick_ready=feed_ready and inputs.part_present and inputs.vacuum_ok,
        start_request_accepted=inputs.start_button and feed_ready,
        reset_request_observed=inputs.reset_button,
        part_at_infeed=inputs.infeed_photoeye,
        alarm_code=alarm,
    )
