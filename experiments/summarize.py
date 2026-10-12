"""Independent arithmetic checks and transparent aggregation for the thesis."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def percentile(values, fraction):
    if not values: return None
    values=sorted(values);position=(len(values)-1)*fraction
    lower=int(position);upper=min(lower+1,len(values)-1)
    return values[lower]+(values[upper]-values[lower])*(position-lower)


def check_cell(cell):
    records=cell['records'];start=cell['warmup_s']*1000;end=start+cell['duration_s']*1000
    selected=[r for r in records if start<=r['elapsed_ms']<end]
    assert len(selected)==cell['frames'], 'Frame denominator mismatch'
    assert all(a['elapsed_ms']<=b['elapsed_ms'] for a,b in zip(records,records[1:])), 'Out of order records'
    flags=[];scores=[]
    for step in range(round(cell['duration_s']*10)):
        at=start+step*100
        preceding=[r for r in records if r['elapsed_ms']<=at]
        if not preceding:
            flags.append(False);scores.append(0);continue
        last=preceding[-1];age=last['latency_ms']+at-last['elapsed_ms']
        good=0<=age<=250;flags.append(good)
        scores.append(last.get('weighted_marker_success',0)*good)
    assert abs(statistics.mean(flags)-cell['fresh_time_fraction_250ms'])<1e-9
    if cell['fresh_weighted_task_fraction'] is not None:
        assert abs(statistics.mean(scores)-cell['fresh_weighted_task_fraction'])<1e-9
    assert abs(sum(r['bytes'] for r in selected)*8/cell['duration_s']/1e6-cell['payload_mbps'])<1e-9
    return selected


def summarize(root):
    files=['rd-pi.json','network-pi-fixture.json','network-pi-confirmation.json','network-pi-live.json']
    evidence={name:json.loads((root/name).read_text(encoding='utf-8')) for name in files}
    network=[];live=[]
    for name in files[1:]:
        document=evidence[name]
        assert document['complete'] and len(document['cells'])==document['intended_cells']
        for cell in document['cells']:
            selected=check_cell(cell)
            (live if name.endswith('live.json') else network).append({**cell,'selected':selected,'file':name})
    groups=[]
    for kbps in sorted({c['kbps'] for c in network}):
        for mode in ['full','uniform','static-roi','semantic']:
            group=[c for c in network if c['kbps']==kbps and c['policy']==mode]
            assert len(group)==4 and sorted(c['seed'] for c in group)==[7,7,8,8]
            selected=[r for c in group for r in c['selected']]
            groups.append({'kbps':kbps,'policy':mode,'sessions':len(group),'seconds':sum(c['duration_s'] for c in group),
                           'received_frames':len(selected),'fps_mean':statistics.mean(c['fps'] for c in group),
                           'latency_p50_ms':percentile([r['latency_ms'] for r in selected],.5),
                           'latency_p95_ms':percentile([r['latency_ms'] for r in selected],.95),
                           'fresh_fraction':statistics.mean(c['fresh_time_fraction_250ms'] for c in group),
                           'conservative_fresh_fraction':statistics.mean(c['conservative_fresh_time_fraction_250ms'] for c in group),
                           'task_fraction':statistics.mean(c['fresh_weighted_task_fraction'] for c in group),
                           'fresh_min':min(c['fresh_time_fraction_250ms'] for c in group),'fresh_max':max(c['fresh_time_fraction_250ms'] for c in group),
                           'clock_warning_sessions':sum(c['clock_quality_warning'] for c in group),
                           'failed_sessions':sum(bool(c['failures']) for c in group)})
    rd=evidence['rd-pi.json'];assert len(rd['rows'])==1080
    rd_groups=[]
    for kbps in (512,1024,2048):
        for mode in ['full','uniform','static-roi','semantic']:
            rows=[r for r in rd['rows'] if r['kbps']==kbps and r['policy']==mode];assert len(rows)==90
            region_success={name:statistics.mean(r['regions'][name]['marker_correct'] for r in rows) for name in ['process-A','process-B']}
            rd_groups.append({'kbps':kbps,'policy':mode,'n':len(rows),
                              **{key:statistics.mean(r[key] for r in rows) for key in ['bytes','encode_ms','weighted_roi_psnr_db','background_psnr_db']},
                              'marker_fraction':statistics.mean(sum(v['weight']*v['marker_correct'] for v in r['regions'].values())/10 for r in rows),
                              'region_success':region_success,'minimum_region_success':min(region_success.values())})
    return {'scope':'Engineering prototype; two independent runs per scene and condition, not a production validation',
            'checks':{'cells_verified':len(network)+len(live),'rd_rows':1080,'arithmetic':'independently recomputed time grid and byte denominator'},
            'source_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files},
            'network':groups,'rd':rd_groups,'live':[{k:v for k,v in c.items() if k not in ['records','selected','age_samples_ms']} for c in live],
            'network_sessions':len(network),'network_seconds':sum(c['duration_s'] for c in network),
            'clock_warning_sessions':sum(c['clock_quality_warning'] for c in network),
            'failed_sessions':sum(bool(c['failures']) for c in network)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--results',default='results');args=parser.parse_args()
    root=Path(args.results);result=summarize(root)
    (root/'analysis.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['checks','network_sessions','clock_warning_sessions','failed_sessions']}))
