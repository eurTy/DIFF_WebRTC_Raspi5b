"""Closed-loop tests for the synthetic camera and virtual plant only."""

import unittest

from pc_inspection.virtual_workcell import SimulationControls, VirtualWorkcell


class VirtualWorkcellTests(unittest.TestCase):
    def ready_cell(self):
        cell = VirtualWorkcell()
        cell.tick(0)
        cell.tick(80)
        self.assertTrue(cell.place_part())
        ready = cell.tick(160)
        self.assertTrue(ready.vision.sim_sample_qualified)
        self.assertTrue(ready.vision.sim_motion_observed)
        return cell

    def test_full_visible_cycle_uses_feedback_not_elapsed_move_guess(self):
        cell = self.ready_cell()
        started = cell.tick(200, start=True)
        self.assertTrue(started.station.sim_conveyor_cmd)
        self.assertEqual(started.part_position, cell.plant.ENTRY)
        self.assertFalse(started.glue_applied)

        snapshots = [started]
        for now_ms in range(240, 2001, 40):
            snapshots.append(cell.tick(now_ms))
            if snapshots[-1].station.sim_state == 30:
                break
        self.assertTrue(any(s.part_position > cell.plant.ENTRY for s in snapshots))
        self.assertTrue(any(s.at_glue_station for s in snapshots))
        self.assertTrue(any(s.station.sim_glue_cmd for s in snapshots))
        self.assertTrue(snapshots[-1].glue_applied)
        self.assertEqual(snapshots[-1].station.sim_state, 30)
        self.assertTrue(all(not (s.station.sim_conveyor_cmd and s.station.sim_glue_cmd)
                            for s in snapshots))
        self.assertTrue(all(not s.station.sim_glue_cmd or s.at_glue_station
                            for s in snapshots))

        reset = cell.tick(snapshots[-1].now_ms + 40, reset=True)
        self.assertEqual(reset.station.sim_state, 0)
        self.assertIsNone(reset.part_position)
        self.assertFalse(reset.glue_applied)

    def test_no_part_no_start_and_no_motion(self):
        cell = VirtualWorkcell()
        cell.tick(0)
        cell.tick(80)
        result = cell.tick(160, start=True)
        self.assertFalse(result.vision.sim_motion_observed)
        self.assertEqual(result.station.sim_state, 0)
        self.assertFalse(result.station.sim_conveyor_cmd)
        self.assertIsNone(result.part_position)

    def test_frozen_camera_latches_station_fault(self):
        cell = self.ready_cell()
        cell.tick(200, start=True)
        frozen = SimulationControls(freeze_frames=True)
        cell.tick(240, controls=frozen)
        cell.tick(400, controls=frozen)
        failed = cell.tick(440, controls=frozen)
        self.assertEqual(failed.vision.sim_fault_code, 50)
        self.assertEqual((failed.station.sim_state, failed.station.sim_fault_code),
                         (90, 30))
        self.assertFalse(failed.station.sim_conveyor_cmd or failed.station.sim_glue_cmd)
        position = failed.part_position
        self.assertEqual(cell.tick(480, controls=frozen).part_position, position)

    def test_missing_arrival_feedback_times_out_without_glue(self):
        cell = self.ready_cell()
        stuck = SimulationControls(arrival_sensor=False)
        cell.tick(200, controls=stuck, start=True)
        result = None
        for now_ms in range(240, 2001, 40):
            result = cell.tick(now_ms, controls=stuck)
            self.assertFalse(result.station.sim_glue_cmd)
            if result.station.sim_state == 90:
                break
        self.assertTrue(result.at_glue_station)
        self.assertEqual(result.station.sim_fault_code, 60)
        self.assertFalse(result.glue_applied)

    def test_guard_and_stop_remove_virtual_commands(self):
        for controls, stop, fault in (
            (SimulationControls(guard_closed=False), False, 20),
            (SimulationControls(), True, 10),
        ):
            with self.subTest(fault=fault):
                cell = self.ready_cell()
                cell.tick(200, start=True)
                result = cell.tick(240, controls=controls, stop=stop)
                self.assertEqual(result.station.sim_fault_code, fault)
                self.assertFalse(result.station.sim_conveyor_cmd)
                self.assertFalse(result.station.sim_glue_cmd)

    def test_rejects_time_reversal(self):
        cell = VirtualWorkcell()
        cell.tick(100)
        with self.assertRaises(ValueError):
            cell.tick(99)

    def test_long_ui_pause_fails_closed_without_replaying_motion(self):
        cell = self.ready_cell()
        cell.tick(200, start=True)
        moving = cell.tick(240)
        result = cell.tick(1240)
        self.assertEqual(result.part_position, moving.part_position)
        self.assertEqual(result.vision.sim_fault_code, 10)
        self.assertEqual((result.station.sim_state, result.station.sim_fault_code),
                         (90, 30))
        self.assertFalse(result.station.sim_conveyor_cmd or result.station.sim_glue_cmd)


if __name__ == "__main__":
    unittest.main()
