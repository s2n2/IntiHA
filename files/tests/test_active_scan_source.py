"""Standalone regression checks for the real coordinator's active scan methods.

Use the production AST so these tests do not require a running Home Assistant;
they verify scan lifecycle and epoch safety, not actual ESPHome radio behaviour.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/intiha/coordinator.py"


def _methods(*names: str, bluetooth):
    parsed = ast.parse(SOURCE.read_text())
    coordinator = next(
        node
        for node in parsed.body
        if isinstance(node, ast.ClassDef) and node.name == "IntiHACoordinator"
    )
    found = [
        node
        for node in coordinator.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names
    ]
    assert {node.name for node in found} == set(names)
    functions = ast.Module(body=found, type_ignores=[])
    ast.fix_missing_locations(functions)
    scope = {
        "asyncio": asyncio,
        "bluetooth": bluetooth,
        "_LOGGER": logging.getLogger(__name__),
        "BridgeError": RuntimeError,
        "HomeAssistantError": RuntimeError,
        "CONF_SCAN_DURATION": "scan_duration",
        "DEFAULT_SCAN_DURATION": 30,
    }
    exec(compile(functions, str(SOURCE), "exec"), scope)
    return tuple(scope[name] for name in names)


@pytest.mark.asyncio
async def test_search_requests_active_sweep_without_waiting_for_scan_to_finish():
    entered = asyncio.Event()
    release = asyncio.Event()
    cached = [{"address": "AA", "name": "Bond"}]
    events = []

    async def fake_scan(_hass, *, duration):
        assert duration == 15.0
        entered.set()
        await release.wait()
        cached.append({"address": "BB", "name": "Second compatible device"})

    bluetooth = SimpleNamespace(async_request_active_scan=fake_scan)
    async_scan, active_scan = _methods(
        "async_scan", "_async_active_scan", bluetooth=bluetooth
    )

    class Subject:
        enabled = True
        _shutdown = False
        engine_connected = True
        _scan_until = 0
        scanning = False
        _last_advertisement = {}
        config = {"scan_duration": 45}
        hass = SimpleNamespace(loop=asyncio.get_running_loop())
        bridge = SimpleNamespace(
            connected=True, epoch=7, request=AsyncMock(return_value={})
        )
        ble = SimpleNamespace(discovered=lambda: list(cached))

        def __init__(self):
            self.tasks = []

        def _notify(self):
            pass

        async def _async_ble_event(self, message):
            events.append(message)

        def _spawn(self, task, _name):
            self.tasks.append(asyncio.create_task(task))

        async def _async_send_cached(self, epoch):
            assert epoch == self.bridge.epoch
            for entry in self.ble.discovered():
                await self._async_ble_event({**entry, "type": "advertisement"})

    subject = Subject()
    subject._async_active_scan = active_scan.__get__(subject, Subject)
    await async_scan(subject)
    assert [e["address"] for e in events] == ["AA"]
    assert len(subject.tasks) == 1
    await entered.wait()
    release.set()
    await asyncio.gather(*subject.tasks)
    assert [e["address"] for e in events] == ["AA", "AA", "BB"]
    subject.bridge.request.assert_awaited_once_with("scan", seconds=45)


@pytest.mark.asyncio
async def test_retired_engine_generation_does_not_receive_post_scan_data():
    called = []
    async def fake_scan(_hass, *, duration):
        called.append(duration)

    (active_scan,) = _methods(
        "_async_active_scan",
        bluetooth=SimpleNamespace(async_request_active_scan=fake_scan),
    )

    class Subject:
        _shutdown = False
        enabled = True
        engine_connected = True
        hass = object()
        bridge = SimpleNamespace(epoch=99)
        _last_advertisement = {"AA": 1.0}
        send_cache = AsyncMock()

        async def _async_send_cached(self, epoch):
            await self.send_cache(epoch)

    subject = Subject()
    await active_scan(subject, 98, 10)
    assert called == [10.0]
    assert subject._last_advertisement == {"AA": 1.0}
    subject.send_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_sweep_error_does_not_disconnect_engine():
    async def broken_scan(_hass, *, duration):
        raise OSError("scan not supported")

    (active_scan,) = _methods(
        "_async_active_scan",
        bluetooth=SimpleNamespace(async_request_active_scan=broken_scan),
    )

    class Subject:
        _shutdown = False
        enabled = True
        engine_connected = True
        hass = object()
        bridge = SimpleNamespace(epoch=2)
        _last_advertisement = {"AA": 1.0}
        send_cache = AsyncMock()

        _safe_error = staticmethod(str)

        async def _async_send_cached(self, epoch):
            await self.send_cache(epoch)

    subject = Subject()
    await active_scan(subject, 2, 10)
    assert subject._last_advertisement == {"AA": 1.0}
    subject.send_cache.assert_not_awaited()


def test_periodic_sweep_rate_is_bounded():
    parsed = ast.parse(SOURCE.read_text())
    coordinator = next(
        node
        for node in parsed.body
        if isinstance(node, ast.ClassDef) and node.name == "IntiHACoordinator"
    )
    tick = next(
        node
        for node in coordinator.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_tick"
    )
    assert any(isinstance(n, ast.Constant) and n.value == 180 for n in ast.walk(tick))
    assert any(
        isinstance(n, ast.Attribute) and n.attr == "_next_active_sweep"
        for n in ast.walk(tick)
    )
