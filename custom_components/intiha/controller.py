"""Native-server lifecycle, independent devices, and non-replaying controls."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict
import logging
import random
import time
from typing import Any

import aiohttp

from .client import EngineClient
from .models import Description, Device, DisconnectedError, Motor, ProtocolError, percent

_LOGGER = logging.getLogger(__name__)


class Controller:
    """Own one standard Intiface client session, not its Bluetooth stack.

    A device appearing in the server's inventory is already initialised by the
    upstream driver. The HA client sends a normal StopDeviceCmd before allowing
    its own movement commands. An Ok means server acknowledgement, not measured
    motor state. No bytes are intercepted, captured, rewritten or cached.
    """

    def __init__(self, session: aiohttp.ClientSession, url: str, namespace: str,
                 *, auto_scan: bool = True, auto_reconnect: bool = True,
                 control_timeout: float = 60, request_timeout: float = 8,
                 retry_initial: float = 2, retry_max: float = 60,
                 on_update: Callable[[], None] | None = None):
        self.session, self.url, self.namespace = session, url, namespace
        self.auto_scan, self.auto_reconnect = auto_scan, auto_reconnect
        if not 1 <= control_timeout <= 600:
            raise ValueError("Control timeout must be between 1 and 600 seconds")
        self.control_timeout, self.request_timeout = control_timeout, request_timeout
        self.retry_initial, self.retry_max = retry_initial, retry_max
        self.on_update = on_update or (lambda: None)
        self.enabled = False
        self.status = "disabled"
        self.last_error: str | None = None
        self.server_name = ""
        self.server_watchdog_ms = 0
        self.scanning = False
        self.reconnect_count = 0
        self.last_disconnect: dict[str, Any] = {}
        self.devices: dict[str, Device] = {}
        self._indices: dict[int, str] = {}
        self._client: EngineClient | None = None
        self._runner: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()
        self._move_locks: dict[str, asyncio.Lock] = {}
        self._deadlines: dict[str, float] = {}
        self._last_scan = 0.0
        self._scan_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._epoch = 0
        self._inventory_task: asyncio.Task | None = None
        self._scan_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.connected and self.status == "connected")

    @property
    def ready_count(self) -> int:
        return sum(d.ready and d.connected for d in self.devices.values())

    @property
    def connected_device_count(self) -> int:
        return sum(d.connected for d in self.devices.values())

    def _update(self) -> None:
        try:
            self.on_update()
        except Exception:
            _LOGGER.exception("IntiHA state listener failed")

    def _spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._jobs.add(task)
        task.add_done_callback(self._job_done)
        return task

    def _job_done(self, task: asyncio.Task) -> None:
        self._jobs.discard(task)
        if not task.cancelled():
            err = task.exception()
            if err is not None:
                _LOGGER.error("IntiHA task %s failed: %s", task.get_name(), err)

    async def set_enabled(self, enabled: bool) -> None:
        async with self._lifecycle_lock:
            if enabled:
                if self.enabled and self._runner and not self._runner.done():
                    return
                self.enabled = True
                self.last_error = None
                self._runner = asyncio.create_task(self._run(), name="intiha-native-session")
                self._update()
            else:
                await self._shutdown()

    async def reconnect(self) -> None:
        async with self._lifecycle_lock:
            was_enabled = self.enabled
            await self._shutdown()
            if was_enabled:
                self.enabled = True
                self._runner = asyncio.create_task(self._run(), name="intiha-native-session")
                self._update()

    def _invalidate_all(self) -> None:
        for device in self.devices.values():
            device.invalidate()
        self._indices.clear()
        self._deadlines.clear()
        self.scanning = False

    async def _shutdown(self) -> None:
        self.enabled = False
        self._epoch += 1
        self._invalidate_all()
        self._update()
        client = self._client
        if client and client.connected:
            try:
                await client.request("StopAllDevices", timeout=3)
            except Exception as err:
                self.last_error = f"Shutdown stop not acknowledged: {err}"
            await client.close("disabled_by_user_or_shutdown")
        if self._runner is not None:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None
        await self._cancel_jobs()
        self._client = None
        self.status = "disabled"
        self._update()

    async def _cancel_jobs(self) -> None:
        jobs = list(self._jobs)
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._jobs.clear()

    async def _run(self) -> None:
        delay = self.retry_initial
        try:
            while self.enabled:
                self.status = "connecting"
                self._epoch += 1
                epoch = self._epoch
                client = EngineClient(self.session, timeout=self.request_timeout,
                                      on_event=lambda kind, body: self._event(epoch, kind, body))
                self._client = client
                self._update()
                connected_at = None
                try:
                    await client.open(self.url)
                    if not self.enabled or epoch != self._epoch:
                        break
                    self.status = "connected"
                    connected_at = time.monotonic()
                    self.server_name = client.server_name
                    self.server_watchdog_ms = client.max_ping_time
                    self.last_error = None
                    inventory = await client.request("RequestDeviceList", expected="DeviceList")
                    self._sync_devices(inventory.get("Devices"), epoch)
                    if self.auto_scan:
                        self._scan_task = self._spawn(self._background_scan(epoch),
                                                      "intiha-native-scan")
                    self._update()
                    await self._monitor(client, epoch)
                    if self.enabled:
                        self.last_error = client.close_reason
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self.last_error = f"{type(err).__name__}: {err}"
                finally:
                    await self._cancel_jobs()
                    await client.close(client.close_reason if client.close_reason != "connected"
                                       else "controller_session_finished")
                    self.last_disconnect = {"reason": client.close_reason, "code": client.close_code,
                                            "epoch": epoch}
                    self._invalidate_all()
                    self._update()
                if not self.enabled or not self.auto_reconnect:
                    break
                # Short-lived successful handshakes do not reset connection backoff.
                if connected_at is not None and time.monotonic() - connected_at > 30:
                    delay = self.retry_initial
                self.status = "retry_wait"
                self.reconnect_count += 1
                self._update()
                await asyncio.sleep(delay * random.uniform(1.0, 1.15))
                delay = min(self.retry_max, max(self.retry_initial, delay * 2))
        finally:
            self.status = "disconnected" if self.enabled else "disabled"
            self._invalidate_all()
            self._update()

    async def _monitor(self, client: EngineClient, epoch: int) -> None:
        last_inventory = time.monotonic()
        while self.enabled and client.connected and epoch == self._epoch:
            try:
                await asyncio.wait_for(client.closed.wait(), timeout=0.25)
                return
            except TimeoutError:
                pass
            now = time.monotonic()
            for key, deadline in tuple(self._deadlines.items()):
                if now >= deadline:
                    self._deadlines.pop(key, None)
                    self._spawn(self._stop_safely(key), "intiha-control-timeout")
            if now - last_inventory >= 15:
                last_inventory = now
                if self._inventory_task is None or self._inventory_task.done():
                    self._inventory_task = self._spawn(self._refresh_inventory(client, epoch),
                                                       "intiha-native-inventory")
            if (self.auto_scan and not self.scanning and now - self._last_scan >= 60
                and (self._scan_task is None or self._scan_task.done())):
                self._scan_task = self._spawn(self._background_scan(epoch), "intiha-native-scan")

    async def _refresh_inventory(self, client: EngineClient, epoch: int) -> None:
        try:
            result = await client.request("RequestDeviceList", expected="DeviceList")
            self._sync_devices(result.get("Devices"), epoch)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if epoch == self._epoch:
                self.last_error = f"Inventory refresh failed: {err}"
                self._update()

    async def _background_scan(self, epoch: int) -> None:
        try:
            await self.scan()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if epoch == self._epoch:
                self.last_error = f"Native discovery failed: {err}"
                self._update()

    def _event(self, epoch: int, kind: str, body: dict) -> None:
        if epoch != self._epoch or not self.enabled:
            return
        if kind == "DeviceAdded":
            desc = Description.parse(body)
            current = [d.description for d in self.devices.values()
                       if d.connected and d.description.index != desc.index]
            self._sync_descriptions(current + [desc], epoch,
                                    force_new_indices={desc.index})
        elif kind == "DeviceRemoved":
            index = body.get("DeviceIndex")
            key = self._indices.pop(index, None)
            if key and key in self.devices:
                self.devices[key].invalidate()
                self._deadlines.pop(key, None)
            self._update()
        elif kind == "ScanningFinished":
            self.scanning = False
            self._update()
        elif kind == "DeviceList":
            self._sync_devices(body.get("Devices"), epoch)

    def _sync_devices(self, raw: Any, epoch: int) -> None:
        if not isinstance(raw, list) or len(raw) > 256:
            raise ProtocolError("Expected a Buttplug v3 DeviceList array")
        self._sync_descriptions([Description.parse(d) for d in raw], epoch)

    def _sync_descriptions(self, descriptions: list[Description], epoch: int,
                           force_new_indices: set[int] | None = None) -> None:
        if epoch != self._epoch or not self.enabled:
            return
        groups: dict[str, list[Description]] = defaultdict(list)
        indices: set[int] = set()
        for desc in descriptions:
            if desc.index in indices:
                raise ProtocolError("Duplicate DeviceIndex in inventory")
            indices.add(desc.index)
            groups[desc.identity(self.namespace)].append(desc)
        active_keys = set()
        fresh = []
        new_indices = {}
        for identity, group in groups.items():
            ambiguous = len(group) > 1
            for desc in group:
                key = f"{identity}_ambiguous_{desc.index}" if ambiguous else identity
                active_keys.add(key)
                new_indices[desc.index] = key
                old = self.devices.get(key)
                unchanged = (old and old.connected and old.description == desc
                             and desc.index not in (force_new_indices or set()))
                if unchanged:
                    continue
                if old:
                    old.invalidate()
                self._deadlines.pop(key, None)
                device = Device(key, desc, ambiguous=ambiguous)
                device.values = {m.index: None for m in desc.motors}
                if ambiguous:
                    device.state = "ambiguous_identity"
                    device.last_error = "Give identical devices unique display names in Intiface"
                self.devices[key] = device
                fresh.append(device)
        for key, device in self.devices.items():
            if key not in active_keys and device.connected:
                device.invalidate()
                self._deadlines.pop(key, None)
        self._indices = new_indices
        # Never await one peripheral's initial stop in the socket reader or
        # global inventory loop. Other devices may become ready independently.
        for device in fresh:
            self._spawn(self._initial_stop(device, epoch), "intiha-device-initial-stop")
        self._update()

    async def _initial_stop(self, device: Device, epoch: int) -> None:
        try:
            await self.stop_device(device.key, expected_instance=device.instance)
            if self._current(device, epoch) and device.description.battery_index is not None:
                await self.read_battery(device.key)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if self._current(device, epoch):
                device.last_error = f"Initial stop/read failed: {err}"
                if not device.last_stop_acknowledged:
                    device.state = "stop_not_acknowledged"
                    device.ready = False
                self._update()

    def _current(self, device: Device, epoch: int) -> bool:
        return (self.enabled and epoch == self._epoch and self.devices.get(device.key) is device
                and device.connected and self._indices.get(device.description.index) == device.key)

    def _require_device(self, key: str, ready: bool = False) -> Device:
        device = self.devices.get(key)
        if not self.connected or device is None or not device.connected:
            raise DisconnectedError("The device is not connected to the current Intiface session")
        if ready and (not device.ready or device.ambiguous):
            raise ProtocolError("Control is blocked until identity and initial stop are acknowledged")
        return device

    async def scan(self) -> None:
        async with self._scan_lock:
            if not self.enabled or not self._client or not self._client.connected:
                raise DisconnectedError("Connect to an Intiface server first")
            if self.scanning:
                return
            self._last_scan = time.monotonic()
            client, epoch = self._client, self._epoch
            # ScanningFinished may arrive before the request's Ok. Set the
            # optimistic state first so that event cannot be overwritten.
            self.scanning = True
            try:
                await client.request("StartScanning")
            except BaseException:
                if epoch == self._epoch:
                    self.scanning = False
                raise
            finally:
                self._update()

    async def stop_device(self, key: str, *, expected_instance: str | None = None) -> None:
        device = self._require_device(key)
        if expected_instance is not None and device.instance != expected_instance:
            raise DisconnectedError("The device session changed before stop")
        epoch = self._epoch
        client = self._client
        device.command_generation += 1
        generation = device.command_generation
        device.ready = False
        self._deadlines.pop(key, None)
        self._update()
        assert client is not None
        try:
            await client.request("StopDeviceCmd", {"DeviceIndex": device.description.index})
        except Exception as err:
            if self._current(device, epoch):
                device.last_stop_acknowledged = False
                device.state = "stop_not_acknowledged"
                device.last_error = str(err)
                device.values = {m.index: None for m in device.description.motors}
                self.last_error = f"Stop not acknowledged for {device.description.label}: {err}"
                self._update()
            raise
        if not self._current(device, epoch) or device.command_generation != generation:
            return
        device.last_stop_acknowledged = True
        device.ready = not device.ambiguous
        device.state = "ready" if device.ready else "ambiguous_identity"
        if not device.ambiguous:
            device.last_error = None
        device.values = {m.index: 0.0 for m in device.description.motors}
        self._update()

    async def _stop_safely(self, key: str) -> None:
        try:
            await self.stop_device(key)
        except Exception:
            # The stop method records the failure. If an uncertain command is
            # still active, release the server connection as a second stop path.
            if self._client:
                await self._client.close("control_timeout_stop_failed")

    async def stop_all(self) -> None:
        if not self._client or not self._client.connected:
            raise DisconnectedError("Server offline; a stop cannot be confirmed")
        epoch = self._epoch
        records = [(d, d.command_generation + 1) for d in self.devices.values() if d.connected]
        for device, generation in records:
            device.command_generation = generation
            device.ready = False
        self._deadlines.clear()
        self._update()
        try:
            await self._client.request("StopAllDevices")
        except Exception as err:
            self.last_error = f"Stop all not acknowledged: {err}"
            for device, _ in records:
                if self._current(device, epoch):
                    device.last_stop_acknowledged = False
                    device.last_error = self.last_error
                    device.state = "stop_not_acknowledged"
            self._update()
            raise
        for device, generation in records:
            if self._current(device, epoch) and device.command_generation == generation:
                device.last_stop_acknowledged = True
                device.ready = not device.ambiguous
                device.state = "ready" if device.ready else "ambiguous_identity"
                device.values = {m.index: 0.0 for m in device.description.motors}
                if not device.ambiguous:
                    device.last_error = None
        self._update()

    async def set_vibration(self, key: str, motor: int, value: float) -> None:
        value = percent(value)
        device = self._require_device(key, ready=True)
        if not any(m.index == motor for m in device.description.motors):
            raise ValueError("Unknown vibration feature")
        epoch, generation = self._epoch, device.command_generation
        lock = self._move_locks.setdefault(key, asyncio.Lock())
        async with lock:
            gap = max(0.05, device.description.gap_ms / 1000)
            remaining = device.last_output_at + gap - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            if (not self._current(device, epoch) or not device.ready
                or device.command_generation != generation):
                raise DisconnectedError("Queued output cancelled by stop or device/session change")
            client = self._client
            assert client is not None
            device.last_output_at = time.monotonic()
            if value > 0:
                self._deadlines[key] = device.last_output_at + self.control_timeout
            try:
                await client.request("ScalarCmd", {"DeviceIndex": device.description.index,
                    "Scalars": [{"Index": motor, "Scalar": value / 100, "ActuatorType": "Vibrate"}]})
            except asyncio.CancelledError:
                if self._current(device, epoch):
                    device.ready = False
                    device.values[motor] = None
                    self._spawn(self._stop_safely(key), "intiha-cancelled-command-stop")
                raise
            except Exception as err:
                if self._current(device, epoch):
                    device.last_error = f"Output not acknowledged; not replayed: {err}"
                    device.values[motor] = None
                    device.ready = False
                    self._update()
                    self._spawn(self._stop_safely(key), "intiha-failed-command-stop")
                raise
            if self._current(device, epoch) and device.command_generation == generation:
                device.values[motor] = value
                # The stop ACK describes the last stop, not current motor state.
                device.last_stop_acknowledged = False
                if all(v == 0 for v in device.values.values()):
                    self._deadlines.pop(key, None)
                self._update()

    async def read_battery(self, key: str) -> None:
        device = self._require_device(key)
        index = device.description.battery_index
        if index is None:
            return
        epoch = self._epoch
        assert self._client is not None
        body = await self._client.request("SensorReadCmd", {
            "DeviceIndex": device.description.index, "SensorIndex": index, "SensorType": "Battery"
        }, expected="SensorReading")
        data = body.get("Data")
        if (body.get("DeviceIndex") != device.description.index or body.get("SensorIndex") != index
            or body.get("SensorType") != "Battery" or not isinstance(data, list) or not data
            or type(data[0]) is not int):
            raise ProtocolError("Invalid battery response")
        lower, upper = device.description.battery_range
        if not lower <= data[0] <= upper:
            raise ProtocolError("Battery response outside advertised range")
        if self._current(device, epoch):
            device.battery = (data[0] - lower) / (upper - lower) * 100
            self._update()

    def export_known_devices(self) -> list[dict]:
        # Only metadata persists. Never restore movement values or connection state.
        return [{"key": d.key, "description": asdict(d.description)}
                for d in self.devices.values() if not d.ambiguous]

    def restore_known_devices(self, data: Any) -> None:
        if not isinstance(data, list):
            return
        for raw in data[:256]:
            try:
                desc_data = dict(raw["description"])
                desc_data["motors"] = tuple(Motor(**m) for m in desc_data["motors"])
                desc_data["battery_range"] = tuple(desc_data.get("battery_range", [0, 100]))
                desc = Description(**desc_data)
                key = desc.identity(self.namespace)
                if raw["key"] != key:
                    continue
                device = Device(key, desc)
                device.invalidate()
                self.devices[key] = device
            except (KeyError, ValueError, TypeError):
                _LOGGER.warning("Ignoring malformed stored IntiHA device metadata")
