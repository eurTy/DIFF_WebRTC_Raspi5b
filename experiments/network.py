"""Real TCP relay experiment; all numerical outputs retain source and scope labels."""
import argparse
import asyncio
import json
from pathlib import Path
import platform
import random
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType
import cv2
import numpy as np

from experiments.link import RateRelay
from experiments.provenance import source_hashes
from semantic_video.encoder import MODES
from semantic_video.metrics import evaluate
from semantic_video.protocol import reconstruct, unpack
from semantic_video.source import FixtureSource, mono_ms


async def calibrate(client,url):
    samples=[]
    async with client.ws_connect(url+'/clock',compress=0) as ws:
        for index in range(9):
            start=mono_ms()
            await ws.send_json({'type':'clock','id':index})
            message=await asyncio.wait_for(ws.receive_json(),3)
            end=mono_ms()
            rtt=end-start-(message['sent_ms']-message['received_ms'])
            if rtt>=0:
                samples.append({'rtt_ms':rtt,'offset_ms':((message['received_ms']-start)+(message['sent_ms']-end))/2})
            await asyncio.sleep(.03)
    if len(samples)<3: raise ValueError('Insufficient clock samples')
    return min(samples,key=lambda sample:sample['rtt_ms'])


async def cell(url,policy,kbps,seed,duration,warmup):
    parsed=urlsplit(url)
    records=[];raw=[];failures=[]
    async with ClientSession(timeout=ClientTimeout(total=None,sock_connect=5,sock_read=10)) as client:
        clock=await calibrate(client,url)
        async with RateRelay(parsed.hostname,parsed.port or 80,kbps) as relay:
            async with client.ws_connect(f'http://127.0.0.1:{relay.listen_port}/stream',compress=0,max_msg_size=4194304) as ws:
                await ws.send_json({'type':'start','mode':policy,'kbps':kbps,'fps':10,'seed':seed})
                ready=await asyncio.wait_for(ws.receive_json(),5)
                started=mono_ms();deadline=started+(duration+warmup)*1000
                while mono_ms()<deadline:
                    try:
                        message=await asyncio.wait_for(ws.receive(),max(.01,(deadline-mono_ms())/1000))
                        if message.type!=WSMsgType.BINARY:
                            failures.append({'type':str(message.type),'elapsed_ms':mono_ms()-started})
                            break
                        meta,images=unpack(message.data)
                        decoded=await asyncio.to_thread(reconstruct,meta,images)
                        shown=mono_ms()
                        await ws.send_json({'type':'ack','id':meta['id']})
                        latency=shown+clock['offset_ms']-meta['captured_ms']
                        record={'elapsed_ms':shown-started,'latency_ms':latency,'id':meta['id'],'bytes':len(message.data),
                                'encode_ms':meta['encode_ms'],'level':meta['level'],'budget_exceeded':meta['budget_exceeded'],
                                'parts':[p['name'] for p in meta['parts']]}
                        records.append(record)
                        # JPEGs are retained only in memory until synthetic fidelity scoring.
                        if ready['source']=='SYNTHETIC_TASK_FIXTURE': raw.append((record,meta,images))
                    except asyncio.TimeoutError:
                        if mono_ms()<deadline-50: failures.append({'type':'timeout','elapsed_ms':mono_ms()-started})
                        break
                wire_bytes=relay.bytes
        clock_end=await calibrate(client,url)
    clock_error=clock['rtt_ms']/2+abs(clock_end['offset_ms']-clock['offset_ms'])
    if ready['source']=='SYNTHETIC_TASK_FIXTURE':
        fixture=FixtureSource(seed)
        for record,meta,images in raw:
            quality=evaluate(fixture.at(meta['id']).image,reconstruct(meta,images))
            record['quality']=quality
            record['weighted_marker_success']=sum(r['weight']*r['marker_correct'] for r in quality['regions'].values())/10
    selected=[r for r in records if warmup*1000<=r['elapsed_ms']<(warmup+duration)*1000]
    ages=[];fresh=[];task=[];conservative=[];j=-1
    for timestamp in np.arange(warmup*1000,(warmup+duration)*1000,100):
        while j+1<len(records) and records[j+1]['elapsed_ms']<=timestamp: j+=1
        age=records[j]['latency_ms']+timestamp-records[j]['elapsed_ms'] if j>=0 else None
        ages.append(age)
        fresh.append(bool(age is not None and 0<=age<=250))
        conservative.append(bool(age is not None and age>=0 and age+clock_error<=250))
        task.append((records[j].get('weighted_marker_success',0) if j>=0 else 0)*fresh[-1])
    latencies=[r['latency_ms'] for r in selected]
    return {'policy':policy,'kbps':kbps,'seed':seed,'source':ready['source'],'duration_s':duration,'warmup_s':warmup,
            'clock':clock,'clock_after':clock_end,'clock_offset_shift_ms':clock_end['offset_ms']-clock['offset_ms'],
            'clock_diagnostic_envelope_ms':clock_error,'clock_quality_warning':clock['rtt_ms']>20 or abs(clock_end['offset_ms']-clock['offset_ms'])>20,
            'frames':len(selected),'fps':len(selected)/duration,'payload_mbps':sum(r['bytes'] for r in selected)*8/duration/1e6,
            'latency_p50_ms':float(np.median(latencies)) if latencies else None,
            'latency_p95_ms':float(np.percentile(latencies,95)) if latencies else None,
            'fresh_time_fraction_250ms':float(np.mean(fresh)),
            'conservative_fresh_time_fraction_250ms':float(np.mean(conservative)),
            'fresh_weighted_task_fraction':float(np.mean(task)) if raw else None,
            'mean_encode_ms':float(np.mean([r['encode_ms'] for r in selected])) if selected else None,
            'all_wire_bytes_including_upgrade':wire_bytes,'failures':failures,'records':records,
            'age_samples_ms':ages,'fresh_time_samples':len(fresh)}


