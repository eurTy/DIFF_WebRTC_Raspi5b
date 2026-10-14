import asyncio
import unittest
from unittest.mock import patch
from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError, web
from semantic_video.protocol import unpack
from semantic_video.server import application


class Server(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runner=web.AppRunner(application())
        await self.runner.setup()
        self.site=web.TCPSite(self.runner,'127.0.0.1',0)
        await self.site.start()
        self.url=f'http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}'
        self.client=ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_stream_clock_and_status(self):
        async with self.client.get(self.url+'/api/status') as response:
            self.assertEqual((await response.json())['control'],'NO_DEVICE_COMMANDS')
        async with self.client.ws_connect(self.url+'/clock') as clock:
            await clock.send_json({'type':'clock','id':1})
            tick=await clock.receive_json()
            self.assertGreaterEqual(tick['sent_ms'],tick['received_ms'])
        async with self.client.ws_connect(self.url+'/stream') as stream:
            await stream.send_json({'type':'start'})
            self.assertEqual((await stream.receive_json())['type'],'ready')
            for _ in range(3):
                incoming=await asyncio.wait_for(stream.receive(),2)
                meta,_=unpack(incoming.data)
                await stream.send_json({'type':'ack','id':meta['id']})

    async def test_wrong_ack_closes(self):
        async with self.client.ws_connect(self.url+'/stream') as stream:
            await stream.send_json({'type':'start'})
            await stream.receive_json()
            await stream.receive()
            await stream.send_json({'type':'ack','id':False})
            message=await stream.receive()
            self.assertEqual(message.type,WSMsgType.CLOSE)
            self.assertEqual(message.data,1008)

    async def test_origin_and_limits(self):
        with self.assertRaises(WSServerHandshakeError) as error:
            await self.client.ws_connect(self.url+'/stream',origin='https://unrelated.invalid')
        self.assertEqual(error.exception.status,403)
        sockets=[await self.client.ws_connect(self.url+'/stream') for _ in range(2)]
        try:
            with self.assertRaises(WSServerHandshakeError) as error:
                await self.client.ws_connect(self.url+'/stream')
            self.assertEqual(error.exception.status,503)
        finally:
            for socket in sockets: await socket.close()

    async def test_invalid_config_closes(self):
        for settings in [{'fps':True},{'kbps':float('nan')},{'regions':[]},{'mode':'invalid'}]:
            async with self.client.ws_connect(self.url+'/stream') as stream:
                await stream.send_json({'type':'start',**settings})
                self.assertEqual((await stream.receive()).type,WSMsgType.CLOSE)

    async def test_disconnect_while_low_rate_pacing_releases_slot(self):
        stream=await self.client.ws_connect(self.url+'/stream')
        await stream.send_json({'type':'start','mode':'full','kbps':128,'fps':2})
        await stream.receive_json()
        meta,_=unpack((await stream.receive()).data)
        await stream.send_json({'type':'ack','id':meta['id']})
        await asyncio.sleep(.03)
        await asyncio.wait_for(stream.close(),.5)
        await asyncio.sleep(.03)
        async with self.client.get(self.url+'/api/status') as response:
            self.assertEqual((await response.json())['sessions'],0)

    async def test_realtime_two_frames_bounded_and_ordered(self):
        async with self.client.ws_connect(self.url+'/stream') as stream:
            await stream.send_json({'type':'start','feedback':'realtime'})
            self.assertEqual((await stream.receive_json())['window'], 1)
            first,_=unpack((await stream.receive()).data)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(stream.receive(),.12)
            await stream.send_json({'type':'ack','id':first['id']})
            # Eight fast confirmations at full budget may open a second slot.
            for _ in range(40):
                meta,_=unpack((await stream.receive()).data)
                await stream.send_json({'type':'ack','id':meta['id']})
                if meta['send_window']==2:
                    break
            self.assertEqual(meta['send_window'],2)
            first,_=unpack((await stream.receive()).data)
            second,_=unpack((await stream.receive()).data)
            self.assertGreater(second['id'],first['id'])
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(stream.receive(),.28)
            await stream.send_json({'type':'ack','id':first['id'],'dropped':True})
            # A late ACK shrinks the window; the second frame must drain first.
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(stream.receive(),.05)
            await stream.send_json({'type':'ack','id':second['id']})
            third,_=unpack((await stream.receive()).data)
            self.assertGreater(third['id'],second['id'])
            await stream.send_json({'type':'ack','id':first['id']})
            closed=await stream.receive()
            self.assertEqual((closed.type,closed.data),(WSMsgType.CLOSE,1008))

    async def test_realtime_timeout_is_retryable(self):
        with patch('semantic_video.server.DELIVERY_TIMEOUT',.1):
            async with self.client.ws_connect(self.url+'/stream') as stream:
                await stream.send_json({'type':'start','feedback':'realtime'})
                await stream.receive_json()
                self.assertEqual((await stream.receive()).type,WSMsgType.BINARY)
                closed=await asyncio.wait_for(stream.receive(),2)
                self.assertEqual((closed.type,closed.data),(WSMsgType.CLOSE,1013))

    async def test_realtime_rejects_non_boolean_discard(self):
        async with self.client.ws_connect(self.url+'/stream') as stream:
            await stream.send_json({'type':'start','feedback':'realtime'})
            await stream.receive_json()
            meta,_=unpack((await stream.receive()).data)
            await stream.send_json({'type':'ack','id':meta['id'],'dropped':'false'})
            closed=await stream.receive()
            self.assertEqual((closed.type,closed.data),(WSMsgType.CLOSE,1008))

    async def test_unresponsive_peer_does_not_hold_slot(self):
        stream=await self.client.ws_connect(self.url+'/stream')
        await stream.send_json({'type':'start','feedback':'realtime'})
        await stream.receive_json()
        # Deliberately do not read or answer the WebSocket closing handshake.
        await asyncio.sleep(3.5)
        async with self.client.get(self.url+'/api/status') as response:
            self.assertEqual((await response.json())['sessions'],0)
        await stream.close()


if __name__=='__main__': unittest.main()
