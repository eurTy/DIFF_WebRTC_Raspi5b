import unittest

from tools.correlate_delivery import correlate


class CorrelationTests(unittest.TestCase):
    def test_same_sequence_on_another_session_is_not_joined(self):
        data = {"sessions": [{"id": "run:1", "server_session_id": "server1", "config": {"codec": "jpeg"}}],
                "packets": [{"session_id": "run:1", "seq": 5, "declared_payload_bytes": 100,
                             "payload_bytes": 29, "payload_crc32": 123, "actual_crc32": 789}],
                "events": [{"type": "integrity_rejected", "session_id": "run:1", "seq": 5, "at": 200}], "clocks": [],
                "server_diagnostics": {"boot_id": "boot", "discarded": 0, "sessions": [], "events": [
                    {"server_session_id": "server2", "event": "write_start", "seq": 5, "expected_bytes": 999, "expected_crc32": 456},
                    {"server_session_id": "server1", "event": "write_start", "seq": 5, "expected_bytes": 100, "expected_crc32": 123}]}}
        fault = correlate(data)["faults"][0]
        self.assertEqual(fault["server_write"]["expected_bytes"], 100)
        self.assertTrue(fault["server_header_matches_client_header"])
        self.assertIsNone(fault["receive_estimate_server_ms"])

    def test_missing_server_trace_is_not_reported_as_zero_faults(self):
        with self.assertRaises(ValueError):
            correlate({})


if __name__ == "__main__":
    unittest.main()
