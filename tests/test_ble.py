"""Exercise the BLE bridge against HA's real API with a simulated radio.

No physical device behaviour is inferred by these tests. They verify request
isolation, cancellation, cleanup and stop ordering, which are independent of
the device's vendor protocol.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import pytest_asyncio
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from custom_components.intiha import ble

pytestmark = pytest.mark.asyncio

ADDRESS = "AA:BB:CC:DD:EE:01"
OTHER_ADDRESS = "AA:BB:CC:DD:EE:02"
CHARACTERISTIC = "00002a19-0000-1000-8000-00805f9b34fb"
SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"


class FakeServices:
    """A GATT service collection with Bleak's actual public interface."""

    def __init__(self):
        self.characteristic = SimpleNamespace(
            uuid=CHARACTERISTIC,
            handle=12,
            properties=["read", "write", "write-without-response", "notify"],
            max_write_without_response_size=20,
        )

    def __iter__(self):
        yield SimpleNamespace(uuid=SERVICE, characteristics=[self.characteristic])

    def get_characteristic(self, uuid):
        return self.characteristic if uuid == CHARACTERISTIC else None


class FakeClient:
    """Radio stub: every attempted operation remains visible to assertions."""

    def __init__(self, device, *, disconnected_callback, **_kwargs):
        self.device = device
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        self.services = FakeServices()
        self.operations = []
        self.callbacks = {}
        self.block_payload = None
        self.write_started = asyncio.Event()
        self.write_release = asyncio.Event()
        self.fail_writes = False
        self.pair_requested = _kwargs.get("pair", False)

    async def connect(self):
        self.is_connected = True
        if self.pair_requested:
            await self.pair()

    async def pair(self):
        self.operations.append(("pair",))
        # Current Bleak returns None, not a truthy acknowledgement.
        return None

    async def disconnect(self):
        self.operations.append(("disconnect",))
        self.is_connected = False
        self.disconnected_callback(self)

    async def write_gatt_char(self, characteristic, data, *, response):
        uuid = characteristic if isinstance(characteristic, str) else characteristic.uuid
        self.operations.append(("write", uuid, bytes(data), response))
        if self.fail_writes:
            raise OSError("simulated radio failure")
        if bytes(data) == self.block_payload:
            self.write_started.set()
            await self.write_release.wait()

    async def read_gatt_char(self, characteristic):
        self.operations.append(("read", characteristic.uuid))
        return bytearray([93])

    async def start_notify(self, characteristic, callback):
        self.callbacks[characteristic.uuid] = callback

    async def stop_notify(self, characteristic):
        self.operations.append(("unsubscribe", characteristic.uuid))


@dataclass
class Radio:
    bridge: ble.BleBridge
    allowed: set[str]
    events: list = field(default_factory=list)
    clients: list = field(default_factory=list)
    lookup: Mock = field(default_factory=Mock)
    registration: Mock = field(default_factory=Mock)
    gate: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def request(self, op, connection_id="first", **kwargs):
        return await self.bridge.async_request(
            {"op": op, "address": ADDRESS, "connection_id": connection_id, **kwargs}
        )

    async def write(self, data, connection_id="first"):
        return await self.request(
            "write", connection_id, characteristic=CHARACTERISTIC, data=data, response=False
        )

    async def stop_plan(self, connection_id="first"):
        return await self.request(
            "set_stop_plan",
            connection_id,
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": False}],
        )


@pytest_asyncio.fixture
async def radio(hass, monkeypatch):
    allowed = {ADDRESS}
    events = []

    async def emit(event):
        events.append(event)

    bridge = ble.BleBridge(hass, emit, allowed.__contains__)
    result = Radio(bridge, allowed, events=events)
    result.lookup.return_value = BLEDevice(ADDRESS, "Test device", {"source": "proxy-a"})
    result.registration.return_value = Mock()
    monkeypatch.setattr(ble.bluetooth, "async_register_callback", result.registration)
    monkeypatch.setattr(ble.bluetooth, "async_ble_device_from_address", result.lookup)
    monkeypatch.setattr(ble.bluetooth, "async_discovered_service_info", Mock(return_value=[]))
    monkeypatch.setattr(ble, "BleakClientWithServiceCache", FakeClient)

    async def establish(client_class, device, name, **kwargs):
        assert kwargs["max_attempts"] == 1
        assert kwargs["timeout"] >= 10
        client = client_class(device, **kwargs)
        result.clients.append(client)
        result.started.set()
        if result.gate is not None:
            await result.gate.wait()
        await client.connect()
        return client

    monkeypatch.setattr(ble, "establish_connection", establish)
    await bridge.async_start()
    try:
        yield result
    finally:
        await bridge.async_stop()


