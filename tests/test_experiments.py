import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from experiments.network import run
from experiments.summarize import check_cell,percentile


class Arithmetic(unittest.TestCase):
    def test_time_denominator_includes_no_frame(self):
        cell={'duration_s':1,'warmup_s':0,'frames':1,'payload_mbps':.008,
              'records':[{'elapsed_ms':500,'latency_ms':50,'bytes':1000,'weighted_marker_success':.6}],
              'fresh_time_fraction_250ms':.3,'fresh_weighted_task_fraction':.18}
        self.assertEqual(len(check_cell(cell)),1)
        cell['fresh_time_fraction_250ms']=1
        with self.assertRaises(AssertionError):check_cell(cell)

    def test_percentile_uses_observations(self):
        self.assertEqual(percentile([0,10,20],.5),10)
        self.assertEqual(percentile([0,10,20],.95),19)
        self.assertIsNone(percentile([],.5))


class Resume(unittest.IsolatedAsyncioTestCase):
    async def test_resume_preserves_completed_cells(self):
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'result.json'
            old={'kbps':512,'policy':'full','repeat':0,'seed':7,'duration_s':1,'warmup_s':0,'unique':'retained'}
            file.write_text(json.dumps({'server_url':'http://example.invalid','schedule_seed':1,'intended_cells':4,
                                        'cells':[old],'receiver_source_sha256':{'previous':'hash'}}))
            args=argparse.Namespace(url='http://example.invalid',schedule_seed=1,repeats=1,rates=[512],duration=1,warmup=0,output=str(file),resume=True)
            async def fake(url,mode,rate,seed,duration,warmup):
                return {'policy':mode,'kbps':rate,'seed':seed,'duration_s':duration,'warmup_s':warmup,'frames':1,
                        'latency_p50_ms':1,'fresh_time_fraction_250ms':1,'fresh_weighted_task_fraction':1,'failures':[]}
            with patch('experiments.network.cell',side_effect=fake) as mocked:
                await run(args)
                self.assertEqual(mocked.call_count,3)
            saved=json.loads(file.read_text())
            self.assertTrue(saved['complete']);self.assertEqual(saved['cells'][0]['unique'],'retained')
            self.assertEqual(saved['cells'][0]['receiver_source_sha256'],{'previous':'hash'})


if __name__=='__main__':unittest.main()
