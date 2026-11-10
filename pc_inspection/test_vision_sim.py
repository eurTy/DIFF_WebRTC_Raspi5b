"""Fault-injection cases for the isolated, diagnostic-only vision contract."""

from dataclasses import replace
import unittest

from pc_inspection.vision_sim import VisionSimInputs, evaluate


class VisionSimTests(unittest.TestCase):
    def setUp(self):
        self.good = VisionSimInputs(
            simulated_source=True,
            link_ok=True,
            quality_code=1,
            camera_ready=True,
            roi_verified=True,
            frame_age_ms=100,
            sequence_advanced_within_window=True,
            boot_tag_stable=True,
            motion_candidate=True,
        )

    def test_defaults_fail_closed(self):
        result = evaluate(VisionSimInputs())
        self.assertEqual(result.sim_fault_code, 90)
        self.assertFalse(result.sim_sample_qualified)
        self.assertFalse(result.sim_motion_observed)

    def test_only_synthetic_healthy_sample_can_report_motion(self):
        result = evaluate(self.good)
        self.assertEqual(result.sim_fault_code, 0)
        self.assertTrue(result.sim_sample_qualified)
        self.assertTrue(result.sim_motion_observed)
        self.assertFalse(evaluate(replace(self.good, motion_candidate=False)).sim_motion_observed)

    def test_each_fault_masks_motion(self):
        cases = (
            ({"simulated_source": False}, 90),
            ({"link_ok": False}, 10),
            ({"quality_code": 2}, 20),
            ({"camera_ready": False}, 30),
            ({"roi_verified": False}, 40),
            ({"frame_age_ms": -1}, 50),
            ({"frame_age_ms": 251}, 50),
            ({"max_frame_age_ms": 0}, 50),
            ({"sequence_advanced_within_window": False}, 60),
            ({"boot_tag_stable": False}, 70),
        )
        for change, expected_code in cases:
            with self.subTest(change=change):
                result = evaluate(replace(self.good, **change))
                self.assertEqual(result.sim_fault_code, expected_code)
                self.assertFalse(result.sim_sample_qualified)
                self.assertFalse(result.sim_motion_observed)

    def test_age_limit_is_inclusive(self):
        self.assertTrue(evaluate(replace(self.good, frame_age_ms=250)).sim_sample_qualified)

    def test_first_fault_has_priority(self):
        result = evaluate(replace(self.good, link_ok=False, roi_verified=False))
        self.assertEqual(result.sim_fault_code, 10)
        result = evaluate(replace(self.good, boot_tag_stable=False,
                                  sequence_advanced_within_window=False))
        self.assertEqual(result.sim_fault_code, 70)


if __name__ == "__main__":
    unittest.main()
