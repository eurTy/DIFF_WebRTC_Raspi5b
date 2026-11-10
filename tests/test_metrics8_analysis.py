import unittest

from tools.analyze_metrics8 import clock_at, derive, prefix, summarize
from tools.analyze_mobile_exports import distribution


class Metrics8AnalysisTests(unittest.TestCase):
    def test_cumulative_history_requires_exact_prefix(self):
        self.assertTrue(prefix([1, 2], [1, 2, 3]))
        self.assertFalse(prefix([1, 3], [1, 2, 3]))
        self.assertFalse(prefix([1, 2, 3], [1, 2]))

    def test_clock_uses_prior_sample_and_expires(self):
        clocks = [{"at": 100, "estimate": {"offset": 10, "rtt": 20}},
                  {"at": 200, "estimate": {"offset": 11, "rtt": 21}}]
        self.assertIsNone(clock_at(clocks, 99))
        self.assertEqual(clock_at(clocks, 150)["offset"], 10)
        self.assertEqual(clock_at(clocks, 200)["offset"], 11)
        self.assertIsNotNone(clock_at(clocks, 10200))
        self.assertIsNone(clock_at(clocks, 10201))

    def test_packet_join_is_session_specific_and_clocks_not_mixed(self):
        data = {
            "sessions": [{"id": "run:1"}, {"id": "run:2"}],
            "clocks": [{"session_id": "run:1", "at": 100,
                        "estimate": {"offset": 1000, "rtt": 30}}],
            "frames": [{"session_id": "run:1", "seq": 1, "latency_ms": 150},
                       {"session_id": "run:2", "seq": 1, "latency_ms": 900}],
            "packets": [{"session_id": "run:1", "seq": 1, "received_at_ms": 200,
                         "captured_ms": 1060, "server_sent_ms": 1100,
                         "presented_at_ms": 210, "ack_at_ms": 205,
                         "part_bytes": [7, 13], "payload_bytes": 17},
                        {"session_id": "run:2", "seq": 1, "received_at_ms": 220,
                         "captured_ms": 1150, "server_sent_ms": 1180,
                         "payload_bytes": 10}]
        }
        first, second = derive(data)
        self.assertEqual(first["publication_to_send_ms"], 40)
        self.assertEqual(first["send_to_receive_estimate_ms"], 100)
        self.assertEqual(first["receive_to_present_ms"], 10)
        self.assertEqual(first["send_to_receive_from_presented_ms"], 100)
        self.assertEqual(first["payload_shortfall_bytes"], 3)
        self.assertIsNone(second["send_to_receive_estimate_ms"])
        self.assertIsNone(second["receive_to_present_ms"])
        self.assertIsNone(second["payload_shortfall_bytes"])

    def test_summary_keeps_reconnect_wait_and_missing_age(self):
        data = {
            "frames": [{"session_id": "run:1", "seq": 1, "at": 100, "latency_ms": 20},
                       {"session_id": "run:2", "seq": 1, "at": 900, "latency_ms": 30}],
            "samples": [{"session_id": "run:1", "at": 250, "age_ms": 100,
                         "visibility": "visible", "active": True},
                        {"session_id": "run:1", "at": 500, "age_ms": None,
                         "visibility": "visible", "active": True},
                        {"session_id": "run:2", "at": 750, "age_ms": 900,
                         "visibility": "visible", "active": True},
                        {"session_id": "run:2", "at": 800, "age_ms": 10000,
                         "visibility": "hidden", "active": True}],
            "events": []
        }
        result = summarize(data, [], ["run:1", "run:2"], 0, 1000, "jpeg")
        self.assertEqual(result["observed_fps"], 2)
        self.assertEqual(result["age_ms"]["p50"], 500)
        self.assertEqual(result["age_ms"]["missing"], 1)
        self.assertEqual(result["age_over_500_pct"], 50)
        self.assertEqual(result["longest_gaps"][0]["gap_ms"], 800)
        self.assertTrue(result["longest_gaps"][0]["cross_session"])
        self.assertIsNone(result["receive_to_present_ms"]["p95"])

    def test_explicit_payload_length_is_preserved_for_both_codecs(self):
        packet = {"session_id": "run:1", "seq": 1, "codec": "h264",
                  "received_at_ms": 200, "captured_ms": 100,
                  "declared_payload_bytes": 8292, "payload_bytes": 3085}
        data = {"sessions": [{"id": "run:1"}], "clocks": [], "frames": [],
                "packets": [packet, {**packet, "seq": 2, "codec": "jpeg",
                                     "part_bytes": [4000, 4292]}]}
        first, second = derive(data)
        self.assertEqual(first["payload_shortfall_bytes"], 5207)
        self.assertIsNone(first["part_total_bytes"])
        self.assertEqual(second["declared_payload_bytes"], 8292)
        self.assertEqual(second["part_total_bytes"], 8292)
        self.assertEqual(second["payload_shortfall_bytes"], 5207)

    def test_distribution_does_not_turn_missing_or_bool_into_zero(self):
        result = distribution([None, float("nan"), True, 0, 100])
        self.assertEqual((result["count"], result["missing"]), (2, 3))
        self.assertEqual((result["p50"], result["p95"]), (50, 95))


if __name__ == "__main__":
    unittest.main()
