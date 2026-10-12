"""Randomized, paired feedback-filter ablation with the same task policy."""
import argparse
import asyncio
import json
from pathlib import Path
import random
from aiohttp import ClientError
import cv2
from experiments.network import cell
from experiments.provenance import source_hashes


async def run(args):
    hashes=source_hashes()
    schedule=[(rate,seed,feedback,repeat) for repeat in range(args.repeats) for rate in args.rates for seed in args.seeds for feedback in ['single','median']]
    random.Random(20261015).shuffle(schedule)
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    rows=[];failures=[]
    if args.resume and destination.exists():
        previous=json.loads(destination.read_text())
        if previous['schedule']!=[list(row) for row in schedule] or previous['url']!=args.url:raise ValueError('Mismatched resume')
        rows=previous['cells'];failures=previous['setup_failures']
    done={(r['kbps'],r['seed'],r['feedback'],r['repeat']) for r in rows}
    def save():
        destination.write_text(json.dumps({'experiment':'PAIRED_FEEDBACK_ABLATION','url':args.url,'schedule':schedule,'receiver_source_sha256':hashes,
                                           'source_hash_snapshot':'run_start','complete':len(rows)==len(schedule),'cells':rows,'setup_failures':failures},indent=2),encoding='utf-8')
    for rate,seed,feedback,repeat in schedule:
        if (rate,seed,feedback,repeat) in done:continue
        for attempt in range(3):
            try:
                result=await cell(args.url,'semantic',rate,seed,args.duration,3,feedback)
                break
            except (TimeoutError,OSError,ValueError,ClientError) as error:
                failures.append({'rate':rate,'seed':seed,'feedback':feedback,'repeat':repeat,'attempt':attempt,'error':type(error).__name__})
                save()
                if attempt==2:raise
                await asyncio.sleep(2)
        result['repeat']=repeat;rows.append(result);save()
        print(json.dumps({k:result[k] for k in ['kbps','seed','feedback','frames','fresh_time_fraction_250ms','fresh_weighted_task_fraction']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://192.168.3.19:8088');parser.add_argument('--rates',type=int,nargs='+',default=[512,1024,2048]);parser.add_argument('--seeds',type=int,nargs='+',default=[7,8]);parser.add_argument('--repeats',type=int,default=2);parser.add_argument('--duration',type=float,default=12);parser.add_argument('--resume',action='store_true');parser.add_argument('--output',default='results/feedback-pi-fixture.json');args=parser.parse_args()
    cv2.setNumThreads(1);asyncio.run(run(args))