async def run(args):
    hashes=source_hashes()
    cells=[(rate,mode,rep,7+rep) for rep in range(args.repeats) for rate in args.rates for mode in MODES]
    random.Random(args.schedule_seed).shuffle(cells)
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    results=[];setup_failures=[]
    if args.resume and destination.exists():
        previous=json.loads(destination.read_text(encoding='utf-8'))
        if previous['server_url']!=args.url or previous['schedule_seed']!=args.schedule_seed or previous['intended_cells']!=len(cells):
            raise ValueError('Resume parameters do not match the saved experiment')
        results=previous['cells'];setup_failures=previous.get('setup_failures',[])
        for item in results:
            if item['duration_s']!=args.duration or item['warmup_s']!=args.warmup:
                raise ValueError('Resume time window differs')
            item.setdefault('receiver_source_sha256',previous.get('receiver_source_sha256',{}))
    completed={(c['kbps'],c['policy'],c['repeat'],c['seed']) for c in results}
    for rate,mode,rep,seed in cells:
        if (rate,mode,rep,seed) in completed: continue
        for attempt in range(3):
            try:
                result=await cell(args.url.rstrip('/'),mode,rate,seed,args.duration,args.warmup)
                break
            except (asyncio.TimeoutError,OSError,ValueError,ClientError) as error:
                failure={'kbps':rate,'policy':mode,'repeat':rep,'seed':seed,'attempt':attempt+1,'error':type(error).__name__,
                         'stage':'session or calibration incomplete; not a valid measurement','wall_time':time.time()}
                setup_failures.append(failure);print(json.dumps(failure),flush=True)
                checkpoint={'experiment':'REAL_TCP_APPLICATION_PACED_RELAY','server_url':args.url,'schedule_seed':args.schedule_seed,
                            'intended_cells':len(cells),'complete':False,'cells':results,'setup_failures':setup_failures,'receiver_source_sha256':hashes,'source_hash_snapshot':'run_start'}
                destination.write_text(json.dumps(checkpoint,indent=2),encoding='utf-8')
                if attempt==2: raise
                await asyncio.sleep(2)
        result['receiver_source_sha256']=hashes
        result['repeat']=rep;results.append(result)
        document={'experiment':'REAL_TCP_APPLICATION_PACED_RELAY','server_url':args.url,'receiver_platform':platform.platform(),'receiver_source_sha256':hashes,'source_hash_snapshot':'run_start',
                  'scope':'Synthetic fixture unless explicitly marked live; TCP byte pacing is not RF loss emulation. Clock uses separate unshaped connection.',
                  'latency_endpoint':'Python JPEG decode completion, not browser scanout','schedule_seed':args.schedule_seed,
                  'age_sampling_ms':100,'intended_cells':len(cells),'complete':len(results)==len(cells),'cells':results,'setup_failures':setup_failures}
        destination.write_text(json.dumps(document,indent=2),encoding='utf-8')
        print(json.dumps({k:result[k] for k in ['policy','kbps','seed','frames','latency_p50_ms','fresh_time_fraction_250ms','fresh_weighted_task_fraction','failures']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8088');parser.add_argument('--rates',nargs='+',type=int,default=[512,1024,2048]);parser.add_argument('--repeats',type=int,default=2);parser.add_argument('--duration',type=float,default=10);parser.add_argument('--warmup',type=float,default=3);parser.add_argument('--schedule-seed',type=int,default=20261012);parser.add_argument('--resume',action='store_true');parser.add_argument('--output',default='results/network.json');args=parser.parse_args()
    cv2.setNumThreads(1);asyncio.run(run(args))
