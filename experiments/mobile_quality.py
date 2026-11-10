"""Paired synthetic codec quality, not a cellular or industrial validation."""
import argparse
import io
import json
from pathlib import Path
import statistics

import av
import cv2
import numpy as np

from experiment_v04.policy import TaskFreshnessEncoder
from experiment_v05.codec import H264Encoder
from semantic_video.model import DEFAULT_REGIONS
from semantic_video.protocol import unpack
from semantic_video.source import FixtureSource, mono_ms


def quality(original, decoded):
    mask = np.zeros((720,1280),dtype=bool)
    for r in DEFAULT_REGIONS:
        x,y,w,h = r.rect(1280,720)
        mask[y:y+h,x:x+w] = True
    difference = (original.astype(np.float32)-decoded.astype(np.float32))**2
    def psnr(values):
        mse = float(values.mean())
        return 99.0 if mse == 0 else float(10*np.log10(255**2/mse))
    return {'roi_psnr_db':psnr(difference[mask]),'background_psnr_db':psnr(difference[~mask])}


def run(count, kbps):
    source = FixtureSource(7)
    # Same 20 fps input sampling for all codecs; no camera media is retained.
    frames = [source.at(round(i*30/20),i*50) for i in range(count+1)]
    results = []
    for semantic in (False,True):
        encoder = H264Encoder(DEFAULT_REGIONS,kbps,20,semantic)
        encoded = bytearray(encoder.init)
        sizes,times = [],[]
        try:
            for f in frames:
                result = encoder.encode(f)
                if result:
                    meta,data = result
                    encoded.extend(data);sizes.append(len(data));times.append(meta['encode_ms'])
        finally:
            encoder.close()
        with av.open(io.BytesIO(encoded)) as stream:
            decoded = list(stream.decode(video=0))
        scores = [quality(f.image,d.to_ndarray(format='bgr24')) for f,d in zip(frames,decoded)]
        results.append({'codec':'h264-task' if semantic else 'h264-uniform',
                        'frames':len(decoded),'payload_kbps_at_20fps':sum(sizes)*8/(count/20)/1000,
                        'encode_mean_ms':statistics.mean(times),
                        **{key:statistics.mean(s[key] for s in scores) for key in scores[0]}})
    encoder = TaskFreshnessEncoder(DEFAULT_REGIONS,kbps,20)
    scores,sizes,times,patches = [],[],[],[]
    for f in frames[:-1]:
        data,meta = encoder.encode(f)
        _,images = unpack(data)
        output = np.zeros_like(f.image)
        for part,raw in zip(meta['parts'],images):
            image = cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
            x,y,w,h = part['rect'];output[y:y+h,x:x+w] = cv2.resize(image,(w,h))
        name = meta['parts'][1]['name'] if len(images)>1 else None
        # Fast ACK assumption keeps v0.4 at its configured budget. Not a 4G RTT simulation.
        encoder.visible(name,len(data),20,len(images[1]) if name else 0)
        scores.append(quality(f.image,output));sizes.append(len(data));times.append(meta['encode_ms']);patches.append(len(images)-1)
    results.append({'codec':'v04-jpeg-fast-ack-assumption','frames':count,
                    'payload_kbps_at_20fps':sum(sizes)*8/(count/20)/1000,
                    'encode_mean_ms':statistics.mean(times),'mean_regions_per_frame':statistics.mean(patches),
                    **{key:statistics.mean(s[key] for s in scores) for key in scores[0]}})
    return {'scope':'PC synthetic seed 7, paired source frames, configured budget equal but achieved rates differ; not cellular/optical/industrial evidence',
            'kbps':kbps,'fps':20,'results':results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--frames',type=int,default=90)
    parser.add_argument('--kbps',type=int,default=2400)
    parser.add_argument('--output',default='runtime/mobile-quality.json')
    args = parser.parse_args()
    result = run(args.frames,args.kbps)
    target = Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
