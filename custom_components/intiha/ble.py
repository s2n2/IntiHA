"""Bluetooth transport using Home Assistant's managed adapters and proxies.

This module deliberately knows no device protocols. The upstream Buttplug
engine supplies characteristic operations and a verified stop plan. Every
operation belongs to one physical connection generation; nothing is replayed
after a disconnect.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 40.0
IO_TIMEOUT = 10.0
STOP_TIMEOUT = 4.0
DISCONNECT_TIMEOUT = 5.0
EMIT_TIMEOUT = 5.0
MAX_WRITE_BYTES = 512
MAX_STOP_WRITES = 16
MAX_CONNECTIONS = 32
MAX_EVENTS = 256
MAX_RETIRED_IDS = 4096
_ADDRESS = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z")
_OPS = frozenset(
    {
        "connect",
        "disconnect",
        "read",
        "write",
        "subscribe",
        "unsubscribe",
        "set_stop_plan",
        "prepare_stop",
    }
)


class BleBridgeError(RuntimeError):
    """An operation cannot be completed on its requested BLE generation."""


@dataclass(slots=True)
class _StopWrite:
    characteristic: str
    data: bytes
    response: bool


@dataclass(slots=True)
class _Connection:
    address: str
    connection_id: str
    client: Any
    subscriptions: set[str] = field(default_factory=set)
    stop_plan: tuple[_StopWrite, ...] = ()


class BleBridge:
    """Provide bounded BLE RPCs and discovery through HA's Bluetooth manager.

    ``is_allowed`` is checked at execution time, including after a slow
    connection. ``emit`` receives JSON-compatible dictionaries. Discovery is
    intentionally unfiltered here: the official engine identifies supported
    devices before the coordinator admits their Bluetooth connections.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        is_allowed: Callable[[str], bool],
    ) -> None:
        self._hass = hass
        self._emit = emit
        self._is_allowed = is_allowed
        self._running = False
        self._resetting = False
        self._epoch = 0
        self._unsubscribe: Callable[[], None] | None = None
        self._connections: dict[str, _Connection] = {}
        self._connecting: set[str] = set()
        self._closing: dict[str, asyncio.Task[Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._reset_lock = asyncio.Lock()
        self._requests: set[asyncio.Task[Any]] = set()
        self._background: set[asyncio.Task[Any]] = set()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(MAX_EVENTS)
        self._event_worker: asyncio.Task[None] | None = None
        self._used_ids: set[tuple[str, str]] = set()
        self._used_order: deque[tuple[str, str]] = deque()

    @property
    def connected_addresses(self) -> frozenset[str]:
        """Addresses with a currently live, owned GATT connection."""
        return frozenset(
            address
            for address, connection in self._connections.items()
            if connection.client.is_connected
        )

    async def async_start(self) -> None:
        """Register with the shared HA scanner; never start a second scanner."""
        if self._running:
            return
        self._running = True
        self._event_worker = self._hass.async_create_background_task(
            self._forward_events(), "intiha BLE events"
        )
        try:
            self._unsubscribe = bluetooth.async_register_callback(
                self._hass,
                self._advertisement,
                {"connectable": False},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        except BaseException:
            self._running = False
            self._event_worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._event_worker
            self._event_worker = None
            raise

    async def async_stop(self) -> None:
        """Stop known outputs, release all links, and remove subscriptions."""
        self._running = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self.async_disconnect_all()
        if self._event_worker is not None:
            self._event_worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._event_worker
            self._event_worker = None
        if self._background:
            await asyncio.gather(*tuple(self._background), return_exceptions=True)
        self._clear_events()

    async def async_disconnect_all(self) -> None:
        """Invalidate requests and stop links while retaining discovery callbacks.

        Called on a bridge loss as well as an explicit off/reload. Revocation
        happens before waiting for in-flight operations, so a delayed request
        cannot obtain a newly connected device.
        """
        async with self._reset_lock:
            self._resetting = True
            self._epoch += 1
            connections = tuple(self._connections.values())
            self._connections.clear()
            self._clear_events()
            requests = tuple(self._requests)
            try:
                for task in requests:
                    task.cancel()
                if requests:
                    await asyncio.gather(*requests, return_exceptions=True)
                if self._closing:
                    await asyncio.gather(*tuple(self._closing.values()), return_exceptions=True)
                await asyncio.gather(
                    *(
                        self._close_connection(connection, "bridge_reset")
                        for connection in connections
                    ),
                    return_exceptions=True,
                )
            finally:
                self._resetting = False

    def discovered(self) -> list[dict[str, Any]]:
        """Snapshot current advertisements, including advertisement-only proxies.

        ``source`` and RSSI describe an advertisement, not the active GATT
        connection; HA may choose another adapter with an available slot.
        """
        return [
            self._advertisement_dict(info)
            for info in bluetooth.async_discovered_service_info(self._hass, connectable=False)
        ]

    async def async_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Execute a bridge RPC with bounded per-device serialisation."""
        if not isinstance(request, Mapping):
            raise ValueError("BLE request must be an object")
        op = request.get("op")
        if not isinstance(op, str) or op not in _OPS:
            raise ValueError("Unsupported BLE operation")
        address = self._validate_address(request.get("address"))
        connection_id = request.get("connection_id")
        if not isinstance(connection_id, str) or not 1 <= len(connection_id) <= 128:
            raise ValueError("A connection_id of 1 to 128 characters is required")
        if not self._running or self._resetting:
            raise BleBridgeError("Bluetooth bridge is disabled or resetting")
        if op != "disconnect" and not self._is_allowed(address):
            raise BleBridgeError("Device has not been identified as compatible")
        # Do not allocate locks for arbitrary unrecognised peripherals.
        if op == "disconnect" and address not in self._connections:
            return {}
        epoch = self._epoch
        task = asyncio.create_task(self._execute(op, address, connection_id, dict(request), epoch))
        self._requests.add(task)
        try:
            return await task
        finally:
            self._requests.discard(task)

    async def _execute(
        self,
        op: str,
        address: str,
        connection_id: str,
        request: dict[str, Any],
        epoch: int,
    ) -> dict[str, Any]:
        async with self._locks.setdefault(address, asyncio.Lock()):
            if not self._running or self._resetting or self._epoch != epoch:
                raise BleBridgeError("BLE operation belongs to an expired bridge session")
            if op != "disconnect" and not self._is_allowed(address):
                raise BleBridgeError("Device is no longer available to this integration")
            if op == "connect":
                return await self._connect(address, connection_id, epoch)
            connection = self._connections.get(address)
            if connection is None or connection.connection_id != connection_id:
                raise BleBridgeError("BLE connection_id is stale or disconnected")
            if op == "disconnect":
                self._connections.pop(address)
                stopped = await self._close_connection(connection, "requested")
                if not stopped:
                    raise BleBridgeError("Stop could not be sent before disconnecting the device")
                return {}
            if not connection.client.is_connected:
                self._connection_lost(address, connection_id, "link_lost")
                raise BleBridgeError("BLE connection is no longer connected")
            if op == "prepare_stop":
                return await self._prepare_stop(connection, request, epoch)
            if op == "set_stop_plan":
                writes = request.get("writes")
                if not isinstance(writes, list) or not 1 <= len(writes) <= MAX_STOP_WRITES:
                    raise ValueError("Stop plan must contain 1 to 16 writes")
                connection.stop_plan = tuple(
                    self._validate_write(connection, write) for write in writes
                )
                return {}
            characteristic = self._characteristic(connection, request.get("characteristic"))
            uuid = str(characteristic.uuid).lower()
            write = self._validate_write(connection, request) if op == "write" else None
            if op == "read" and "read" not in characteristic.properties:
                raise ValueError("Characteristic does not support reads")
            if op == "subscribe" and not {"notify", "indicate"}.intersection(
                characteristic.properties
            ):
                raise ValueError("Characteristic does not support notifications")
            try:
                async with asyncio.timeout(IO_TIMEOUT):
                    if op == "read":
                        data = await connection.client.read_gatt_char(characteristic)
                        return {"data": list(data)}
                    if op == "write":
                        assert write is not None
                        await connection.client.write_gatt_char(
                            characteristic, write.data, response=write.response
                        )
                    elif op == "subscribe" and uuid not in connection.subscriptions:
                        # Register first: a device may notify synchronously while
                        # start_notify is awaiting its acknowledgement.
                        connection.subscriptions.add(uuid)
                        await connection.client.start_notify(
                            characteristic,
                            lambda _sender, data: self._notification(connection, uuid, bytes(data)),
                        )
                    elif op == "unsubscribe" and uuid in connection.subscriptions:
                        connection.subscriptions.remove(uuid)
                        await connection.client.stop_notify(characteristic)
            except BaseException as err:
                # An uncertain write must never be silently retried, nor can
                # queued commands continue on a link whose state is unknown.
                if self._connections.get(address) is connection:
                    self._connections.pop(address)
                    await self._close_connection(connection, "gatt_operation_failed")
                if isinstance(err, asyncio.CancelledError):
                    raise
                if isinstance(err, TimeoutError):
                    raise BleBridgeError(
                        f"GATT {op} timed out after {IO_TIMEOUT:g}s on {uuid}"
                    ) from err
                raise BleBridgeError(
                    f"GATT {op} failed on {uuid}: {str(err).strip() or type(err).__name__}"
                ) from err
            return {}

    async def _prepare_stop(
        self, connection: _Connection, request: dict[str, Any], epoch: int
    ) -> dict[str, Any]:
        """Write AND cache a stop on this exact live connection, atomically.

        The caller owns the per-address operation lock. A protocol 'Ok' or an
        idle queue is not verification: every GATT write must actually return.
        Store the validated plan first so cancellation/bridge loss still has a
        local stop to attempt. A failed verification never enables controls.
        """
        writes = request.get("writes")
        if not isinstance(writes, list) or not 1 <= len(writes) <= MAX_STOP_WRITES:
            raise ValueError("Stop plan must contain 1 to 16 writes")
        plan = tuple(self._validate_write(connection, write) for write in writes)
        connection.stop_plan = plan
        try:
            async with asyncio.timeout(IO_TIMEOUT):
                for write in plan:
                    if (
                        self._epoch != epoch
                        or self._resetting
                        or self._connections.get(connection.address) is not connection
                        or not connection.client.is_connected
                    ):
                        raise BleBridgeError("Bluetooth link changed during stop verification")
                    await connection.client.write_gatt_char(
                        write.characteristic, write.data, response=write.response
                    )
                if (
                    self._epoch != epoch
                    or self._resetting
                    or self._connections.get(connection.address) is not connection
                    or not connection.client.is_connected
                ):
                    raise BleBridgeError("Bluetooth link lost before stop verification completed")
        except BaseException as err:
            if self._connections.get(connection.address) is connection:
                self._connections.pop(connection.address)
                await self._close_connection(connection, "stop_verification_failed")
            if isinstance(err, asyncio.CancelledError):
                raise
            if isinstance(err, TimeoutError):
                raise BleBridgeError(
                    f"Stop GATT write timed out after {IO_TIMEOUT:g}s; link closed"
                ) from err
            raise BleBridgeError(
                f"Stop GATT write failed: {str(err).strip() or type(err).__name__}"
            ) from err
        _LOGGER.debug("Verified and cached %s stop writes for this BLE session", len(plan))
        return {
            "verified": True,
            "connection_id": connection.connection_id,
            "write_count": len(plan),
        }

    async def _connect(self, address: str, connection_id: str, epoch: int) -> dict[str, Any]:
        if address in self._closing:
            raise BleBridgeError("Previous BLE connection is still closing")
        existing = self._connections.get(address)
        if existing is not None:
            if existing.connection_id == connection_id and existing.client.is_connected:
                return self._services(existing)
            if existing.client.is_connected:
                raise BleBridgeError("Device already has a different connection_id")
            self._connections.pop(address)
            await self._close_connection(existing, "link_lost")
        key = (address, connection_id)
        if key in self._used_ids:
            raise BleBridgeError("A retired connection_id cannot be reused")
        if len(self._connections) + len(self._connecting) >= MAX_CONNECTIONS:
            raise BleBridgeError("Maximum active BLE connections reached")
        device = bluetooth.async_ble_device_from_address(self._hass, address, connectable=True)
        if device is None:
            raise BleBridgeError("No connectable Bluetooth adapter can currently reach this device")
        self._used_ids.add(key)
        self._used_order.append(key)
        if len(self._used_order) > MAX_RETIRED_IDS:
            self._used_ids.discard(self._used_order.popleft())
        self._connecting.add(address)
        created: list[Any] = []

        class TrackedClient(BleakClientWithServiceCache):
            """Retain a handle for cleanup if establishing a link is cancelled."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                created.append(self)

        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                client = await establish_connection(
                    TrackedClient,
                    device,
                    name=device.name or "IntiHA device",
                    disconnected_callback=lambda client: self._connection_lost(
                        address, connection_id, "link_lost", client=client
                    ),
                    # The engine owns the retry/backoff state machine. One
                    # bounded attempt here avoids two proxy attempts overrunning
                    # the RPC deadline and producing an empty TimeoutError.
                    max_attempts=1,
                    timeout=15.0,
                )
            if (
                not self._running
                or self._resetting
                or self._epoch != epoch
                or not self._is_allowed(address)
                or not client.is_connected
            ):
                raise BleBridgeError("Connection was revoked or lost while connecting")
            connection = _Connection(address, connection_id, client)
            result = self._services(connection)
            self._connections[address] = connection
        except BaseException as err:
            for pending in created:
                await self._disconnect_client(pending)
            if isinstance(err, asyncio.CancelledError):
                raise
            if isinstance(err, TimeoutError):
                raise BleBridgeError(
                    f"BLE connection/service discovery timed out after {CONNECT_TIMEOUT:g}s"
                ) from err
            raise
        finally:
            self._connecting.discard(address)
        await self._emit_safely(
            {"type": "connected", "address": address, "connection_id": connection_id}
        )
        return result

    @staticmethod
    def _services(connection: _Connection) -> dict[str, Any]:
        return {
            "address": connection.address,
            "connection_id": connection.connection_id,
            "services": [
                {
                    "uuid": str(service.uuid).lower(),
                    "characteristics": [
                        {
                            "uuid": str(characteristic.uuid).lower(),
                            "handle": characteristic.handle,
                            "properties": list(characteristic.properties),
                        }
                        for characteristic in service.characteristics
                    ],
                }
                for service in connection.client.services
            ],
        }

    @staticmethod
    def _validate_address(value: Any) -> str:
        if not isinstance(value, str) or not _ADDRESS.fullmatch(value.upper()):
            raise ValueError("A Bluetooth MAC address is required")
        return value.upper()

    @staticmethod
    def _characteristic(connection: _Connection, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("A characteristic UUID is required")
        try:
            uuid = str(UUID(value))
        except ValueError as err:
            raise ValueError("Invalid characteristic UUID") from err
        characteristic = connection.client.services.get_characteristic(uuid)
        if characteristic is None:
            raise ValueError("Characteristic is absent from this connected device")
        return characteristic

    @classmethod
    def _validate_write(cls, connection: _Connection, value: Any) -> _StopWrite:
        if not isinstance(value, Mapping):
            raise ValueError("Write must be an object")
        characteristic = cls._characteristic(connection, value.get("characteristic"))
        data = value.get("data")
        if (
            not isinstance(data, list)
            or not 1 <= len(data) <= MAX_WRITE_BYTES
            or any(type(item) is not int or not 0 <= item <= 255 for item in data)
        ):
            raise ValueError("Write data must contain 1 to 512 integer bytes")
        response = value.get("response", False)
        if type(response) is not bool:
            raise ValueError("Write response must be a boolean")
        required = "write" if response else "write-without-response"
        if required not in characteristic.properties:
            raise ValueError("Characteristic does not support this write mode")
        if not response and len(data) > characteristic.max_write_without_response_size:
            raise ValueError("Write exceeds the negotiated characteristic payload size")
        return _StopWrite(str(characteristic.uuid).lower(), bytes(data), response)

    async def _close_connection(self, connection: _Connection, reason: str) -> bool:
        cancelled = False
        stopped = True
        if connection.stop_plan:
            # A concurrent off/reset may cancel the RPC already performing a
            # stop. Finish that bounded stop instead of cancelling it halfway
            # through and immediately severing the only usable control link.
            stop_task = asyncio.create_task(self._send_stop_plan(connection))
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError:
                cancelled = True
                with suppress(Exception):
                    await stop_task
            except Exception:
                pass
            if stop_task.done() and not stop_task.cancelled() and stop_task.exception():
                stopped = False
                await self._emit_safely(
                    {
                        "type": "stop_failed",
                        "address": connection.address,
                        "connection_id": connection.connection_id,
                        "reason": "Stop could not be sent before the Bluetooth link closed",
                    }
                )
        await self._disconnect_client(connection.client)
        await self._emit_safely(
            {
                "type": "disconnected",
                "address": connection.address,
                "connection_id": connection.connection_id,
                "reason": reason,
            }
        )
        if cancelled:
            raise asyncio.CancelledError
        return stopped

    @staticmethod
    async def _send_stop_plan(connection: _Connection) -> None:
        async with asyncio.timeout(STOP_TIMEOUT):
            for write in connection.stop_plan:
                if not connection.client.is_connected:
                    raise BleBridgeError("Link lost before stop could be sent")
                await connection.client.write_gatt_char(
                    write.characteristic, write.data, response=write.response
                )

    @staticmethod
    async def _disconnect_client(client: Any) -> None:
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT):
                await client.disconnect()
        except Exception:
            _LOGGER.debug("A BLE link could not be cleanly disconnected", exc_info=True)

    def _connection_lost(
        self, address: str, connection_id: str, reason: str, *, client: Any = None
    ) -> None:
        connection = self._connections.get(address)
        if (
            connection is None
            or connection.connection_id != connection_id
            or (client is not None and connection.client is not client)
        ):
            # A retry can create more than one client under the same RPC ID.
            # A late disconnect from the abandoned client must not retire its
            # successful replacement.
            return
        self._connections.pop(address)
        task = self._spawn(self._close_retired_connection(connection, reason), "BLE disconnected")
        self._closing[address] = task

        def finished(completed: asyncio.Task[Any]) -> None:
            if self._closing.get(address) is completed:
                self._closing.pop(address)

        task.add_done_callback(finished)

    async def _close_retired_connection(self, connection: _Connection, reason: str) -> None:
        # A disconnect callback can arrive during a write. Serialise cleanup
        # with that write and finish cleanup before admitting another link.
        async with self._locks.setdefault(connection.address, asyncio.Lock()):
            await self._close_connection(connection, reason)

    def _notification(self, connection: _Connection, characteristic: str, data: bytes) -> None:
        if (
            not self._running
            or self._resetting
            or self._connections.get(connection.address) is not connection
            or characteristic not in connection.subscriptions
        ):
            return
        event = {
            "type": "notification",
            "address": connection.address,
            "connection_id": connection.connection_id,
            "characteristic": characteristic,
            "data": list(data),
        }
        try:
            self._events.put_nowait(event)
        except asyncio.QueueFull:
            # Losing protocol notifications can desynchronise a device. Stop
            # and reconnect instead of continuing with corrupted state.
            self._connection_lost(
                connection.address, connection.connection_id, "notification_queue_full"
            )

    def _advertisement(
        self, info: bluetooth.BluetoothServiceInfoBleak, _change: bluetooth.BluetoothChange
    ) -> None:
        if not self._running:
            return
        with suppress(asyncio.QueueFull):
            self._events.put_nowait(self._advertisement_dict(info))

    @staticmethod
    def _advertisement_dict(info: bluetooth.BluetoothServiceInfoBleak) -> dict[str, Any]:
        return {
            "type": "advertisement",
            "address": info.address.upper(),
            "name": info.name or "",
            "rssi": info.rssi,
            "source": info.source,
            "connectable": info.connectable,
            "service_uuids": list(info.service_uuids),
            "manufacturer_data": {
                str(key): list(value) for key, value in info.manufacturer_data.items()
            },
            "service_data": {key: list(value) for key, value in info.service_data.items()},
        }

    async def _forward_events(self) -> None:
        while True:
            event = await self._events.get()
            if event["type"] == "notification":
                connection = self._connections.get(event["address"])
                if (
                    connection is None
                    or connection.connection_id != event["connection_id"]
                    or event["characteristic"] not in connection.subscriptions
                ):
                    continue
            await self._emit_safely(event)

    async def _emit_safely(self, event: dict[str, Any]) -> None:
        try:
            async with asyncio.timeout(EMIT_TIMEOUT):
                await self._emit(event)
        except Exception:
            _LOGGER.debug("BLE event delivery failed", exc_info=True)

    def _spawn(self, coroutine: Awaitable[Any], name: str) -> asyncio.Task[Any]:
        task = self._hass.async_create_background_task(coroutine, f"intiha {name}")
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def _clear_events(self) -> None:
        while not self._events.empty():
            self._events.get_nowait()
