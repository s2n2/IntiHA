#!/usr/bin/env python3
"""Run the production protocol client against a real, EMPTY upstream engine.

CI starts that engine with NO hardware managers. This test is not a radio test.
It refuses StopAllDevices when inventory is nonempty. Never point CI at a live
controller containing devices.
"""
from pathlib import Path
import argparse
import asyncio
import json
import sys
import types
import aiohttp

ROOT=Path(__file__).resolve().parents[1]
pkg=types.ModuleType('intiha_smoke')
pkg.__path__=[str(ROOT/'custom_components/intiha')]
sys.modules['intiha_smoke']=pkg
from intiha_smoke.client import EngineClient  # noqa: E402

async def run(url):
    async with aiohttp.ClientSession() as session:
        last_error = None
        for _ in range(30):
            client=EngineClient(session, timeout=2)
            try:
                info=await client.open(url)
                break
            except Exception as err:
                last_error=err
                await client.close()
                await asyncio.sleep(1)
        else:
            raise RuntimeError(f'Actual upstream engine handshake failed: {last_error}')
        try:
            inventory=await client.request('RequestDeviceList', expected='DeviceList')
            if inventory.get('Devices') != []:
                raise RuntimeError('Smoke test requires EMPTY inventory; refusing any device commands')
            await client.request('Ping')
            await client.request('StopAllDevices')
            print(json.dumps({'test':'actual-upstream-protocol-smoke','server':info,
                              'device_count':0,'radio_tested':False}, indent=2))
        finally:
            await client.close('smoke_test_complete')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='ws://127.0.0.1:12347')
    asyncio.run(run(parser.parse_args().url))
