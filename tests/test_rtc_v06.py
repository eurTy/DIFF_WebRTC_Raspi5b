import asyncio
from fractions import Fraction
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
from aiohttp import web, ClientSession, WSServerHandshakeError
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from aiortc.codecs.h264 import h264_depayload

from experiment_v06.media import QualityEncoder, LatestTrack, instrument_sender, read_latest
from experiment_v06.server import settings, application, private_answer
from aiortc.sdp import SessionDescription
from semantic_video.source import FixtureSource


class RtcMediaTests(unittest.TestCase):
    def test_native_resolution_immediate_decode_and_small_packets(self):
        encoder = QualityEncoder(2400, 20)
        decoder = av.CodecContext.create('h264', 'r')
        source = FixtureSource(7)
        for i in range(6):
            frame = av.VideoFrame.from_ndarray(source.at(i).image, format='bgr24')
            frame.pts, frame.time_base = 90000000+i*4500, Fraction(1,90000)
            payloads, timestamp = encoder.encode(frame, force_keyframe=i==3)
            self.assertEqual(timestamp, frame.pts)
            self.assertTrue(payloads)
            self.assertLessEqual(max(map(len,payloads)),1100)
            pictures = decoder.decode(av.Packet(b''.join(h264_depayload(p) for p in payloads)))
            self.assertEqual(len(pictures),1)
            self.assertEqual((pictures[0].width,pictures[0].height),(1280,720))
        self.assertEqual(encoder.codec.max_b_frames,0)

    def test_congestion_keeps_spatial_quality_settings(self):
        encoder = QualityEncoder(2400,20)
        old = encoder.target_bitrate
        encoder.target_bitrate = 1000000
        self.assertEqual(encoder.target_bitrate,old)
        self.assertLess(encoder.effective_fps,20)
        encoder.target_bitrate = 10000000
        self.assertEqual(encoder.effective_fps,20)

    def test_retry_bounded_and_stale_not_suppressed(self):
        class Source:
            calls=0
            def read(self):
                self.calls+=1
                raise ValueError('Input overwritten during read')
        source=Source()
        with self.assertRaises(ValueError): read_latest(source)
        self.assertEqual(source.calls,3)

    def test_settings_reject_boolean_and_audio(self):
        with self.assertRaises(ValueError): settings({'kbps':True,'sdp':'m=video 9'})
        with self.assertRaises(ValueError): settings({'sdp':'m=video 9\nm=audio 9'})
        self.assertEqual(settings({'sdp':'m=video 9'}),(2400,20))

    def test_private_answer_excludes_docker_and_unavailable_interfaces(self):
        sdp=('v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n'
             'm=video 40000 UDP/TLS/RTP/SAVPF 99\r\nc=IN IP4 172.22.0.2\r\n'
             'a=rtpmap:99 H264/90000\r\n'
             'a=candidate:1 1 udp 123 172.22.0.2 40000 typ host\r\n'
             'a=candidate:2 1 udp 122 100.91.218.90 40001 typ host\r\n')
        answer=SessionDescription.parse(private_answer(sdp,'100.91.218.90'))
        self.assertEqual(answer.media[0].host,'100.91.218.90')
        self.assertEqual(answer.media[0].port,40001)
        self.assertEqual([c.ip for c in answer.media[0].ice_candidates],['100.91.218.90'])
        with self.assertRaises(ValueError): private_answer(sdp,'100.99.99.99')


class RtcAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_rtp_mapping_uses_completed_send_not_next_capture(self):
        sender=SimpleNamespace(_RTCRtpSender__encoder=None,
            _RTCRtpSender__rtp_timestamp=0,_RTCRtpSender__packet_count=0)
        track=SimpleNamespace(latest_meta={'id':1,'captured_ms':100})
        async def original(codec): return SimpleNamespace(payloads=[b'x'])
        sender._next_encoded_frame=original
        emitted=[]
        instrument_sender(sender,track,QualityEncoder(2400,20),emitted.append)
        await sender._next_encoded_frame(None)
        self.assertEqual(emitted,[])
        sender._RTCRtpSender__rtp_timestamp=4294967295
        sender._RTCRtpSender__packet_count=7
        track.latest_meta={'id':2,'captured_ms':150}
        await sender._next_encoded_frame(None)
        self.assertEqual(emitted[0]['rtp'],4294967295)
        self.assertEqual(emitted[0]['id'],1)
        self.assertEqual(emitted[0]['captured_ms'],100)
        sender._RTCRtpSender__rtp_timestamp=4499
        sender._RTCRtpSender__packet_count=9
        await sender._next_encoded_frame(None)
        self.assertEqual(emitted[1]['rtp'],4499)
        self.assertEqual(emitted[1]['id'],2)

    async def test_pinned_sender_adapter_and_latest_track(self):
        pc=RTCPeerConnection(RTCConfiguration(iceServers=[]))
        try:
            encoder=QualityEncoder(2400,20)
            track=LatestTrack(FixtureSource(),encoder)
            sender=pc.addTrack(track)
            instrument_sender(sender,track,encoder,lambda _:None)
            first=await track.recv()
            await asyncio.sleep(.2)
            second=await track.recv()
            self.assertGreater(second.pts-first.pts,15000)
            self.assertEqual((second.width,second.height),(1280,720))
            track.stop()
        finally:
            await pc.close()


class RtcServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Keep unit-test ICE checks on loopback, independent of VPN/adapters.
        self.addresses=patch('aioice.ice.get_host_addresses',return_value=['127.0.0.1'])
        self.addresses.start()
        self.runner=web.AppRunner(application())
        await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0)
        await site.start()
        self.url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        self.client=ClientSession()

    async def asyncTearDown(self):
        try:
            await self.client.close()
            await asyncio.wait_for(self.runner.cleanup(), 5)
        finally:
            self.addresses.stop()

    async def test_vp8_first_offer_gets_decodable_h264_and_bursty_feedback_is_safe(self):
        pc=RTCPeerConnection(RTCConfiguration(iceServers=[]))
        pc.addTransceiver('video',direction='recvonly')
        pc.createDataChannel('timing',ordered=False,maxRetransmits=0)
        ready=asyncio.get_running_loop().create_future()
        @pc.on('track')
        def track(value): ready.set_result(value)
        try:
            await pc.setLocalDescription(await pc.createOffer())
            async with self.client.ws_connect(self.url+'/signal') as ws:
                await ws.send_json({'type':'offer','sdp':pc.localDescription.sdp})
                answer=await ws.receive_json(timeout=10)
                self.assertIn('H264/90000',answer['sdp'])
                self.assertNotIn('VP8/90000',answer['sdp'])
                await pc.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type='answer'))
                remote=await asyncio.wait_for(ready,3)
                frame=await asyncio.wait_for(remote.recv(),6)
                self.assertEqual((frame.width,frame.height),(1280,720))
                await ws.send_json({'type':'candidate','candidate':{'candidate':'','sdpMid':'0','sdpMLineIndex':0}})
                await ws.send_json({'type':'display','fps':20})
                await ws.send_json({'type':'display','fps':20})
                await asyncio.sleep(.1)
                async with self.client.get(self.url+'/api/status') as response:
                    state=await response.json()
                    self.assertEqual(state['sessions'],1)
                    self.assertEqual(state['client_samples'],1)
                    self.assertTrue(state['remote_candidates']['complete'])
        finally:
            await pc.close()

    async def test_session_limit_release_and_cross_origin(self):
        async with self.client.get(self.url+'/signal',headers={'Origin':'http://untrusted.invalid'}) as response:
            self.assertEqual(response.status,403)
        async with self.client.ws_connect(self.url+'/signal') as ws:
            with self.assertRaises(WSServerHandshakeError) as caught:
                await self.client.ws_connect(self.url+'/signal')
            self.assertEqual(caught.exception.status,503)
            await ws.send_json({'type':'offer','sdp':'invalid','kbps':True})
            await ws.receive()
        await asyncio.sleep(.1)
        async with self.client.get(self.url+'/api/status') as response:
            self.assertEqual((await response.json())['sessions'],0)

    async def test_startup_failure_is_not_hidden_by_a_quick_retry(self):
        for value in ({'event':'stage','stage':'ice-config'},
                      {'event':'failure','stage':'local-description','message':'test ICE error','relay':0},
                      {'event':'stage','stage':'ice-config'}):
            async with self.client.post(self.url+'/api/client',json=value) as response:
                self.assertEqual(response.status,200)
        async with self.client.get(self.url+'/api/status') as response:
            state=await response.json()
            self.assertEqual(state['last_client_failure']['message'],'test ICE error')
            self.assertEqual(state['last_client_failure']['stage'],'local-description')
            self.assertEqual(state['client_diagnostic']['stage'],'ice-config')


if __name__=='__main__': unittest.main()
