"""Synthetic sequence watchdog tests; no PLC or camera connection."""

import unittest

from pc_inspection.vision_sequence_sim import VisionSequenceSim


class VisionSequenceSimTests(unittest.TestCase):
    def setUp(self):
        self.watch = VisionSequenceSim()

    def scan(self, now_ms, **changes):
        values = dict(simulated_source=True, link_ok=True, sample_seq=1,
                      boot_tag=7, max_stall_ms=500, now_ms=now_ms)
        values.update(changes)
        return self.watch.scan(**values)

    def test_requires_two_progressing_samples(self):
        self.assertFalse(self.scan(0).sequence_advanced_within_window)
        self.assertFalse(self.scan(10).boot_tag_stable)
        self.assertTrue(self.scan(20, sample_seq=2).sequence_advanced_within_window)

    def test_stall_expires_and_new_progress_recovers(self):
        self.scan(0)
        self.assertTrue(self.scan(10, sample_seq=2).sequence_advanced_within_window)
        self.assertTrue(self.scan(20, sample_seq=2).sequence_advanced_within_window)
        self.assertTrue(self.scan(519, sample_seq=2).sequence_advanced_within_window)
        stalled = self.scan(520, sample_seq=2)
        self.assertFalse(stalled.sequence_advanced_within_window)
        self.assertTrue(stalled.boot_tag_stable)
        self.assertTrue(self.scan(530, sample_seq=3).sequence_advanced_within_window)

    def test_link_loss_requires_new_baseline(self):
        self.scan(0)
        self.scan(10, sample_seq=2)
        self.assertFalse(self.scan(20, sample_seq=2, link_ok=False).boot_tag_stable)
        self.assertFalse(self.scan(30, sample_seq=3).sequence_advanced_within_window)
        self.assertTrue(self.scan(40, sample_seq=4).sequence_advanced_within_window)

    def test_boot_change_and_replay_fail_closed(self):
        self.scan(0)
        self.scan(10, sample_seq=2)
        self.assertFalse(self.scan(20, sample_seq=1).sequence_advanced_within_window)
        self.assertTrue(self.scan(30, sample_seq=1).boot_tag_stable)
        self.assertTrue(self.scan(40, sample_seq=3).sequence_advanced_within_window)
        self.assertFalse(self.scan(50, sample_seq=1, boot_tag=8).boot_tag_stable)
        self.assertTrue(self.scan(60, sample_seq=2, boot_tag=8).boot_tag_stable)

    def test_invalid_inputs_and_clock_regression(self):
        self.assertFalse(self.scan(0, sample_seq=0).sequence_advanced_within_window)
        self.assertFalse(self.scan(10, boot_tag=0).sequence_advanced_within_window)
        self.assertFalse(self.scan(20, max_stall_ms=0).sequence_advanced_within_window)
        self.scan(30)
        self.assertTrue(self.scan(40, sample_seq=2).sequence_advanced_within_window)
        self.assertFalse(self.scan(20, sample_seq=2).sequence_advanced_within_window)


if __name__ == "__main__":
    unittest.main()
