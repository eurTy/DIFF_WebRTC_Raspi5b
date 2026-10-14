"""Recompute convergence and continuous freshness from retained numerical evidence."""
import argparse
import bisect
import gzip
import hashlib
import json
from pathlib import Path
import statistics

from .summarize import check_cell, percentile


def health_error_count(health):
    return sum(bool(row.get('error')) for row in health)


def continuous_freshness(frames, start, end, limit=250):
    shown = sorted((r for r in frames if not r['dropped'] and r['time'] <= end), key=lambda r:r['time'])
    fresh_ms = 0
    for i, row in enumerate(shown):
        left = max(start, row['time'])
        right = min(end, shown[i+1]['time'] if i+1<len(shown) else end)
        if row['latency_ms'] is not None:
            fresh_ms += max(0, min(right, row['time']+limit-row['latency_ms'])-left)
    times = [r['time'] for r in shown]
    ages = []
    for step in range(int((end-start)/100)+1):
        at = start+step*100
        index = bisect.bisect_right(times,at)-1
        row = shown[index] if index>=0 else None
        ages.append(row['latency_ms']+at-row['time'] if row and row['latency_ms'] is not None else None)
    return {'fresh_250_time_fraction':fresh_ms/(end-start),
            'age_p95_ms':percentile([a for a in ages if a is not None],.95),
            'age_max_ms':max((a for a in ages if a is not None),default=None),
            'unknown_grid_samples':sum(a is None for a in ages),'grid_samples':len(ages)}


def summarize(root, soak_path=None, allow_incomplete=False):
    path=root/'paired-stress.json'
    paired=json.loads(path.read_text(encoding='utf-8'))
    assert paired['complete'] and len(paired['cells'])==paired['intended_cells']==12
    assert sorted((c['link_kbps'],c['feedback'],c['seed']) for c in paired['cells'])==sorted((r,f,s) for r in (128,512) for f in ('single','realtime') for s in (6,7,8))
    for cell in paired['cells']:
        check_cell(cell)
        assert not cell['failures']
    groups=[]
    for rate in (128,512):
        for mode in ('single','realtime'):
            cells=[c for c in paired['cells'] if c['link_kbps']==rate and c['feedback']==mode]
            groups.append({'link_kbps':rate,'feedback':mode,'cells':len(cells),
                           **{key:statistics.mean(c[key] for c in cells) for key in ('fps','latency_p95_ms','fresh_time_fraction_250ms','fresh_weighted_task_fraction')},
                           'worst_cell_p95_ms':max(c['latency_p95_ms'] for c in cells),
                           'task_min':min(c['fresh_weighted_task_fraction'] for c in cells),
                           'task_max':max(c['fresh_weighted_task_fraction'] for c in cells)})
    result={'scope':'Three textures, one run per cell; group P95 is mean of cell P95, not pooled percentile or significance proof',
            'paired_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'paired':groups}
    if soak_path:
        data=json.loads(gzip.decompress(soak_path.read_bytes()) if soak_path.suffix=='.gz' else soak_path.read_text(encoding='utf-8'))
        if not data['complete'] and not allow_incomplete:
            raise ValueError('Incomplete observation: use --allow-incomplete to analyze, never to certify completion')
        frames=data['export']['frames'];samples=data['samples'];start=samples[0]['browser_time'];end=samples[-1]['browser_time']
        assert len(frames)==data['export']['displayed']+data['export']['discarded']
        selected=[r for r in frames if start<=r['time']<=end]
        latencies=[r['latency_ms'] for r in selected if r['latency_ms'] is not None]
        shown=[r for r in frames if not r['dropped'] and start<=r['time']<=end]
        gaps=[b['time']-a['time'] for a,b in zip(shown,shown[1:])]
        sensors=[h['sample']['sensors'] for h in data['health'] if 'sample' in h]
        result['endurance']={'sha256':hashlib.sha256(soak_path.read_bytes()).hexdigest(),'seconds':(end-start)/1000,
                             'run_complete':data['complete'],'browser_errors':data['errors'],
                             'requested_duration_s':data['requested_duration_s'],
                             'meaning':'Observation summary; completion is not a stability pass',
                             'frames_collected':len(frames),'displayed':data['export']['displayed'],
                             'discarded':data['export']['discarded'],'reconnects':data['export']['reconnects'],
                             'fps':len(shown)*1000/(end-start),'payload_mbps':sum(r['bytes'] for r in selected)*8/(end-start)/1000,
                             'arrival_latency_population':'All calibrated arrivals, including discarded frames; outages remain in the AoI denominator',
                             'unknown_latency_arrivals':sum(r['latency_ms'] is None for r in selected),
                             'unknown_latency_displayed':sum(r['latency_ms'] is None for r in shown),
                             'displayed_latency_max_ms':max((r['latency_ms'] for r in shown if r['latency_ms'] is not None),default=None),
                             'p50_ms':percentile(latencies,.5),'p95_ms':percentile(latencies,.95),'max_arrival_ms':max(latencies,default=None),
                             'display_gap_max_ms':max(gaps,default=None),'display_gaps_gt250ms':sum(g>250 for g in gaps),
                             'display_gaps_gt1000ms':sum(g>1000 for g in gaps),'health_errors':health_error_count(data['health']),
                             'temperature_max_c':max((s['temperature_c'] for s in sensors),default=None),
                             'wifi_signal_min_dbm':min((s['wifi_signal_dbm'] for s in sensors if s['wifi_signal_dbm'] is not None),default=None),
                             'aoi_method':'Piecewise integration from draw-complete timestamps and per-frame estimated latency; not the 250ms UI refresh',
                             **continuous_freshness(frames,start,end)}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--results',default='results/convergence-20261013');parser.add_argument('--soak');parser.add_argument('--allow-incomplete',action='store_true');parser.add_argument('--output');args=parser.parse_args()
    root=Path(args.results);result=summarize(root,Path(args.soak) if args.soak else None,args.allow_incomplete)
    Path(args.output or root/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
