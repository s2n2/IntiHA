"""Import production core modules without executing Home Assistant's entrypoint.

No HA/BLE runtime is simulated by this loader. Socket tests use real aiohttp
clients/servers, but the server's device hardware is a test double.
"""
import asyncio
import copy
from pathlib import Path
import sys
import types

import aiohttp
from aiohttp import web
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("intiha_core")
package.__path__ = [str(ROOT / "custom_components" / "intiha")]
sys.modules["intiha_core"] = package


def device(index=0, name="We-Vibe Bond", display=None, motors=1, gap=0, battery=False):
    messages = {"ScalarCmd": [{"FeatureDescriptor": f"Motor {i+1}", "StepCount": 20,
                              "ActuatorType": "Vibrate"} for i in range(motors)]}
    if battery:
        messages["SensorReadCmd"] = [{"SensorType": "Battery", "SensorRange": [[0, 100]]}]
    result = {"DeviceIndex": index, "DeviceName": name, "DeviceMessages": messages,
              "DeviceMessageTimingGap": gap}
    if display is not None:
        result["DeviceDisplayName"] = display
    return result


class Server:
    def __init__(self, inventory=None):
        self.inventory = inventory or []
        self.messages = []
        self.sockets = []
        self.jobs = set()
        self.hooks = {}
        self.ping_ms = 1000
        self.handshakes = 0

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        try:
            async for frame in ws:
                if frame.type != aiohttp.WSMsgType.TEXT:
                    continue
                for msg in frame.json():
                    kind, body = next(iter(msg.items()))
                    self.messages.append((kind, copy.deepcopy(body)))
                    task = asyncio.create_task(self.reply(ws, kind, body))
                    self.jobs.add(task)
                    task.add_done_callback(self._done)
        finally:
            pass
        return ws

    def _done(self, job):
        self.jobs.discard(job)
        if not job.cancelled():
            job.exception()

    async def reply(self, ws, kind, body):
        if kind in self.hooks:
            handled = await self.hooks[kind](ws, body)
            if handled:
                return
        ident = body["Id"]
        if kind == "RequestServerInfo":
            self.handshakes += 1
            response = {"ServerInfo": {"Id": ident, "ServerName": "Test native Intiface",
                        "MessageVersion": 3, "MaxPingTime": self.ping_ms}}
        elif kind == "RequestDeviceList":
            response = {"DeviceList": {"Id": ident, "Devices": self.inventory}}
        elif kind == "SensorReadCmd":
            response = {"SensorReading": {"Id": ident, "DeviceIndex": body["DeviceIndex"],
                        "SensorIndex": body["SensorIndex"], "SensorType": "Battery", "Data": [72]}}
        else:
            response = {"Ok": {"Id": ident}}
        if not ws.closed:
            await ws.send_json([response])

    async def emit(self, kind, data):
        await self.sockets[-1].send_json([{kind: {"Id": 0, **data}}])

    async def disconnect(self):
        await self.sockets[-1].close()

    def of_kind(self, kind):
        return [body for k, body in self.messages if k == kind]


@pytest_asyncio.fixture
async def native_server():
    servers = []
    async def make(inventory=None):
        server = Server(inventory)
        app = web.Application()
        app.router.add_get("/", server.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        server.url = f"ws://127.0.0.1:{port}"
        servers.append((server, runner))
        return server
    yield make
    for server, runner in servers:
        for ws in server.sockets:
            await ws.close()
        for job in list(server.jobs):
            job.cancel()
        if server.jobs:
            await asyncio.gather(*server.jobs, return_exceptions=True)
        await runner.cleanup()


@pytest_asyncio.fixture
async def session():
    async with aiohttp.ClientSession() as session:
        yield session


async def eventually(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest_asyncio.fixture
async def controllers(session):
    from intiha_core.controller import Controller
    opened = []
    async def make(server, **kwargs):
        settings = dict(request_timeout=0.5, retry_initial=0.03, retry_max=0.1)
        settings.update(kwargs)
        ctl = Controller(session, server.url, "test-entry", **settings)
        opened.append(ctl)
        await ctl.set_enabled(True)
        return ctl
    yield make
    for controller in opened:
        await controller.set_enabled(False)
