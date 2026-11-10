from dataclasses import fields, replace
import unittest

from pc_inspection.io_plan import build_points
from pc_inspection.pilot_inputs import PilotInputs, evaluate


READY = PilotInputs(clamp_retracted=True, pusher_retracted=True,
                    lift_down=True, motor_ready=True, guard_door_closed=True)


class PilotInputTests(unittest.TestCase):
    def test_input_contract_matches_first_16_planned_di_points(self):
        points = build_points()[:16]
        self.assertEqual([p.address for p in points],
                         [f"I{byte}.{bit}" for byte in range(2) for bit in range(8)])
        self.assertEqual([p.name.split("_DI_", 1)[1] for p in points],
                         [field.name for field in fields(PilotInputs)])

    def test_ready_and_start_are_advisory(self):
        actual = evaluate(replace(READY, start_button=True, part_present=True,
                                  vacuum_ok=True, infeed_photoeye=True))
        self.assertTrue(actual.feed_ready)
        self.assertTrue(actual.pick_ready)
        self.assertTrue(actual.start_request_accepted)
        self.assertTrue(actual.part_at_infeed)
        self.assertEqual(actual.alarm_code, 0)

    def test_missing_part_blocks_pick_but_not_infeed(self):
        actual = evaluate(replace(READY, start_button=True))
        self.assertTrue(actual.feed_ready)
        self.assertFalse(actual.pick_ready)
        self.assertTrue(actual.start_request_accepted)

    def test_fault_priority_and_inhibition(self):
        cases = (
            ({"clamp_extended": True}, 10),
            ({"guard_door_closed": False}, 20),
            ({"stop_button": True}, 30),
            ({"jam_detected": True}, 40),
            ({"motor_ready": False}, 50),
            ({"magazine_low": True}, 60),
            ({"lift_down": False}, 70),
        )
        for overrides, code in cases:
            with self.subTest(overrides=overrides):
                actual = evaluate(replace(READY, start_button=True, **overrides))
                self.assertFalse(actual.feed_ready)
                self.assertFalse(actual.start_request_accepted)
                self.assertEqual(actual.alarm_code, code)

    def test_reset_does_not_clear_active_fault(self):
        actual = evaluate(replace(READY, jam_detected=True, reset_button=True))
        self.assertTrue(actual.reset_request_observed)
        self.assertFalse(actual.feed_ready)
        self.assertEqual(actual.alarm_code, 40)


if __name__ == "__main__":
    unittest.main()
