import unittest

from pc_inspection.core import AssemblyFixture, SimulatedCell, SlotDetector


class PcInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = AssemblyFixture()

    def inspect(self, scenario, detector=None):
        detector = detector or SlotDetector(self.fixture.empty)
        result = None
        for frame_id in range(3):
            result = detector.inspect(self.fixture.frame(scenario), frame_id, 1000, 1000)
        return result

    def test_all_fixture_verdicts(self):
        expected = {
            "OK": ("PRESENT", "PRESENT", "PRESENT"),
            "SLOT_2_MISSING": ("PRESENT", "MISSING", "PRESENT"),
            "SLOT_3_MISPLACED": ("PRESENT", "PRESENT", "MISPLACED"),
            "SLOT_1_OCCLUDED": ("UNKNOWN", "PRESENT", "PRESENT"),
        }
        for scenario, statuses in expected.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(self.inspect(scenario).statuses, statuses)

    def test_transient_change_blocks_before_confirmation(self):
        detector = SlotDetector(self.fixture.empty)
        self.inspect("OK", detector)
        result = detector.inspect(self.fixture.frame("SLOT_2_MISSING"), 4, 1000, 1000)
        self.assertEqual(result.statuses[1], "UNKNOWN")
        self.assertFalse(result.is_ready(1000))
        detector.inspect(self.fixture.frame("SLOT_2_MISSING"), 5, 1000, 1000)
        result = detector.inspect(self.fixture.frame("SLOT_2_MISSING"), 6, 1000, 1000)
        self.assertEqual(result.statuses[1], "MISSING")

    def test_stale_or_future_frame_is_unknown(self):
        detector = SlotDetector(self.fixture.empty, settle_frames=1)
        for captured_ms, now_ms in ((1000, 1301), (1001, 1000)):
            result = detector.inspect(self.fixture.frame(), 1, captured_ms, now_ms)
            self.assertEqual(result.statuses, ("UNKNOWN",) * 3)
            self.assertFalse(result.is_ready(now_ms))

    def test_cycle_and_mutually_exclusive_outputs(self):
        inspection = self.inspect("OK")
        cell = SimulatedCell(move_ms=100, glue_ms=50)
        self.assertEqual(cell.tick(inspection, 1000).state, "READY")
        moving = cell.tick(inspection, 1001, start=True)
        self.assertTrue(moving.conveyor)
        self.assertFalse(moving.glue)
        gluing = cell.tick(inspection, 1101)
        self.assertFalse(gluing.conveyor)
        self.assertTrue(gluing.glue)
        done = cell.tick(inspection, 1151)
        self.assertEqual(done.state, "COMPLETE")
        self.assertFalse(done.conveyor or done.glue)

    def test_loss_of_vision_stops_and_latches_fault(self):
        inspection = self.inspect("OK")
        cell = SimulatedCell()
        cell.tick(inspection, 1000)
        cell.tick(inspection, 1001, start=True)
        failed = cell.tick(inspection, 1301)
        self.assertEqual(failed.state, "FAULT")
        self.assertFalse(failed.conveyor or failed.glue)
        self.assertEqual(cell.tick(inspection, 1002).state, "FAULT")
        self.assertEqual(cell.tick(inspection, 1002, reset=True).state, "READY")

    def test_missing_part_and_guard_block_start(self):
        inspection = self.inspect("SLOT_2_MISSING")
        cell = SimulatedCell()
        blocked = cell.tick(inspection, 1000, start=True)
        self.assertEqual(blocked.state, "BLOCKED")
        self.assertFalse(blocked.conveyor or blocked.glue)
        normal = self.inspect("OK")
        cell.tick(normal, 1000)
        stopped = cell.tick(normal, 1001, guard_closed=False, start=True)
        self.assertEqual(stopped.state, "FAULT")
        self.assertFalse(stopped.conveyor or stopped.glue)


if __name__ == "__main__":
    unittest.main()