async def test_current_ha_advertisement_shape_and_connectable_semantics(radio, monkeypatch):
    """Use a real HA service-info object; a passive source stays passive."""
    info = BluetoothServiceInfoBleak(
        name="Example",
        address=ADDRESS,
        rssi=-55,
        manufacturer_data={123: b"\x01\x02"},
        service_data={SERVICE: b"\x03"},
        service_uuids=[SERVICE],
        source="shelly-example",
        device=BLEDevice(ADDRESS, "Example", {}),
        advertisement=None,
        connectable=False,
        time=100.0,
        tx_power=None,
    )
    monkeypatch.setattr(ble.bluetooth, "async_discovered_service_info", Mock(return_value=[info]))
    snapshot = radio.bridge.discovered()
    assert snapshot[0]["source"] == "shelly-example"
    assert snapshot[0]["connectable"] is False
    assert snapshot[0]["manufacturer_data"] == {"123": [1, 2]}
    assert snapshot[0]["service_data"] == {SERVICE: [3]}
    # Per HA's documented matcher semantics, False opts into all adapters.
    assert radio.registration.call_args.args[2] == {"connectable": False}
    callback = radio.registration.call_args.args[1]
    callback(info, None)
    await asyncio.sleep(0)
    assert snapshot[0] in radio.events


async def test_connect_services_read_write_and_new_adapter_lookup(radio):
    result = await radio.request("connect")
    assert result["services"][0]["characteristics"][0]["handle"] == 12
    assert radio.bridge.connected_addresses == {ADDRESS}
    radio.lookup.assert_called_with(radio.bridge._hass, ADDRESS, connectable=True)
    assert await radio.request("read", characteristic=CHARACTERISTIC) == {"data": [93]}
    await radio.write([1, 2])
    assert radio.clients[0].operations[-1] == ("write", CHARACTERISTIC, b"\x01\x02", False)
    await radio.request("disconnect")
    radio.lookup.return_value = BLEDevice(ADDRESS, "Test device", {"source": "proxy-b"})
    await radio.request("connect", "second")
    assert len(radio.clients) == 2
    assert radio.clients[1].device.details["source"] == "proxy-b"
    assert radio.lookup.call_count == 2


async def test_unrecognised_device_never_reaches_bluetooth(radio):
    with pytest.raises(ble.BleBridgeError, match="not been identified as compatible"):
        await radio.bridge.async_request(
            {"op": "connect", "address": OTHER_ADDRESS, "connection_id": "bad"}
        )
    assert radio.clients == []
    radio.lookup.assert_not_called()


async def test_device_removed_during_connect_closes_partial_client(radio):
    radio.gate = asyncio.Event()
    pending = asyncio.create_task(radio.request("connect"))
    await radio.started.wait()
    radio.allowed.clear()
    radio.gate.set()
    with pytest.raises(ble.BleBridgeError, match="revoked"):
        await pending
    assert radio.clients[0].operations == [("disconnect",)]
    assert not radio.bridge.connected_addresses


async def test_stale_commands_and_callbacks_cannot_touch_new_link(radio):
    await radio.request("connect")
    old = radio.clients[0]
    await radio.request("disconnect")
    await radio.request("connect", "second")
    new = radio.clients[1]
    with pytest.raises(ble.BleBridgeError, match="stale"):
        await radio.write([100])
    with pytest.raises(ble.BleBridgeError, match="stale"):
        await radio.request("disconnect")
    old.disconnected_callback(old)
    assert new.is_connected
    assert new.operations == []
    with pytest.raises(ble.BleBridgeError, match="different"):
        await radio.request("connect", "third")
    await radio.request("disconnect", "second")
    with pytest.raises(ble.BleBridgeError, match="retired"):
        await radio.request("connect")


