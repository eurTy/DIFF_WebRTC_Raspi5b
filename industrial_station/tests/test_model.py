import unittest

from industrial_station.model import Station
from industrial_station.protocol import Action, Fault, Reason, Result, State, command, decode_status, words


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.model = Station(clock=lambda: self.now / 1000, boot_id=123, move_ms=100, timeout_ms=300)
        self.seq = 0
        self.send(Action.ARM)

    def send(self, action, param=0, **kwargs):
        self.seq += 1
        values = command(action, self.seq, param, kwargs.get("boot", 123), kwargs.get("session", 456),
                         kwargs.get("deadline", self.now + 1000))
        return self.model.execute(values)

    def advance(self, milliseconds):
        self.now += milliseconds
        self.model.tick()

    def test_normal_manual_cycle_and_simulation_quality(self):
        self.assertEqual(self.send(Action.EXTEND)[1], Result.ACCEPTED)
        self.advance(100)
        self.assertEqual(self.model.state, State.EXTENDED)
        self.send(Action.RETRACT)
        self.advance(100)
        sample = decode_status(self.model.registers())
        self.assertEqual(sample["cycles"], 1)
        self.assertEqual(sample["source"], "SIMULATED")
        self.assertIsNone(sample["current_mA"])
        self.assertFalse(sample["extend_output"] or sample["retract_output"])

    def test_auto_cycle_and_mode_interlocks(self):
        self.assertEqual(self.send(Action.CYCLE)[2], Reason.INTERLOCK)
        self.send(Action.MODE, 2)
        self.assertEqual(self.send(Action.EXTEND)[2], Reason.INTERLOCK)
        self.send(Action.CYCLE)
        self.assertEqual(self.send(Action.MODE, 1)[2], Reason.BUSY)
        self.advance(100)
        self.assertEqual(self.model.state, State.RETRACTING)
        self.advance(100)
        self.assertEqual(self.model.cycles, 1)
        self.assertEqual(self.model.last_action_ms, 200)

    def test_duplicate_commands_execute_once_and_modified_duplicate_rejected(self):
        values = command(Action.EXTEND, 2, 0, 123, 456, 1000)
        for _ in range(10):
            self.assertEqual(self.model.execute(values)[1], Result.ACCEPTED)
        self.assertEqual(self.model.motion_starts, 1)
        modified = values.copy()
        modified[0] = Action.RETRACT
        self.assertEqual(self.model.execute(modified)[2], Reason.SEQUENCE)
        self.advance(100)
        self.assertEqual(self.model.execute(values)[1], Result.COMPLETED)

    def test_invalid_boot_session_deadline_and_sequence(self):
        for override, reason in [({"boot": 124}, Reason.BOOT), ({"session": 457}, Reason.SESSION),
                                 ({"deadline": 0}, Reason.EXPIRED), ({"deadline": 6000}, Reason.EXPIRED)]:
            self.assertEqual(self.send(Action.EXTEND, **override)[2], reason)
        self.assertEqual(self.model.motion_starts, 0)

    def test_timeout_and_fault_reset_do_not_start_motion(self):
        self.send(Action.INJECT, Fault.TARGET_LIMIT_MISSING)
        self.send(Action.EXTEND)
        self.advance(301)
        self.assertEqual(self.model.alarm, Reason.ACTION_TIMEOUT)
        self.assertEqual(self.send(Action.RESET)[2], Reason.FAULT_ACTIVE)
        self.send(Action.INJECT, Fault.NONE)
        self.send(Action.RESET)
        self.assertIsNone(self.model.active)
        self.assertEqual(self.model.state, State.EXTENDED)

    def test_both_limits_and_slow_motion(self):
        self.send(Action.INJECT, Fault.BOTH_LIMITS)
        self.assertEqual(self.model.alarm, Reason.LIMIT_CONFLICT)
        self.send(Action.INJECT, 0)
        self.send(Action.RESET)
        self.send(Action.INJECT, Fault.SLOW_MOTION)
        self.send(Action.EXTEND)
        self.advance(301)
        self.assertEqual(self.model.alarm, Reason.ACTION_TIMEOUT)

    def test_local_watchdog_stops_and_retires_session(self):
        self.send(Action.INJECT, Fault.TARGET_LIMIT_MISSING)
        self.model.timeout_ms = 5000
        self.send(Action.EXTEND)
        self.advance(1001)
        self.assertEqual(self.model.alarm, Reason.COMM_TIMEOUT)
        self.assertEqual(self.model.owner, 0)
        self.assertEqual(self.send(Action.ARM)[2], Reason.SESSION)
        self.assertEqual(self.send(Action.ARM, session=999)[1], Result.COMPLETED)
        self.assertIsNone(self.model.active)

    def test_repeated_keepalive_counter_does_not_extend_lease(self):
        self.advance(500)
        self.assertTrue(self.model.keepalive([*words(123), *words(456), *words(1)]))
        self.advance(900)
        self.assertFalse(self.model.keepalive([*words(123), *words(456), *words(1)]))
        self.advance(101)
        self.assertEqual(self.model.owner, 0)

    def test_stop_at_intermediate_position(self):
        self.send(Action.EXTEND)
        self.advance(40)
        self.send(Action.STOP)
        self.assertEqual(self.model.finished[2], Reason.STOPPED)
        self.assertEqual(self.model.state, State.STOPPED)
        self.advance(100)
        self.assertEqual(self.model.position, .4)

    def test_cache_eviction_does_not_allow_replay_or_evict_active_command(self):
        values = command(Action.EXTEND, 2, 0, 123, 456, 1000)
        self.model.execute(values)
        for sequence in range(3, 150):
            self.model.execute(command(Action.MODE, sequence, 1, 123, 456, 1000))
        self.assertLessEqual(len(self.model.cache), 128)
        self.advance(100)
        self.assertEqual(self.model.execute(values)[1], Result.COMPLETED)
        self.assertEqual(self.model.execute(command(Action.ARM, 1, 0, 123, 456, 1000))[2], Reason.SEQUENCE)

    def test_wire_requires_simulated_protocol_marker(self):
        r = self.model.registers()
        r[11] = 0
        with self.assertRaises(ValueError):
            decode_status(r)

    def test_new_session_clears_previous_motion_result_namespace(self):
        self.send(Action.EXTEND)
        self.advance(100)
        self.assertEqual(self.model.finished[0], 2)
        self.advance(1001)
        self.model.execute(command(Action.ARM, 1, 0, 123, 999, self.now + 1000))
        self.assertEqual(self.model.finished[0], 0)
        result = self.model.execute(command(Action.RETRACT, 2, 0, 123, 999, self.now + 1000))
        self.assertEqual(result[1], Result.ACCEPTED)
        self.assertEqual(decode_status(self.model.registers())["finished_sequence"], 0)


if __name__ == "__main__":
    unittest.main()
