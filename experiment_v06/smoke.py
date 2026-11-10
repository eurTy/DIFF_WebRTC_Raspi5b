"""Bounded RTP decode check. Keeps numbers only; skips an occupied viewer."""
import argparse
import asyncio
import json
import time

from aiohttp import ClientSession
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, RTCIceServer
from aioice.ice import TransportPolicy


async def run(url, relay=None):
    async with ClientSession() as client:
        async with client.get(url+'/api/status') as response:
            status=await response.json()
        if status['sessions']:
            print(json.dumps({'skipped':'Viewer occupied; did not interrupt'}))
            return
        servers=[]
        if relay:
            async with client.get(url+'/api/ice') as response:
                config=await response.json()
            for server in config['iceServers']:
                server['urls']=[u for u in server['urls'] if u.endswith('transport='+relay)]
                servers.append(RTCIceServer(**server))
            if not servers: raise ValueError('TURN not configured')
        pc=RTCPeerConnection(RTCConfiguration(iceServers=servers))
        pc.addTransceiver('video',direction='recvonly')
        dc=pc.createDataChannel('timing',ordered=False,maxRetransmits=0)
        if relay:
            gatherers=[t.sender.transport.transport.iceGatherer for t in pc.getTransceivers()]
            gatherers.append(pc.sctp.transport.transport.iceGatherer)
            for gatherer in gatherers:
                gatherer._connection._transport_policy=TransportPolicy.RELAY
                original=gatherer._connection.get_component_candidates
                async def relay_candidates(component, addresses, original=original):
                    # aioice's RELAY policy hides host candidates but retains
                    # their sockets. A probe must prevent host-path bypass.
                    return await original(component, [])
                gatherer._connection.get_component_candidates=relay_candidates
        frames=[]
        metadata=[]
        done=asyncio.Event()
        consumer=None
        @dc.on('message')
        def on_message(message):
            data=json.loads(message)
            metadata.append({k:data[k] for k in ('rtp','id','encode_ms')})
        @pc.on('track')
        def on_track(track):
            nonlocal consumer
            async def receive():
                while len(frames)<35:
                    frame=await track.recv()
                    frames.append({'at':time.monotonic(),'width':frame.width,'height':frame.height})
                done.set()
            consumer=asyncio.create_task(receive())
        try:
            await pc.setLocalDescription(await pc.createOffer())
            if relay:
                assert ' typ relay' in pc.localDescription.sdp, 'TURN allocation failed'
            async with client.ws_connect(url+'/signal') as ws:
                await ws.send_json({'type':'offer','sdp':pc.localDescription.sdp,'kbps':2400,'fps':20})
                answer=await ws.receive_json(timeout=12)
                await pc.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type='answer'))
                await asyncio.wait_for(done.wait(),20)
                connection=pc.getTransceivers()[0].receiver.transport.transport._connection
                selected=next(iter(connection._nominated.values()))
                result={'frames':len(frames),'metadata_frames':len(metadata),'connection':pc.connectionState,
                        'resolution':[frames[-1]['width'],frames[-1]['height']],
                        'decode_fps':(len(frames)-1)/(frames[-1]['at']-frames[0]['at']),
                        'encode_ms_last':metadata[-1]['encode_ms'] if metadata else None,
                        'scope':'Same-host RTP decode, not phone latency', 'turn_test':relay,
                        'selected_local_type':selected.local_candidate.type,
                        'selected_remote_type':selected.remote_candidate.type}
                print(json.dumps(result))
                assert result['resolution']==[1280,720] and metadata
                if relay: assert result['selected_local_type']=='relay'
        finally:
            if consumer:
                consumer.cancel()
                await asyncio.gather(consumer,return_exceptions=True)
            await pc.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8091')
    parser.add_argument('--relay',choices=('tcp','udp'))
    args=parser.parse_args()
    asyncio.run(run(args.url,args.relay))
