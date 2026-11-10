"""Reference for simulation-only vision diagnostics; never drives PLC outputs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VisionSimInputs:
    simulated_source: bool = False
    link_ok: bool = False
    quality_code: int = 0
    camera_ready: bool = False
    roi_verified: bool = False
    frame_age_ms: int = 65535
    max_frame_age_ms: int = 250
    sequence_advanced_within_window: bool = False
    boot_tag_stable: bool = False
    motion_candidate: bool = False


@dataclass(frozen=True)
class VisionSimDiagnostics:
    sim_sample_qualified: bool
    sim_motion_observed: bool
    sim_fault_code: int


def evaluate(inputs: VisionSimInputs) -> VisionSimDiagnostics:
    if not inputs.simulated_source:
        fault = 90
    elif not inputs.link_ok:
        fault = 10
    elif inputs.quality_code != 1:
        fault = 20
    elif not inputs.camera_ready:
        fault = 30
    elif not inputs.roi_verified:
        fault = 40
    elif (inputs.max_frame_age_ms <= 0 or inputs.frame_age_ms < 0
          or inputs.frame_age_ms > inputs.max_frame_age_ms):
        fault = 50
    elif not inputs.boot_tag_stable:
        fault = 70
    elif not inputs.sequence_advanced_within_window:
        fault = 60
    else:
        fault = 0

    qualified = fault == 0
    return VisionSimDiagnostics(
        sim_sample_qualified=qualified,
        sim_motion_observed=qualified and inputs.motion_candidate,
        sim_fault_code=fault,
    )
