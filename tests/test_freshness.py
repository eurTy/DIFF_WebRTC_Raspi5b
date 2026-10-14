import unittest
from experiments.summarize_convergence import continuous_freshness, health_error_count


class Freshness(unittest.TestCase):
    def test_null_health_error_is_success(self):
        self.assertEqual(health_error_count([{'error': None}, {}, {'error': 'timeout'}]), 1)

    def test_freeze_and_dropped_arrival_stay_in_denominator(self):
        frames=[{'time':0,'latency_ms':50,'dropped':False},
                {'time':400,'latency_ms':900,'dropped':True},
                {'time':500,'latency_ms':100,'dropped':False}]
        result=continuous_freshness(frames,0,1000)
        self.assertAlmostEqual(result['fresh_250_time_fraction'],.35)
        self.assertEqual(result['age_max_ms'],600)

    def test_no_calibrated_frame_is_not_fresh(self):
        result=continuous_freshness([{'time':0,'latency_ms':None,'dropped':False}],0,1000)
        self.assertEqual(result['fresh_250_time_fraction'],0)
        self.assertEqual(result['unknown_grid_samples'],result['grid_samples'])
