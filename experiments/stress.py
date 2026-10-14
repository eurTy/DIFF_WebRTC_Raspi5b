"""Rate mismatch test: configured 1200 kbps versus a separately constrained TCP relay."""
import argparse
import asyncio
import json
import random
from pathlib import Path

import cv2

from .network import cell
from .provenance import source_hashes


async def run(args):
    document = {'scope':'Application TCP pacing, not RF packet loss; Python decode endpoint; all arrivals retained',
                'url':args.url,'source_sha256':source_hashes(),'schedule_seed':args.schedule_seed,'cells':[], 'complete':False}
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    cells=[(rate,feedback,seed) for rate in args.rates for feedback in ('single','realtime') for seed in args.seeds]
    random.Random(args.schedule_seed).shuffle(cells)
    document['intended_cells']=len(cells)
    for rate,feedback,seed in cells:
        result=await cell(args.url,'semantic',1200,seed,args.duration,3,feedback,link_kbps=rate)
        document['cells'].append(result)
        document['complete']=len(document['cells'])==len(cells)
        output.write_text(json.dumps(document,indent=2),encoding='utf-8')
        print(json.dumps({key:result[key] for key in ('feedback','seed','link_kbps','fps','latency_p50_ms','latency_p95_ms','fresh_time_fraction_250ms','fresh_weighted_task_fraction','failures')}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:18088')
    parser.add_argument('--output',default='runtime/stress.json')
    parser.add_argument('--duration',type=float,default=15)
    parser.add_argument('--rates',nargs='+',type=int,default=[1200,512,128])
    parser.add_argument('--seeds',nargs='+',type=int,default=[6,7,8])
    parser.add_argument('--schedule-seed',type=int,default=20261013)
    cv2.setNumThreads(1)
    asyncio.run(run(parser.parse_args()))
