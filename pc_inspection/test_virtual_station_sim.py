"""Synthetic virtual-station tests; no PLC, camera or output connection."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest

from pc_inspection.vision_sequence_sim import VisionSequenceSim
from pc_inspection.vision_sim import VisionSimInputs, evaluate
from pc_inspection.virtual_station_sim import StationInputs, VirtualStationSim


READY = StationInputs(
    simulated_source=True,
    vision_qualified=True,
    motion_observed=True,
    guard_closed=True,
    motor_ready=True,
    glue_ready=True,
)


class VirtualStationSimTests(unittest.TestCase):
    def setUp(self):
        self.station = VirtualStationSim()

    def reset_to_idle(self):
        self.assertEqual(self.station.scan(StationInputs(), 0).sim_fault_code, 90)
        output = self.station.scan(replace(READY, reset_button=True), 10)
        self.assertEqual((output.sim_state, output.sim_fault_code), (0, 0))
        self.station.scan(READY, 20)

    def start_moving(self):
        self.reset_to_idle()
        output = self.station.scan(replace(READY, start_button=True), 30)
        self.assertEqual(output.sim_state, 10)
        self.assertTrue(output.sim_conveyor_cmd)
        self.assertFalse(output.sim_glue_cmd)

    def test_full_cycle_requires_new_start_edge_and_motion(self):
        self.reset_to_idle()
        blocked = self.station.scan(replace(READY, start_button=True,
                                            motion_observed=False), 30)
        self.assertEqual(blocked.sim_state, 0)
        self.assertEqual(self.station.scan(replace(READY, start_button=True),
                                           40).sim_state, 0)
        self.station.scan(READY, 50)
        moving = self.station.scan(replace(READY, start_button=True), 60)
        self.assertTrue(moving.sim_conveyor_cmd)
        self.assertFalse(moving.sim_glue_cmd)

        gluing = self.station.scan(replace(READY, at_glue_station=True), 100)
        self.assertEqual(gluing.sim_state, 20)
        self.assertFalse(gluing.sim_conveyor_cmd)
        self.assertTrue(gluing.sim_glue_cmd)
        self.assertTrue(self.station.scan(READY, 110).sim_glue_cmd)
        self.assertTrue(self.station.scan(READY, 509).sim_glue_cmd)
        complete = self.station.scan(READY, 510)
        self.assertEqual(complete.sim_state, 30)
        self.assertFalse(complete.sim_conveyor_cmd or complete.sim_glue_cmd)
        self.assertEqual(self.station.scan(replace(READY, reset_button=True),
                                           520).sim_state, 0)
        self.assertEqual(self.station.scan(replace(READY, start_button=True,
                                                    at_glue_station=True),
                                           530).sim_state, 0)

    def test_move_timeout_latches_until_fresh_reset(self):
        self.start_moving()
        self.station.scan(replace(READY, start_button=True), 40)
        self.assertTrue(self.station.scan(READY, 1539).sim_conveyor_cmd)
        timed_out = self.station.scan(READY, 1540)
        self.assertEqual((timed_out.sim_state, timed_out.sim_fault_code), (90, 60))
        self.assertFalse(timed_out.sim_conveyor_cmd or timed_out.sim_glue_cmd)
        self.assertEqual(self.station.scan(replace(READY, at_glue_station=True),
                                           1550).sim_state, 90)
        self.assertEqual(self.station.scan(replace(READY, reset_button=True),
                                           1560).sim_state, 0)

    def test_timer_preset_is_sampled_on_entry(self):
        self.start_moving()
        self.station.scan(READY, 40)
        self.assertTrue(self.station.scan(replace(READY, max_move_ms=2000),
                                          1539).sim_conveyor_cmd)
        self.assertEqual(self.station.scan(replace(READY, max_move_ms=2000),
                                           1540).sim_fault_code, 60)

    def test_arrival_at_timeout_does_not_start_glue(self):
        self.start_moving()
        self.station.scan(READY, 40)
        output = self.station.scan(replace(READY, at_glue_station=True), 1540)
        self.assertEqual((output.sim_state, output.sim_fault_code), (90, 60))
        self.assertFalse(output.sim_conveyor_cmd or output.sim_glue_cmd)

    def test_active_faults_disable_both_commands(self):
        cases = (
            (dict(stop_button=True), 10),
            (dict(guard_closed=False), 20),
            (dict(vision_qualified=False), 30),
            (dict(motor_ready=False), 40),
            (dict(glue_ready=False), 50),
            (dict(max_move_ms=0), 70),
            (dict(simulated_source=False), 90),
        )
        for changes, fault in cases:
            with self.subTest(changes=changes):
                station = VirtualStationSim()
                station.scan(StationInputs(), 0)
                station.scan(replace(READY, reset_button=True), 10)
                station.scan(READY, 20)
                station.scan(replace(READY, start_button=True), 30)
                output = station.scan(replace(READY, **changes), 40)
                self.assertEqual((output.sim_state, output.sim_fault_code),
                                 (90, fault))
                self.assertFalse(output.sim_conveyor_cmd or output.sim_glue_cmd)

    def test_reset_requires_released_button_and_ready_inputs(self):
        self.start_moving()
        self.assertEqual(self.station.scan(replace(READY, stop_button=True,
                                                    reset_button=True),
                                           40).sim_fault_code, 10)
        self.assertEqual(self.station.scan(replace(READY, reset_button=True),
                                           50).sim_state, 90)
        self.station.scan(replace(READY, vision_qualified=False), 60)
        self.assertEqual(self.station.scan(replace(READY, reset_button=True,
                                                    vision_qualified=False),
                                           70).sim_state, 90)
        self.station.scan(READY, 80)
        self.assertEqual(self.station.scan(replace(READY, reset_button=True),
                                           90).sim_state, 0)

    def test_sequence_stall_faults_active_station(self):
        sequence = VisionSequenceSim()
        station = VirtualStationSim()
        inputs = VisionSimInputs(simulated_source=True, link_ok=True,
                                 quality_code=1, camera_ready=True,
                                 roi_verified=True, frame_age_ms=100,
                                 motion_candidate=True)

        def step(now_ms, sample_seq, *, start=False, reset=False):
            freshness = sequence.scan(simulated_source=True, link_ok=True,
                                      sample_seq=sample_seq, boot_tag=7,
                                      max_stall_ms=500, now_ms=now_ms)
            vision = evaluate(replace(
                inputs,
                sequence_advanced_within_window=(
                    freshness.sequence_advanced_within_window),
                boot_tag_stable=freshness.boot_tag_stable,
            ))
            output = station.scan(replace(
                READY, vision_qualified=vision.sim_sample_qualified,
                motion_observed=vision.sim_motion_observed,
                start_button=start, reset_button=reset), now_ms)
            return vision, output

        initial_vision, initial_station = step(0, 1)
        self.assertEqual(initial_vision.sim_fault_code, 70)
        self.assertEqual(initial_station.sim_state, 0)
        self.assertFalse(initial_station.sim_conveyor_cmd or initial_station.sim_glue_cmd)
        self.assertEqual(step(10, 2, reset=True)[1].sim_state, 0)
        self.assertTrue(step(20, 3, start=True)[1].sim_conveyor_cmd)
        self.assertTrue(step(30, 3)[1].sim_conveyor_cmd)
        vision, output = step(530, 3)
        self.assertEqual(vision.sim_fault_code, 60)
        self.assertEqual((output.sim_state, output.sim_fault_code), (90, 30))
        self.assertFalse(output.sim_conveyor_cmd or output.sim_glue_cmd)

    def test_station_acceptance_trace_against_reference(self):
        trace_path = Path(__file__).with_name("plcsim_station_acceptance_v1.json")
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        self.assertEqual(trace["schema_version"], 1)
        self.assertEqual(trace["source"], "SYNTHETIC_ONLY")
        self.assertEqual(trace["plcsim_status"], "NOT_RUN_IN_PLCSIM")
        fields = set(StationInputs.__dataclass_fields__)
        self.assertEqual(set(trace["initial"]), fields)
        state = trace["initial"].copy()
        station = VirtualStationSim()
        last_time = -1
        seen_states = set()
        seen_faults = set()

        for step in trace["steps"]:
            with self.subTest(step=step["label"]):
                self.assertGreater(step["at_ms"], last_time)
                last_time = step["at_ms"]
                self.assertLessEqual(set(step["set"]), fields)
                state.update(step["set"])
                result = station.scan(StationInputs(**state), step["at_ms"])
                self.assertEqual(asdict(result), step["expect"])
                self.assertFalse(result.sim_conveyor_cmd and result.sim_glue_cmd)
                seen_states.add(result.sim_state)
                seen_faults.add(result.sim_fault_code)

        self.assertEqual(seen_states, {0, 10, 20, 30, 90})
        self.assertEqual(seen_faults, {0, 20, 30, 60, 90})


if __name__ == "__main__":
    unittest.main()
