"""Capacity transitions on one continuous stream; no host network settings change."""
import argparse
import asyncio
import json
from pathlib import Path
import statistics

import cv2
from .network import cell
from .provenance import source_hashes
from .summarize import check_cell, percentile


async def run(args):
    profile=[(0,1200),(8,128),(18,512),(28,1200)]
    result={'scope':'Application-paced TCP capacity changes, not RF loss; synthetic seed7; Python decode; one run per mode',
            'profile_seconds_kbps':profile,'source_sha256':source_hashes(),'complete':False,'cells':[]}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    for feedback in ('realtime','single'):
        measured=await cell(args.url,'semantic',1200,7,35,3,feedback,link_profile=profile)
        check_cell(measured)
        phases=[]
        for start,end,rate in [(3,8,1200),(8,18,128),(18,28,512),(28,38,1200)]:
            rows=[r for r in measured['records'] if start*1000<=r['elapsed_ms']<end*1000]
            fresh=[];task=[]
            for step in range(start*10,end*10):
                at=step*100;previous=[r for r in measured['records'] if r['elapsed_ms']<=at]
                row=previous[-1] if previous else None
                good=bool(row and 0<=row['latency_ms']+at-row['elapsed_ms']<=250)
                fresh.append(good);task.append((row.get('weighted_marker_success',0) if row else 0)*good)
            phases.append({'start_s':start,'end_s':end,'link_kbps':rate,'frames':len(rows),'fps':len(rows)/(end-start),
                           'p95_ms':percentile([r['latency_ms'] for r in rows],.95),'fresh_fraction':statistics.mean(fresh),
                           'fresh_task_fraction':statistics.mean(task),'budget_min':min((r['budget_bytes'] for r in rows),default=None),
                           'budget_max':max((r['budget_bytes'] for r in rows),default=None)})
        measured['phases']=phases;result['cells'].append(measured);result['complete']=len(result['cells'])==2
        path.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps({'feedback':feedback,'failures':measured['failures'],'phases':phases}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:18088');parser.add_argument('--output',default='results/convergence-20261013/capacity-steps.json')
    cv2.setNumThreads(1);asyncio.run(run(parser.parse_args()))
