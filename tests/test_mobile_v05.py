import asyncio
import io
import json
import struct
import unittest

try:
    import av
except ImportError:
    raise unittest.SkipTest('Optional mobile experiment requires av==16.1.0')
import cv2
import numpy as np
from aiohttp import ClientSession, WSMsgType, web

from experiment_v05.codec import H264Encoder, JpegEncoder, task_image
from experiment_v05.flow import Flow, valid_ack
from experiment_v05.server import application, read_latest
from semantic_video.model import DEFAULT_REGIONS
from semantic_video.source import FixtureSource


def packet(data):
    size = struct.unpack_from('!I',data,4)[0]
    return json.loads(data[8:8+size]),data[8+size:]


class MobileCodec(unittest.TestCase):
    def test_constant_cellular_rtt_does_not_collapse_quality(self):
        flow = Flow(2400,20)
        for i in range(100):
            flow.observe(150,i*50)
        self.assertEqual(flow.kbps,2400)
        self.assertEqual(flow.window,5)
        flow.observe(400,5100)
        self.assertLess(flow.kbps,2400)
        self.assertEqual(flow.window,2)
        for i in range(200):
            flow.observe(150,5200+i*50)
        self.assertEqual(flow.kbps,2400)
        self.assertGreaterEqual(flow.interval(60000),200)

    def test_task_filter_preserves_regions_same_frame_and_source(self):
        frame = FixtureSource(7).at(3)
        before = frame.image.copy()
        result = task_image(frame.image,DEFAULT_REGIONS,600,True)
        self.assertTrue(np.array_equal(before,frame.image))
        for r in DEFAULT_REGIONS:
            x,y,w,h = r.rect(1280,720)
            self.assertTrue(np.array_equal(before[y:y+h,x:x+w],result[y:y+h,x:x+w]))
        self.assertFalse(np.array_equal(before,result))

    def test_byte_credit_is_bounded_and_preserves_p_frame_cadence(self):
        flow = Flow(2400,20)
        self.assertEqual(flow.next_send(1000,1010,30000),1050)
        self.assertEqual(flow.next_send(1050,1060,2000),1100)
        self.assertAlmostEqual(flow.next_send(1100,1110,100000),1350)

    def test_h264_fragment_timestamp_and_native_resolution(self):
        encoder = H264Encoder(DEFAULT_REGIONS,2400,20)
        source = FixtureSource(7)
        stream = bytearray(encoder.init)
        self.assertIn('avc1.',encoder.mime)
        try:
            for i in range(8):
                result = encoder.encode(source.at(i,100+i*50))
                if i == 0:
                    self.assertIsNone(result)
                else:
                    meta,data = result
                    self.assertEqual((meta['id'],meta['captured_ms']),(i-1,100+(i-1)*50))
                    self.assertEqual(meta['media_time'],(i-1)/20)
                    stream.extend(data)
        finally:
            encoder.close()
        with av.open(io.BytesIO(stream)) as decoded:
            frames = list(decoded.decode(video=0))
        self.assertEqual(len(frames),7)
        self.assertTrue(all((f.width,f.height)==(1280,720) for f in frames))

    def test_jpeg_always_contains_every_same_frame_region(self):
        encoder = JpegEncoder(DEFAULT_REGIONS,384,20)
        meta,data = encoder.encode(FixtureSource().at(1))
        self.assertEqual([p['name'] for p in meta['parts']],['base','process-A','process-B'])
        self.assertEqual(sum(meta['lengths']),len(data))
        base = cv2.imdecode(np.frombuffer(data[:meta['lengths'][0]],np.uint8),cv2.IMREAD_COLOR)
        self.assertGreaterEqual(base.shape[1],640)

    def test_ack_validation_and_input_retry(self):
        self.assertFalse(valid_ack({'type':'ack','seq':True},[(1,0)]))
        self.assertFalse(valid_ack({'type':'ack','seq':2},[(1,0)]))
        self.assertTrue(valid_ack({'type':'ack','seq':1},[(1,0)]))
        class Source:
            calls = 0
            def read(self):
                self.calls += 1
                if self.calls < 3:
                    raise ValueError('Input overwritten during read')
                return 'frame'
        self.assertEqual(read_latest(Source()),('frame',2))


class MobileServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runner = web.AppRunner(application())
        await self.runner.setup()
        site = web.TCPSite(self.runner,'127.0.0.1',0)
        await site.start()
        self.url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_pipelined_bounded_frames_without_ack_then_recovers(self):
        async with self.client.ws_connect(self.url+'/stream') as ws:
            await ws.send_json({'type':'start','codec':'jpeg'})
            await ws.receive_json()
            metas = []
            for _ in range(4):
                value = await asyncio.wait_for(ws.receive(),2)
                self.assertEqual(value.type,WSMsgType.BINARY)
                metas.append(packet(value.data)[0])
            self.assertEqual([m['seq'] for m in metas],list(range(4)))
            # Slot five is unavailable until the consumer acknowledges.
            receive = asyncio.create_task(ws.receive())
            await asyncio.sleep(.15)
            self.assertFalse(receive.done())
            for meta in metas:
                await ws.send_json({'type':'ack','seq':meta['seq']})
            value = await asyncio.wait_for(receive,2)
            self.assertEqual(packet(value.data)[0]['seq'],4)

    async def test_h264_init_and_slow_consumer_timeout(self):
        async with self.client.ws_connect(self.url+'/stream') as ws:
            await ws.send_json({'type':'start','codec':'h264'})
            ready = await ws.receive_json()
            self.assertIn('avc1.',ready['mime'])
            self.assertEqual(ready['implementation']['app_version'],'v0.5')
            self.assertEqual(len(ready['implementation']['source_sha256']),64)
            self.assertEqual(ready['config']['codec'],'h264')
            self.assertEqual(packet((await ws.receive()).data)[0]['kind'],'init')
            count = 0
            while True:
                value = await asyncio.wait_for(ws.receive(),3)
                if value.type == WSMsgType.CLOSE:
                    self.assertEqual(value.data,1013)
                    break
                count += 1
                meta = packet(value.data)[0]
                self.assertGreaterEqual(meta['server_sent_ms'],meta['captured_ms'])
            self.assertEqual(count,4)

    async def test_invalid_ack_and_cross_origin(self):
        async with self.client.get(self.url+'/stream',headers={'Origin':'http://untrusted.invalid'}) as response:
            self.assertEqual(response.status,403)
        async with self.client.ws_connect(self.url+'/stream') as ws:
            await ws.send_json({'type':'start','codec':'jpeg'})
            await ws.receive_json()
            await ws.receive()
            await ws.send_json({'type':'ack','seq':999})
            response = await asyncio.wait_for(ws.receive(),2)
            self.assertEqual((response.type,response.data),(WSMsgType.CLOSE,1008))

    async def test_display_reports_do_not_release_transport_credit(self):
        async with self.client.ws_connect(self.url+'/stream') as ws:
            await ws.send_json({'type':'start','codec':'jpeg'})
            await ws.receive_json()
            for _ in range(4):
                await asyncio.wait_for(ws.receive(),2)
            await ws.send_json({'type':'display','age_ms':85,'latency_ms':60,'fps':15,'buffer_ms':0})
            await asyncio.sleep(.05)
            async with self.client.get(self.url+'/api/status') as response:
                status = await response.json()
                self.assertEqual(status['client_report']['age_ms'],85)
                self.assertEqual(status['client_samples'],1)
                self.assertEqual(status['events']['sent_frames'],4)


if __name__ == '__main__':
    unittest.main()
