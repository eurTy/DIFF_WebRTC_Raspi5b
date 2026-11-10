"""Read numerical client reports without opening or retaining a video stream."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from aiohttp import ClientSession, ClientTimeout


async def collect(url, seconds, output):
    samples = []
    started = time.monotonic()
    async with ClientSession(timeout=ClientTimeout(total=5)) as client:
        while time.monotonic()-started < seconds:
            row = {"at_utc":datetime.now(timezone.utc).isoformat(),"elapsed_s":time.monotonic()-started}
            try:
                async with client.get(url.rstrip('/')+'/api/status') as response:
                    response.raise_for_status()
                    row['status'] = await response.json()
            except Exception as error:
                row['error'] = str(error)
            samples.append(row)
            await asyncio.sleep(1)
    result = {"scope":"Numerical browser reports, not optical latency; codec/config stored when available. Repeated or stale reports are not independent samples.","samples":samples}
    target = Path(output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'polls':len(samples),'last':samples[-1] if samples else None},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--seconds',type=int,default=45)
    parser.add_argument('--output',default='runtime/mobile-phone-probe.json')
    args = parser.parse_args()
    asyncio.run(collect(args.url,args.seconds,args.output))
