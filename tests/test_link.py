import asyncio
import time
import unittest
from experiments.link import RateRelay


class Relay(unittest.IsolatedAsyncioTestCase):
    async def test_payload_integrity_rate_and_cleanup(self):
        async def send(reader,writer):
            await reader.readexactly(1)
            writer.write(b'x'*8192)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        upstream=await asyncio.start_server(send,'127.0.0.1',0)
        try:
            async with RateRelay('127.0.0.1',upstream.sockets[0].getsockname()[1],256) as relay:
                reader,writer=await asyncio.open_connection('127.0.0.1',relay.listen_port)
                started=time.perf_counter();writer.write(b'?');await writer.drain()
                self.assertEqual(await reader.readexactly(8192),b'x'*8192)
                self.assertGreaterEqual(time.perf_counter()-started,.24)
                self.assertEqual(relay.bytes,8192)
                writer.close();await writer.wait_closed()
            self.assertFalse(relay.tasks)
        finally:
            upstream.close();await upstream.wait_closed()


if __name__=='__main__': unittest.main()
