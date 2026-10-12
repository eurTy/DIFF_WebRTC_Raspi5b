"""Independently check browser stability traces without retaining camera images."""
import json
import math
from pathlib import Path


def summarize(document):
    samples=document['samples']
    frames=document['export']['frames']
    valid=[r['latency_ms'] for r in frames if r['latency_ms'] is not None]
    assert all(math.isfinite(value) and value>=0 for value in valid)
    assert all(b['elapsed_s']>=a['elapsed_s'] for a,b in zip(samples,samples[1:]))
    valid.sort()
    percentile=lambda q: valid[min(len(valid)-1,math.floor((len(valid)-1)*q))] if valid else None
    fresh=sum(s['age_ms'] is not None and s['age_ms']<=250 and s['stale']!='true' for s in samples)/len(samples)
    assert abs(fresh-document['summary']['fresh_250_fraction'])<1e-12
    assert percentile(.95)==document['summary']['p95_ms']
    result={**document['summary'],'observed_display_fps':(samples[-1]['frames']-samples[0]['frames'])/(samples[-1]['elapsed_s']-samples[0]['elapsed_s']),
            'reconnects':document['export'].get('reconnects'), 'discarded':document['export'].get('discarded'),
            'recovery_ms':None,'outage_observation_s':None}
    restored=next((e['elapsed_s'] for e in document['events'] if e['type']=='stream_restored'),None)
    if restored is not None:
        previous=max(s['frames'] for s in samples if s['elapsed_s']<restored)
        resumed=next((s['elapsed_s'] for s in samples if s['elapsed_s']>=restored and s['frames']>previous),None)
        result['recovery_ms']=(resumed-restored)*1000 if resumed is not None else None
        result['outage_observation_s']=samples[-1]['elapsed_s']-restored
    return result


def main():
    root=Path('results/stability-20261013')
    result={'scope':'Browser numeric traces; approximate 100 ms sampling, not glass-to-glass; displayed FPS excludes initial frames',
            'runs':{}}
    for path in sorted(root.glob('*.json')):
        data=json.loads(path.read_text(encoding='utf-8'))
        if 'samples' in data and 'export' in data:
            result['runs'][path.name]=summarize(data)
    (root/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