async def test_stop_plan_executes_before_bridge_reset_disconnect(radio):
    await radio.request("connect")
    await radio.stop_plan()
    await radio.write([10])
    await radio.bridge.async_disconnect_all()
    assert radio.clients[0].operations == [
        ("write", CHARACTERISTIC, b"\x0a", False),
        ("write", CHARACTERISTIC, b"\x00", False),
        ("disconnect",),
    ]
    assert not radio.bridge.connected_addresses
    # Discovery survives a bridge outage and a fresh generation is usable.
    await radio.request("connect", "after-reset")
    assert radio.bridge.connected_addresses == {ADDRESS}


async def test_stop_failure_is_reported_and_disconnect_still_runs(radio):
    await radio.request("connect")
    await radio.stop_plan()
    radio.clients[0].fail_writes = True
    await radio.bridge.async_disconnect_all()
    assert any(event["type"] == "stop_failed" for event in radio.events)
    assert radio.clients[0].operations[-1] == ("disconnect",)


async def test_explicit_disconnect_reports_stop_failure_in_rpc_after_cleanup(radio):
    await radio.request("connect")
    await radio.stop_plan()
    radio.clients[0].fail_writes = True
    with pytest.raises(ble.BleBridgeError, match="Stop could not be sent"):
        await radio.request("disconnect")
    assert radio.clients[0].operations[-1] == ("disconnect",)
    assert not radio.bridge.connected_addresses
    assert any(event["type"] == "stop_failed" for event in radio.events)


async def test_gatt_timeout_invalidates_queued_commands_without_replay(radio, monkeypatch):
    monkeypatch.setattr(ble, "IO_TIMEOUT", 0.02)
    await radio.request("connect")
    client = radio.clients[0]
    client.block_payload = b"\x55"
    first = asyncio.create_task(radio.write([0x55]))
    await client.write_started.wait()
    queued = asyncio.create_task(radio.write([0x66]))
    with pytest.raises(ble.BleBridgeError, match="GATT write timed out"):
        await first
    with pytest.raises(ble.BleBridgeError, match="stale"):
        await queued
    assert client.operations == [("write", CHARACTERISTIC, b"\x55", False), ("disconnect",)]
    assert not radio.bridge.connected_addresses


async def test_reset_cancels_pending_connect_and_releases_partial_client(radio):
    radio.gate = asyncio.Event()
    pending = asyncio.create_task(radio.request("connect"))
    await radio.started.wait()
    await radio.bridge.async_disconnect_all()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert radio.clients[0].operations == [("disconnect",)]
    assert not radio.bridge.connected_addresses
    radio.gate.set()
    await radio.request("connect", "new-session")
    assert len(radio.clients) == 2


async def test_reset_cancels_output_then_sends_stop_before_disconnect(radio):
    await radio.request("connect")
    await radio.stop_plan()
    client = radio.clients[0]
    client.block_payload = b"\x55"
    pending = asyncio.create_task(radio.write([0x55]))
    await client.write_started.wait()
    queued = asyncio.create_task(radio.write([0x66]))
    await asyncio.sleep(0)
    await radio.bridge.async_disconnect_all()
    for task in (pending, queued):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert client.operations == [
        ("write", CHARACTERISTIC, b"\x55", False),
        ("write", CHARACTERISTIC, b"\x00", False),
        ("disconnect",),
    ]


async def test_reset_does_not_cancel_a_stop_already_being_transmitted(radio):
    await radio.request("connect")
    await radio.stop_plan()
    client = radio.clients[0]
    client.block_payload = b"\x00"
    disconnect = asyncio.create_task(radio.request("disconnect"))
    await client.write_started.wait()
    reset = asyncio.create_task(radio.bridge.async_disconnect_all())
    await asyncio.sleep(0)
    assert not reset.done()
    client.write_release.set()
    await reset
    with pytest.raises(asyncio.CancelledError):
        await disconnect
    assert client.operations == [("write", CHARACTERISTIC, b"\x00", False), ("disconnect",)]
    assert not any(event["type"] == "stop_failed" for event in radio.events)


async def test_notifications_are_generation_bound_and_unsubscribe_is_effective(radio):
    await radio.request("connect")
    await radio.request("subscribe", characteristic=CHARACTERISTIC)
    callback = radio.clients[0].callbacks[CHARACTERISTIC]
    callback(None, bytearray([3]))
    await asyncio.sleep(0)
    assert radio.events[-1] == {
        "type": "notification",
        "address": ADDRESS,
        "connection_id": "first",
        "characteristic": CHARACTERISTIC,
        "data": [3],
    }
    await radio.request("unsubscribe", characteristic=CHARACTERISTIC)
    count = len(radio.events)
    callback(None, bytearray([4]))
    await asyncio.sleep(0)
    assert len(radio.events) == count
    await radio.request("disconnect")
    await radio.request("connect", "second")
    count = len(radio.events)
    callback(None, bytearray([5]))
    await asyncio.sleep(0)
    assert len(radio.events) == count


