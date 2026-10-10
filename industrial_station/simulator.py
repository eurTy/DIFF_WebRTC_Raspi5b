"""Modbus TCP simulator using PyModbus; no hardware I/O."""
import argparse
import asyncio
import contextlib
import json
import signal

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from .model import Station
from .protocol import COMMAND_ADDRESS, COMMAND_COUNT, DEVICE_ID, Fault, KEEPALIVE_ADDRESS, KEEPALIVE_COUNT, STATUS_COUNT


def device(model):
    async def access(function, start, address, count, registers, values):
        model.tick()
        if model.fault == Fault.COMM_SILENT:
            return ExcCodes.DEVICE_BUSY
        if values is None:
            if function != 3:
                return ExcCodes.ILLEGAL_FUNCTION
            if address < 0 or address + count > STATUS_COUNT:
                return ExcCodes.ILLEGAL_ADDRESS
            registers[-start:STATUS_COUNT - start] = model.registers()
        elif function != 16:
            return ExcCodes.ILLEGAL_FUNCTION
        elif address == COMMAND_ADDRESS and count == COMMAND_COUNT:
            model.execute(values)
        elif address == KEEPALIVE_ADDRESS and count == KEEPALIVE_COUNT:
            if not model.keepalive(values):
                return ExcCodes.ILLEGAL_VALUE
        else:
            return ExcCodes.ILLEGAL_ADDRESS
        return None

    return SimDevice(id=DEVICE_ID, simdata=[
        SimData(0, count=STATUS_COUNT, datatype=DataType.REGISTERS, readonly=True),
        SimData(COMMAND_ADDRESS, count=COMMAND_COUNT, datatype=DataType.REGISTERS),
        SimData(KEEPALIVE_ADDRESS, count=KEEPALIVE_COUNT, datatype=DataType.REGISTERS),
    ], action=access)


@contextlib.asynccontextmanager
async def running(model, host="127.0.0.1", port=5020):
    server = ModbusTcpServer(device(model), address=(host, port))
    async def advance():
        while True:
            model.tick()
            await asyncio.sleep(.01)
    ticker = asyncio.create_task(advance())
    try:
        await server.serve_forever(background=True)
        yield server
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        await server.shutdown()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--move-ms", type=int, default=400)
    parser.add_argument("--timeout-ms", type=int, default=1200)
    args = parser.parse_args()
    model = Station(move_ms=args.move_ms, timeout_ms=args.timeout_ms)
    async with running(model, args.host, args.port):
        print(json.dumps({"source": "SIMULATED", "host": args.host, "port": args.port, "boot_id": model.boot}), flush=True)
        stopped = asyncio.Event()
        with contextlib.suppress(NotImplementedError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
        await stopped.wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
