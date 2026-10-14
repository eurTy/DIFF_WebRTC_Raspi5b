"""A real TCP byte relay with application-layer pacing, not a Wi-Fi emulator."""
import asyncio
from contextlib import suppress
import time
import math


class RateRelay:
    def __init__(self, host, port, kbps):
        if kbps <= 0:
            raise ValueError('Positive link rate required')
        self.host,self.port,self.kbps=host,port,kbps
        self.tasks=set()
        self.bytes=0
        self.server=None
        self.profile=[]
        self.profile_started=0

    def set_profile(self, changes):
        if any(len(c)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in c) or c[0]<0 or c[1]<=0 for c in changes):
            raise ValueError('Finite nonnegative times and positive rates required')
        if any(a[0]>=b[0] for a,b in zip(changes,changes[1:])):
            raise ValueError('Profile times must increase')
        self.profile=list(changes)
        self.profile_started=time.perf_counter()

    def rate_at(self, elapsed):
        rate=self.kbps
        for at,value in self.profile:
            if elapsed<at:break
            rate=value
        return rate

    async def __aenter__(self):
        self.server=await asyncio.start_server(self.handle,'127.0.0.1',0,limit=8192)
        self.listen_port=self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self,*args):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.tasks): task.cancel()
        await asyncio.gather(*list(self.tasks),return_exceptions=True)

    async def pipe(self,reader,writer,shaped):
        deadline=time.perf_counter()
        while data:=await reader.read(2048):
            if shaped:
                rate=self.rate_at(time.perf_counter()-self.profile_started)
                deadline=max(deadline,time.perf_counter())+len(data)*8/(rate*1000)
                await asyncio.sleep(max(0,deadline-time.perf_counter()))
                self.bytes+=len(data)
            writer.write(data)
            await writer.drain()

    async def handle(self,reader,writer):
        task=asyncio.current_task();self.tasks.add(task)
        upstream=None;pumps=[]
        try:
            remote,upstream=await asyncio.wait_for(asyncio.open_connection(self.host,self.port,limit=8192),5)
            upstream.transport.set_write_buffer_limits(high=8192)
            writer.transport.set_write_buffer_limits(high=8192)
            pumps=[asyncio.create_task(self.pipe(reader,upstream,False)),asyncio.create_task(self.pipe(remote,writer,True))]
            await asyncio.wait(pumps,return_when=asyncio.FIRST_COMPLETED)
        except (OSError,asyncio.TimeoutError):
            pass
        finally:
            for pump in pumps: pump.cancel()
            await asyncio.gather(*pumps,return_exceptions=True)
            for connection in [upstream,writer]:
                if connection:
                    connection.close()
                    with suppress(OSError,asyncio.CancelledError): await connection.wait_closed()
            self.tasks.discard(task)
