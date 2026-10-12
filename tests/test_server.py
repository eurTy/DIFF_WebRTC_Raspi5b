import asyncio
import unittest
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


if __name__=='__main__': unittest.main()
