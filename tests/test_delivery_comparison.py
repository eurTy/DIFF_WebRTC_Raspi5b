import unittest

from tools.compare_delivery_exports import codec_summary


class DeliveryComparisonTests(unittest.TestCase):
    def test_separated_codec_phases_do_not_include_other_codec_time_or_gap(self):
        data = {
            "sessions": [{"id": "r:1", "config": {"codec": "h264"}, "start_reason": "initial"},
                         {"id": "r:2", "config": {"codec": "jpeg"}, "start_reason": "user_start"},
                         {"id": "r:3", "config": {"codec": "h264"}, "start_reason": "user_start"}],
            "frames": [{"session_id": sid, "at": at, "latency_ms": 50}
                       for sid, at in [("r:1", 250), ("r:1", 750), ("r:2", 1500), ("r:3", 2250), ("r:3", 2750)]],
            "samples": [{"session_id": sid, "at": at, "age_ms": age, "active": True, "visibility": "visible"}
                        for sid, at, age in [("r:1", 250, 100), ("r:1", 750, 200), ("r:2", 1500, 9000),
                                             ("r:3", 2250, 300), ("r:3", 2750, 400)]],
            "events": []
        }
        phases = [{"label": codec, "start_ms": i*1000, "end_ms": (i+1)*1000, "duration_s": 1}
                  for i, codec in enumerate(["h264", "jpeg", "h264"])]
        result = codec_summary(data, [], phases, "h264")
        self.assertEqual(result["observed_fps"], 2)
        self.assertEqual(result["duration_s"], 2)
        self.assertEqual(result["frame_gap_ms"]["count"], 2)
        self.assertEqual(result["frame_gap_ms"]["max"], 500)
        self.assertEqual(result["gaps_over_1000ms"], 0)
        self.assertEqual(result["age_ms"]["p95"], 385)
        self.assertEqual(result["auto_reconnects"], 0)


if __name__ == "__main__":
    unittest.main()