@pytest.mark.parametrize(
    "write",
    [
        {"characteristic": CHARACTERISTIC, "data": [True], "response": False},
        {"characteristic": CHARACTERISTIC, "data": [256], "response": False},
        {"characteristic": CHARACTERISTIC, "data": [-1], "response": False},
        {"characteristic": CHARACTERISTIC, "data": [1] * 21, "response": False},
        {"characteristic": CHARACTERISTIC, "data": [1] * 513, "response": True},
        {"characteristic": CHARACTERISTIC, "data": [], "response": False},
        {"characteristic": "not-a-uuid", "data": [0], "response": False},
        {"characteristic": CHARACTERISTIC, "data": [0], "response": "false"},
    ],
)
async def test_invalid_stop_plan_never_reaches_radio(radio, write):
    await radio.request("connect")
    with pytest.raises(ValueError):
        await radio.request("set_stop_plan", writes=[write])
    assert radio.clients[0].operations == []


async def test_notification_backpressure_closes_link_instead_of_losing_protocol_state(radio):
    await radio.request("connect")
    await radio.request("subscribe", characteristic=CHARACTERISTIC)
    callback = radio.clients[0].callbacks[CHARACTERISTIC]
    # Do not yield: fill the bounded queue before its consumer runs.
    for _ in range(ble.MAX_EVENTS + 1):
        callback(None, bytearray([1]))
    assert not radio.bridge.connected_addresses
    await radio.bridge.async_disconnect_all()
    assert any(
        event["type"] == "disconnected" and event["reason"] == "notification_queue_full"
        for event in radio.events
    )


async def test_clean_stop_removes_advertisement_subscription_and_can_restart(radio):
    await radio.request("connect")
    await radio.stop_plan()
    unsubscribe = radio.registration.return_value
    await radio.bridge.async_stop()
    unsubscribe.assert_called_once()
    assert not radio.bridge.connected_addresses
    assert not radio.bridge._requests
    assert not radio.bridge._background
    assert radio.bridge._event_worker is None
    with pytest.raises(ble.BleBridgeError, match="disabled"):
        await radio.request("connect", "while-off")
    await radio.bridge.async_start()
    await radio.request("connect", "after-start")
    assert radio.bridge.connected_addresses == {ADDRESS}


async def test_prepare_stop_writes_and_caches_before_acknowledging(radio):
    await radio.request("connect")
    result = await radio.request(
        "prepare_stop",
        writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
    )
    assert result == {"verified": True, "connection_id": "first", "write_count": 1}
    assert radio.clients[0].operations == [("write", CHARACTERISTIC, b"\x00", True)]
    await radio.request("disconnect")
    assert radio.clients[0].operations[-2:] == [
        ("write", CHARACTERISTIC, b"\x00", True),
        ("disconnect",),
    ]


