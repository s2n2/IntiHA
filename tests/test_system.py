"""Run the compiled Rust engine against the complete HA integration.

Only the radio is simulated. The upstream Buttplug engine, JSON/WebSocket
transport, coordinator, BLE bridge, config-entry loader and entity platforms
are real. Build first with ``cargo build --locked --manifest-path engine/Cargo.toml``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import aiohttp
import pytest
import pytest_asyncio
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiha import ble
from custom_components.intiha.const import DOMAIN

pytestmark = [pytest.mark.system, pytest.mark.asyncio]

ADDRESS = "AA:BB:CC:DD:EE:01"
SERVICE = "f000bb03-0451-4000-b000-000000000000"
TX = "f000c000-0451-4000-b000-000000000000"
RX = "f000b000-0451-4000-b000-000000000000"
STOP = bytes([15, 0, 0, 0, 0, 0, 0, 0])
BRIDGE_TOKEN = "system_test_bridge_" + "a" * 32
CLIENT_TOKEN = "system_test_client_" + "b" * 32
ENGINE_BINARY = Path(__file__).resolve().parents[1] / "engine/target/debug/intiha-engine"


async def until(predicate):
    """Wait for an observed state transition, never a guessed fixed delay."""
    async with asyncio.timeout(20):
        # The Rust process and HA entity registry have no shared event object.
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.02)


class NovaServices:
    def __init__(self):
        self.tx = SimpleNamespace(
            uuid=TX, handle=1, properties=["write"], max_write_without_response_size=20
        )
        self.rx = SimpleNamespace(
            uuid=RX, handle=2, properties=["read", "notify"], max_write_without_response_size=20
        )

    def __iter__(self):
        yield SimpleNamespace(uuid=SERVICE, characteristics=[self.tx, self.rx])

    def get_characteristic(self, uuid):
        return {TX: self.tx, RX: self.rx}.get(uuid)


class NovaRadio:
    """Minimal classic Nova GATT radio; no fake protocol or server."""

    def __init__(self, device, *, disconnected_callback, **_kwargs):
        self.address = device.address
        self.disconnected_callback = disconnected_callback
        self.services = NovaServices()
        self.is_connected = False
        self.writes = []
        self.operations = []
        self.fail_stop = False

    async def write_gatt_char(self, characteristic, data, *, response):
        uuid = characteristic if isinstance(characteristic, str) else characteristic.uuid
        assert uuid == TX
        assert response is True
        payload = bytes(data)
        self.writes.append(payload)
        self.operations.append(("write", payload))
        if self.fail_stop and payload == STOP:
            raise OSError("Simulated stop transmission failure")

    async def read_gatt_char(self, characteristic):
        assert characteristic.uuid == RX
        return bytearray([100])

    async def start_notify(self, characteristic, callback):
        assert characteristic.uuid == RX

    async def stop_notify(self, characteristic):
        assert characteristic.uuid == RX

    async def disconnect(self):
        self.operations.append(("disconnect",))
        self.is_connected = False
        self.disconnected_callback(self)


@dataclass
class Stack:
    hass: object
    entry: MockConfigEntry
    binary: Path
    environment: dict[str, str]
    bridge_port: int
    client_port: int
    process: asyncio.subprocess.Process | None = None
    radios: list[NovaRadio] = field(default_factory=list)

    @property
    def coordinator(self):
        return self.entry.runtime_data

    @property
    def client_url(self):
        return f"ws://127.0.0.1:{self.client_port}/?token={CLIENT_TOKEN}"

    async def start_engine(self):
        self.process = await asyncio.create_subprocess_exec(
            str(self.binary),
            env=self.environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with aiohttp.ClientSession() as session:
            async with asyncio.timeout(10):
                while True:
                    if self.process.returncode is not None:
                        pytest.fail("IntiHA engine exited before opening its health endpoint")
                    try:
                        async with session.get(
                            f"http://127.0.0.1:{self.bridge_port}/health"
                        ) as response:
                            if response.status == 200:
                                break
                    except aiohttp.ClientConnectionError:
                        pass
                    await asyncio.sleep(0.02)

    async def crash_engine(self):
        assert self.process is not None
        self.process.kill()
        await self.process.wait()

    async def finish(self):
        if self.entry.state is ConfigEntryState.LOADED:
            await self.hass.config_entries.async_unload(self.entry.entry_id)
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                async with asyncio.timeout(8):
                    await self.process.wait()
            except TimeoutError:
                self.process.kill()
                await self.process.wait()

    def entity(self, domain, key):
        address = ADDRESS if domain == "number" else "server"
        return er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, f"{self.entry.entry_id}_{address}_{key}"
        )


@pytest_asyncio.fixture
async def stack(hass, mock_bluetooth, monkeypatch, socket_enabled, unused_tcp_port_factory):
    binary = ENGINE_BINARY
    if not await hass.async_add_executor_job(binary.exists):
        if os.getenv("INTIHA_REQUIRE_SYSTEM_TESTS") == "1":
            pytest.fail("Required compiled IntiHA engine is missing; run cargo build --locked")
        pytest.skip("Compile engine/target/debug/intiha-engine to run cross-process tests")
    bridge_port = unused_tcp_port_factory()
    client_port = unused_tcp_port_factory()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IntiHA",
        unique_id=DOMAIN,
        data={
            "engine_url": f"ws://127.0.0.1:{bridge_port}/bridge",
            "bridge_token": BRIDGE_TOKEN,
        },
        options={"selected_devices": [ADDRESS], "auto_reconnect": True},
    )
    entry.add_to_hass(hass)
    value = Stack(
        hass,
        entry,
        binary,
        {
            **os.environ,
            "INTIHA_BRIDGE_TOKEN": BRIDGE_TOKEN,
            "INTIHA_CLIENT_TOKEN": CLIENT_TOKEN,
            "INTIHA_BIND": "127.0.0.1",
            "INTIHA_BRIDGE_PORT": str(bridge_port),
            "INTIHA_CLIENT_PORT": str(client_port),
            "RUST_LOG": "error",
        },
        bridge_port,
        client_port,
    )
    device = BLEDevice(ADDRESS, "Nova", {"source": "simulated-esphome-proxy"})
    info = BluetoothServiceInfoBleak(
        name="Nova",
        address=ADDRESS,
        rssi=-45,
        manufacturer_data={},
        service_data={},
        service_uuids=[SERVICE],
        source="simulated-esphome-proxy",
        device=device,
        advertisement=None,
        connectable=True,
        time=100.0,
        tx_power=None,
    )
    monkeypatch.setattr(ble.bluetooth, "async_register_callback", Mock(return_value=Mock()))
    monkeypatch.setattr(ble.bluetooth, "async_discovered_service_info", Mock(return_value=[info]))
    monkeypatch.setattr(ble.bluetooth, "async_ble_device_from_address", Mock(return_value=device))
    monkeypatch.setattr(ble.bluetooth, "async_scanner_count", Mock(return_value=1))
    monkeypatch.setattr(ble, "BleakClientWithServiceCache", NovaRadio)

    async def establish(client_class, device, name, **kwargs):
        radio = client_class(device, **kwargs)
        radio.is_connected = True
        value.radios.append(radio)
        return radio

    monkeypatch.setattr(ble, "establish_connection", establish)
    try:
        await value.start_engine()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await until(lambda: value.coordinator.engine_connected)
        await value.coordinator.async_set_enabled(True)
        await value.coordinator.async_scan()
        await until(lambda: value.coordinator.devices[ADDRESS].connected)
        await until(lambda: value.entity("number", "level_1") is not None)
        yield value
    finally:
        await value.finish()


async def test_full_stack_nova_discovery_and_real_ha_slider(stack):
    device = stack.coordinator.devices[ADDRESS]
    assert device.name == "WeVibe Nova"
    assert len(device.scalar_features()) == 2
    assert stack.coordinator.status == "ready"
    radio = stack.radios[-1]
    assert len(radio.writes) >= 3
    assert all(payload == STOP for payload in radio.writes)
    plan = stack.coordinator.ble._connections[ADDRESS].stop_plan
    assert plan and all(write.data == STOP for write in plan)
    number = stack.entity("number", "level_0")
    await stack.hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 50}, blocking=True
    )
    await until(lambda: any(payload != STOP for payload in radio.writes))
    assert float(stack.hass.states.get(number).state) == 50
    assert stack.hass.states.get(number).attributes["value_type"] == "last_command"
    await stack.coordinator.async_stop_all()
    await until(lambda: not radio.is_connected)
    assert radio.operations[-2:] == [("write", STOP), ("disconnect",)]


async def test_engine_crash_uses_ha_cached_stop_and_restart_never_replays_output(stack):
    coordinator = stack.coordinator
    original_connection = coordinator.devices[ADDRESS].connection_id
    radio = stack.radios[-1]
    await coordinator.async_set_output(ADDRESS, 0, 50)
    await until(lambda: any(payload != STOP for payload in radio.writes))
    await stack.crash_engine()
    await until(lambda: not coordinator.engine_connected and not radio.is_connected)
    assert radio.operations[-2:] == [("write", STOP), ("disconnect",)]
    assert coordinator.devices[ADDRESS].outputs == {}
    assert not coordinator.stop_uncertain
    await stack.start_engine()
    await until(lambda: coordinator.engine_connected and coordinator.devices[ADDRESS].connected)
    assert coordinator.devices[ADDRESS].connection_id != original_connection
    assert stack.radios[-1] is not radio
    assert all(payload == STOP for payload in stack.radios[-1].writes)
    assert coordinator.devices[ADDRESS].outputs == {}


async def test_stop_failure_crosses_real_wire_and_remains_visible(stack):
    coordinator = stack.coordinator
    radio = stack.radios[-1]
    await coordinator.async_set_output(ADDRESS, 0, 50)
    await until(lambda: any(payload != STOP for payload in radio.writes))
    radio.fail_stop = True
    with pytest.raises(HomeAssistantError, match="Stop could not be confirmed"):
        await coordinator.async_stop_all()
    assert coordinator.stop_uncertain
    assert coordinator.status == "stop_unconfirmed"
    assert not radio.is_connected


async def test_external_buttplug_v3_handshake_and_exclusive_control(stack):
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.WSServerHandshakeError):
            await session.ws_connect(f"ws://127.0.0.1:{stack.client_port}/")
        async with session.ws_connect(stack.client_url) as socket:
            await socket.send_json(
                [
                    {
                        "RequestServerInfo": {
                            "Id": 1,
                            "ClientName": "IntiHA system test",
                            "MessageVersion": 3,
                        }
                    }
                ]
            )
            async with asyncio.timeout(10):
                while True:
                    response = await socket.receive_json()
                    if any("ServerInfo" in message for message in response):
                        break
            await until(lambda: stack.coordinator.client_connected)
            assert stack.coordinator.owner == "external_client"
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await session.ws_connect(stack.client_url)
            with pytest.raises(HomeAssistantError):
                await stack.coordinator.async_set_output(ADDRESS, 0, 50)
        await until(lambda: not stack.coordinator.client_connected)
