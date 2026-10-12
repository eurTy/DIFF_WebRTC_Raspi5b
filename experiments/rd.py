"""Paired synthetic rate-distortion sweep. No network or glass-to-glass claims."""
import argparse
import json
from pathlib import Path
import platform
import time
import cv2
import numpy as np
from semantic_video.encoder import Encoder, MODES
from semantic_video.metrics import evaluate
from semantic_video.protocol import reconstruct, unpack
from semantic_video.source import FixtureSource


def run(frames):
    cv2.setNumThreads(1)
    rows=[]
    for seed in (6,7,8):
        source=FixtureSource(seed)
        for kbps in (512,1024,2048):
            for mode in MODES:
                encoder=Encoder(mode,kbps=kbps,fps=10)
                # Seeded simulated time makes aging independent of CPU benchmark duration.
                encoder.started_ms=0
                for index in range(frames):
                    frame=source.at(index*7,0)
                    from unittest.mock import patch
                    with patch('semantic_video.encoder.mono_ms',return_value=index*100):
                        packet,meta=encoder.encode(frame)
                    decoded=reconstruct(*unpack(packet))
                    quality=evaluate(frame.image,decoded)
                    rows.append({'seed':seed,'kbps':kbps,'policy':mode,'sample':index,'bytes':len(packet),
                                 'encode_ms':meta['encode_ms'],'level':meta['level'],'budget_exceeded':meta['budget_exceeded'],
                                 'parts':[p['name'] for p in meta['parts']],**quality})
                    encoder.acknowledge(meta,50,len(packet),now=index*100+50)
    summary=[]
    for seed in (6,7,8):
        for kbps in (512,1024,2048):
            for mode in MODES:
                group=[r for r in rows if (r['seed'],r['kbps'],r['policy'])==(seed,kbps,mode)]
                summary.append({'seed':seed,'kbps':kbps,'policy':mode,'n':len(group),
                                **{key:float(np.mean([r[key] for r in group])) for key in ['bytes','encode_ms','weighted_roi_psnr_db','background_psnr_db','whole_psnr_db']},
                                'marker_success':float(np.mean([sum(v['weight']*v['marker_correct'] for v in r['regions'].values())/10 for r in group])),
                                'over_budget_fraction':float(np.mean([r['budget_exceeded'] for r in group]))})
    return {'experiment':'PAIRED_SYNTHETIC_RD_NOT_NETWORK','platform':platform.platform(),'opencv':cv2.__version__,
            'frames_per_cell':frames,'psnr_exact_match_cap_db':99,'aging_clock':'100 ms synthetic interval; ACK at +50 ms',
            'summary':summary,'rows':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--frames',type=int,default=30);parser.add_argument('--output',default='results/rd.json');args=parser.parse_args()
    started=time.perf_counter();result=run(args.frames);result['elapsed_s']=time.perf_counter()-started
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'rows':len(result['rows']),'elapsed_s':result['elapsed_s'],'output':str(destination)}))
