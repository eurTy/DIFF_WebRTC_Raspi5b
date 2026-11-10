"""Integration of synthetic sequence monitoring and diagnostic decisions."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest

from pc_inspection.vision_sequence_sim import VisionSequenceSim
from pc_inspection.vision_sim import VisionSimInputs, evaluate


class VisionPipelineSimTests(unittest.TestCase):
    def setUp(self):
        self.sequence = VisionSequenceSim()
        self.inputs = VisionSimInputs(
            simulated_source=True,
            link_ok=True,
            quality_code=1,
            camera_ready=True,
            roi_verified=True,
            frame_age_ms=100,
            motion_candidate=True,
        )

    def scan(self, now_ms, sample_seq, boot_tag=7, link_ok=True):
        sequence = self.sequence.scan(
            simulated_source=True,
            link_ok=link_ok,
            sample_seq=sample_seq,
            boot_tag=boot_tag,
            max_stall_ms=500,
            now_ms=now_ms,
        )
        return evaluate(replace(
            self.inputs,
            link_ok=link_ok,
            sequence_advanced_within_window=sequence.sequence_advanced_within_window,
            boot_tag_stable=sequence.boot_tag_stable,
        ))

    def test_boot_and_stall_have_distinct_faults(self):
        self.assertEqual(self.scan(0, 1).sim_fault_code, 70)
        self.assertEqual(self.scan(10, 2).sim_fault_code, 0)
        self.assertEqual(self.scan(20, 2).sim_fault_code, 0)
        stalled = self.scan(520, 2)
        self.assertEqual(stalled.sim_fault_code, 60)
        self.assertFalse(stalled.sim_motion_observed)
        self.assertEqual(self.scan(530, 3).sim_fault_code, 0)
        self.assertEqual(self.scan(540, 1, boot_tag=8).sim_fault_code, 70)
        self.assertEqual(self.scan(550, 2, boot_tag=8).sim_fault_code, 0)

    def test_disconnect_requires_two_new_samples(self):
        self.scan(0, 1)
        self.scan(10, 2)
        self.assertEqual(self.scan(20, 2, link_ok=False).sim_fault_code, 10)
        self.assertEqual(self.scan(30, 3).sim_fault_code, 70)
        self.assertEqual(self.scan(40, 4).sim_fault_code, 0)

    def test_plcsim_acceptance_trace_against_reference(self):
        trace_path = Path(__file__).with_name("plcsim_acceptance_v1.json")
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        self.assertEqual(trace["schema_version"], 1)
        self.assertEqual(trace["source"], "SYNTHETIC_ONLY")

        diagnostic_fields = set(VisionSimInputs.__dataclass_fields__) - {
            "sequence_advanced_within_window", "boot_tag_stable"
        }
        allowed_fields = diagnostic_fields | {"sample_seq", "boot_tag", "max_stall_ms"}
        self.assertEqual(set(trace["initial"]), allowed_fields)
        state = trace["initial"].copy()
        sequence = VisionSequenceSim()
        seen_fault_codes = set()
        last_time = -1

        for step in trace["steps"]:
            with self.subTest(step=step["label"]):
                self.assertGreater(step["at_ms"], last_time)
                last_time = step["at_ms"]
                self.assertLessEqual(set(step["set"]), allowed_fields)
                state.update(step["set"])
                freshness = sequence.scan(
                    simulated_source=state["simulated_source"],
                    link_ok=state["link_ok"],
                    sample_seq=state["sample_seq"],
                    boot_tag=state["boot_tag"],
                    max_stall_ms=state["max_stall_ms"],
                    now_ms=step["at_ms"],
                )
                diagnostic_inputs = {
                    name: state[name] for name in diagnostic_fields
                }
                result = evaluate(replace(
                    VisionSimInputs(**diagnostic_inputs),
                    sequence_advanced_within_window=(
                        freshness.sequence_advanced_within_window
                    ),
                    boot_tag_stable=freshness.boot_tag_stable,
                ))
                self.assertEqual(asdict(result), step["expect"])
                seen_fault_codes.add(result.sim_fault_code)

        self.assertEqual(seen_fault_codes, {0, 10, 20, 30, 40, 50, 60, 70, 90})


if __name__ == "__main__":
    unittest.main()