async def test_prepare_stop_waits_for_gatt_and_serialises_following_commands(radio):
    await radio.request("connect")
    client = radio.clients[0]
    client.block_payload = b"\x00"
    pending = asyncio.create_task(
        radio.request(
            "prepare_stop",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    )
    await client.write_started.wait()
    following = asyncio.create_task(radio.write([10]))
    await asyncio.sleep(0)
    assert not pending.done()
    assert not following.done()
    assert len(client.operations) == 1
    client.write_release.set()
    assert (await pending)["verified"] is True
    await following
    assert client.operations[-1][2] == b"\x0a"


async def test_prepare_stop_write_failure_never_acknowledges_and_closes_link(radio):
    await radio.request("connect")
    radio.clients[0].fail_writes = True
    with pytest.raises(ble.BleBridgeError, match="Stop GATT write failed"):
        await radio.request(
            "prepare_stop",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    assert not radio.bridge.connected_addresses
    assert radio.clients[0].operations[-1] == ("disconnect",)
    assert any(event["type"] == "stop_failed" for event in radio.events)


async def test_prepare_stop_dropped_link_cannot_return_verified(radio):
    await radio.request("connect")
    client = radio.clients[0]
    client.block_payload = b"\x00"
    pending = asyncio.create_task(
        radio.request(
            "prepare_stop",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    )
    await client.write_started.wait()
    client.is_connected = False
    client.disconnected_callback(client)
    client.write_release.set()
    with pytest.raises(ble.BleBridgeError, match="link lost"):
        await pending
    assert not radio.bridge.connected_addresses


async def test_prepare_stop_cancelled_keeps_cached_stop_for_cleanup(radio, monkeypatch):
    monkeypatch.setattr(ble, "STOP_TIMEOUT", 0.01)
    await radio.request("connect")
    client = radio.clients[0]
    client.block_payload = b"\x00"
    pending = asyncio.create_task(
        radio.request(
            "prepare_stop",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    )
    await client.write_started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not radio.bridge.connected_addresses
    # Initial verification plus a separate best-effort cached stop, then close.
    assert len([op for op in client.operations if op[0] == "write"]) == 2
    assert client.operations[-1] == ("disconnect",)


async def test_prepare_stop_timeout_has_useful_error_not_empty_string(radio, monkeypatch):
    monkeypatch.setattr(ble, "IO_TIMEOUT", 0.01)
    monkeypatch.setattr(ble, "STOP_TIMEOUT", 0.01)
    await radio.request("connect")
    radio.clients[0].block_payload = b"\x00"
    with pytest.raises(ble.BleBridgeError, match="Stop GATT write timed out"):
        await radio.request(
            "prepare_stop",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    assert not radio.bridge.connected_addresses


async def test_retired_client_callback_under_same_rpc_id_is_ignored(radio):
    await radio.request("connect")
    current = radio.clients[0]
    abandoned = FakeClient(current.device, disconnected_callback=lambda _: None)
    # Both objects can belong to one logical establish-connection request.
    current.disconnected_callback(abandoned)
    assert radio.bridge.connected_addresses == {ADDRESS}
    assert radio.bridge._connections[ADDRESS].client is current
    assert not radio.bridge._closing


async def test_prepare_stop_rejects_unsupported_write_mode_without_sending(radio):
    await radio.request("connect")
    radio.clients[0].services.characteristic.properties = ["read"]
    with pytest.raises(ValueError, match="write mode"):
        await radio.request(
            "prepare_stop",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    assert radio.clients[0].operations == []
    assert not radio.bridge._connections[ADDRESS].stop_plan


async def test_prepare_stop_rejects_stale_connection_id(radio):
    await radio.request("connect")
    with pytest.raises(ble.BleBridgeError, match="stale"):
        await radio.request(
            "prepare_stop",
            "retired-id",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    assert radio.clients[0].operations == []


async def test_connection_deadline_has_useful_error_and_cleans_client(radio, monkeypatch):
    monkeypatch.setattr(ble, "CONNECT_TIMEOUT", 0.01)
    radio.gate = asyncio.Event()
    with pytest.raises(ble.BleBridgeError, match="connection/pairing/service discovery timed out"):
        await radio.request("connect")
    assert not radio.bridge.connected_addresses
    assert radio.clients[0].operations == [("disconnect",)]


async def test_pairing_policy_reaches_backend_before_service_response(radio):
    radio.bridge._requires_pairing = lambda address: address == ADDRESS
    result = await radio.request("connect")
    assert result["connection_id"] == "first"
    assert radio.clients[0].pair_requested is True
    assert radio.clients[0].operations == [("pair",)]
    assert radio.bridge._connections[ADDRESS].pairing_requested is True
    # Pairing alone must not create a verified stop plan.
    assert radio.bridge._connections[ADDRESS].stop_plan == ()


async def test_normal_devices_are_not_forced_to_pair(radio):
    await radio.request("connect")
    assert radio.clients[0].pair_requested is False
    assert radio.clients[0].operations == []


async def test_security_rejection_teaches_next_connection_without_replaying_output(radio):
    learned = []
    radio.bridge._on_pairing_required = learned.append
    await radio.request("connect")
    client = radio.clients[0]

    async def reject(characteristic, data, *, response):
        client.operations.append(("rejected", bytes(data)))
        raise RuntimeError(
            "Bluetooth GATT Error handle=27 error=15 description=Insufficient encryption"
        )

    client.write_gatt_char = reject
    with pytest.raises(ble.BleBridgeError, match="Insufficient encryption"):
        await radio.write([80])
    assert learned == [ADDRESS]
    assert client.operations == [("rejected", b"\x50"), ("disconnect",)]
    assert not radio.bridge.connected_addresses
    await radio.request("connect", "new-session")
    replacement = radio.clients[-1]
    assert replacement.pair_requested
    assert replacement.operations == [("pair",)]
    assert radio.bridge._connections[ADDRESS].stop_plan == ()


async def test_stop_encryption_failure_learns_security_and_stays_unverified(radio):
    await radio.request("connect")
    client = radio.clients[0]

    async def reject(*_args, **_kwargs):
        raise RuntimeError("Bluetooth GATT Error error=15 description=Insufficient encryption")

    client.write_gatt_char = reject
    with pytest.raises(ble.BleBridgeError, match="Insufficient encryption"):
        await radio.request(
            "prepare_stop",
            writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
        )
    assert ADDRESS in radio.bridge._pairing_required
    assert not radio.bridge.connected_addresses
    await radio.request("connect", "encrypted-session")
    result = await radio.request(
        "prepare_stop",
        "encrypted-session",
        writes=[{"characteristic": CHARACTERISTIC, "data": [0], "response": True}],
    )
    assert result["verified"] is True
    assert radio.clients[-1].operations == [("pair",), ("write", CHARACTERISTIC, b"\x00", True)]


async def test_pairing_not_supported_does_not_fall_back_to_unencrypted(radio, monkeypatch):
    class Unsupported(FakeClient):
        async def pair(self):
            raise NotImplementedError("Pairing is not available in this version ESPHome")

    monkeypatch.setattr(ble, "BleakClientWithServiceCache", Unsupported)
    radio.bridge._requires_pairing = lambda _address: True
    with pytest.raises(ble.BleBridgeError, match="cannot perform the requested pairing"):
        await radio.request("connect")
    assert not radio.bridge.connected_addresses
    assert len(radio.clients) == 1
    assert radio.clients[0].operations == [("disconnect",)]


async def test_pairing_failure_cleans_up_instead_of_enabling_device(radio, monkeypatch):
    class BadPair(FakeClient):
        async def pair(self):
            raise RuntimeError("Pairing failed due to error: 82")

    monkeypatch.setattr(ble, "BleakClientWithServiceCache", BadPair)
    radio.bridge._requires_pairing = lambda _address: True
    with pytest.raises(ble.BleBridgeError, match="Pairing failed due to error: 82"):
        await radio.request("connect")
    assert not radio.bridge.connected_addresses
    assert not radio.clients[0].is_connected


async def test_engine_owns_retry_even_for_helper_transient_errors(radio, monkeypatch):
    class RetryableError(Exception):
        pass

    attempts = []

    class Flaky(FakeClient):
        async def connect(self, **_kwargs):
            attempts.append(1)
            raise RetryableError("ESP_GATT_CONN_TERMINATE_PEER_USER")

    async def transient_retry_helper(client_class, device, name, **kwargs):
        # Models the upstream library's separate transient retry budget.
        client = client_class(device, **kwargs)
        radio.clients.append(client)
        for _attempt in range(9):
            try:
                await client.connect()
                return client
            except RetryableError:
                pass
        raise AssertionError("Inner helper exhausted nine physical attempts")

    monkeypatch.setattr(ble, "BleakClientWithServiceCache", Flaky)
    monkeypatch.setattr(ble, "establish_connection", transient_retry_helper)
    with pytest.raises(ble.BleBridgeError, match="ESP_GATT_CONN_TERMINATE_PEER_USER"):
        await radio.request("connect")
    assert len(attempts) == 1
    assert radio.clients[0].operations == [("disconnect",)]
    # HCI peer termination is not proof of an ATT security rejection.
    assert ADDRESS not in radio.bridge._pairing_required


async def test_cancel_during_pairing_releases_link_and_propagates(radio, monkeypatch):
    entered = asyncio.Event()

    class SlowPair(FakeClient):
        async def pair(self):
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(ble, "BleakClientWithServiceCache", SlowPair)
    radio.bridge._requires_pairing = lambda _address: True
    task = asyncio.create_task(radio.request("connect"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not radio.bridge.connected_addresses
    assert not radio.bridge._connecting
    assert radio.clients[0].operations == [("disconnect",)]
