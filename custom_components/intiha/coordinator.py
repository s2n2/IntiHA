"""IntiHA state, lifecycle and Home Assistant controls."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .ble import BleBridge
from .bridge import BridgeError, EngineBridge
from .const import (
    CONF_AUTO_RECONNECT,
    CONF_CONTROL_TIMEOUT,
    CONF_ENGINE_URL,
    CONF_SCAN_DURATION,
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_SCAN_DURATION,
    DOMAIN,
    MAX_BLE_REQUESTS,
    MAX_KNOWN_DEVICES,
)
from .security import profile_requests_pairing
from .retry_policy import classify_ble_failure

_LOGGER = logging.getLogger(__name__)
_MAC = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")


def normalise_address(value: str) -> str:
    """Use one stable representation for Bluetooth device registry keys."""
    value = value.strip().upper().replace("-", ":")
    if not _MAC.fullmatch(value):
        raise ValueError("Expected a Bluetooth address such as AA:BB:CC:DD:EE:FF")
    return value


def _clean_name(value: object) -> str:
    """Return a bounded, printable Bluetooth/device name."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:100]


def _specific_name(value: object, address: str) -> str | None:
    """Return a useful model/advertised name, excluding generic protocol labels."""
    name = _clean_name(value)
    if not name or name.upper() == address.upper() or _MAC.fullmatch(name.upper()):
        return None
    lowered = name.casefold().replace("-", "")
    if lowered in {
        "device",
        "wevibe device",
        "wevibe 8bit device",
        "intiha device",
        "buttplug device",
    }:
        return None
    return name


def _protocol_label(protocol: str) -> str:
    """Humanise common Buttplug protocol identifiers for HA fallback names."""
    key = protocol.strip().casefold()
    known = {
        "wevibe": "We-Vibe",
        "wevibe-8bit": "We-Vibe 8-bit",
    }
    if key in known:
        return known[key]
    words = [word for word in re.split(r"[-_]+", protocol.strip()) if word]
    return " ".join(word.upper() if word.isupper() else word.capitalize() for word in words)


def _fallback_name(address: str, protocols: list[str], engine_name: object = None) -> str:
    """Build a stable distinguishable name when BLE provides no model name."""
    suffix = ":".join(address.split(":")[-2:])
    engine = _clean_name(engine_name)
    base = ""
    if engine and engine.upper() != address.upper() and not _MAC.fullmatch(engine.upper()):
        base = re.sub(r"\s+Device$", "", engine, flags=re.IGNORECASE).strip()
        if base.casefold().replace("-", "") == "wevibe":
            base = "We-Vibe"
        elif base.casefold().replace("-", "") == "wevibe 8bit":
            base = "We-Vibe 8-bit"
    if not base and protocols:
        base = _protocol_label(protocols[0])
    if not base:
        base = "Bluetooth device"
    return f"{base} · {suffix}"[:100]


@dataclass
class DeviceState:
    """Observed state; output values are commands, not hardware feedback."""

    address: str
    name: str
    connected: bool = False
    index: int | None = None
    connection_id: str | None = None
    messages: dict[str, Any] = field(default_factory=dict)
    rssi: int | None = None
    source: str | None = None
    last_seen: datetime | None = None
    battery: int | None = None
    outputs: dict[int, float] = field(default_factory=dict)
    error: str | None = None
    phase: str = "offline"
    pairing_required: bool = False
    advertised_name: str = ""
    learned_name: str = ""
    protocols: list[str] = field(default_factory=list)
    connect_enabled: bool = True

    def scalar_features(self) -> list[dict[str, Any]]:
        """Expose vibration controls only when advertised by the real engine."""
        features = self.messages.get("ScalarCmd", [])
        return features if isinstance(features, list) else []

    def battery_index(self) -> int | None:
        for index, feature in enumerate(self.messages.get("SensorReadCmd", [])):
            if feature.get("SensorType") == "Battery":
                return index
        return None


class IntiHACoordinator:
    """Push state manager, with all connection work outside entry setup."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        self.enabled = True
        self.engine_connected = False
        self.client_connected = False
        self.scanning = False
        self.version: str | None = None
        self.last_error: str | None = None
        self.stop_uncertain = False
        self._stop_failure_generation = 0
        self._uncertain_addresses: set[str] = set()
        self.discovered: dict[str, dict[str, Any]] = {}
        self.devices: dict[str, DeviceState] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._request_tasks: set[asyncio.Task] = set()
        self._background_tasks: set[asyncio.Task] = set()
        self._runner: asyncio.Task | None = None
        self._shutdown = False
        self._control_paused = False
        self._state_lock = asyncio.Lock()
        self._last_advertisement: dict[str, float] = {}
        self._scan_until = 0.0
        self._active_scan_lock = asyncio.Lock()
        self._name_resolution_last: dict[str, float] = {}
        self._store = Store[dict[str, Any]](hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._cancel_timer: Callable[[], None] | None = None
        self.ble = BleBridge(
            hass,
            self._async_ble_event,
            self._device_connect_allowed,
            requires_pairing=self._requires_pairing,
            on_pairing_required=self._require_pairing,
        )
        self.bridge = EngineBridge(
            async_get_clientsession(hass),
            self.config[CONF_ENGINE_URL],
            self._hello,
            self._async_engine_message,
            self._async_disconnected,
        )

    @property
    def engine_host(self) -> str:
        return urlsplit(self.config[CONF_ENGINE_URL]).hostname or ""

    @property
    def status(self) -> str:
        if self.stop_uncertain:
            return "stop_unconfirmed"
        if not self.enabled:
            return "disabled"
        if not self.engine_connected:
            return "connecting"
        return "ready"

    @property
    def active_proxy_count(self) -> int:
        return bluetooth.async_scanner_count(self.hass, connectable=True)

    @property
    def owner(self) -> str:
        if not self.enabled or not self.engine_connected:
            return "none"
        return "external_client" if self.client_connected else "home_assistant"

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def _remember_specific_name(
        self, device: DeviceState, candidate: object, *, advertised: bool = False
    ) -> bool:
        """Persist a useful identity and never replace it with a generic label."""
        specific = _specific_name(candidate, device.address)
        if not specific:
            return False
        changed = device.learned_name != specific or device.name != specific
        device.learned_name = specific
        device.name = specific
        if advertised:
            device.advertised_name = specific
        if profile_requests_pairing(specific, device.protocols):
            device.pairing_required = True
        return changed

    def _schedule_name_resolution(self, address: str) -> None:
        """Request a bounded address-targeted active scan for a generic name."""
        if not self.enabled or self._shutdown or not _MAC.fullmatch(address):
            return
        now = self.hass.loop.time()
        if now - self._name_resolution_last.get(address, -1000) < 300:
            return
        self._name_resolution_last[address] = now
        self._spawn(self._async_resolve_device_name(address), f"resolve name {address}")

    async def _async_resolve_device_name(self, address: str) -> None:
        try:
            resolved = await self.ble.async_resolve_name(address, timeout=8.0)
        except Exception:
            _LOGGER.debug("Name resolution failed for %s", address, exc_info=True)
            return
        device = self.devices.get(address)
        if device is None or not resolved:
            return
        if self._remember_specific_name(device, resolved, advertised=True):
            self._save()
            self._notify()

    def _device_connect_allowed(self, address: str) -> bool:
        """Allow GATT only for a supported device whose per-device switch is on."""
        device = self.devices.get(address.upper())
        return bool(self.enabled and device is not None and device.connect_enabled)

    def _requires_pairing(self, address: str) -> bool:
        """Transport security policy for a known, compatible device."""
        device = self.devices.get(address)
        return device is not None and device.pairing_required

    @callback
    def _require_pairing(self, address: str) -> None:
        """Remember an observed ATT security rejection, without storing BLE keys."""
        device = self.devices.get(address)
        if device is not None and not device.pairing_required:
            device.pairing_required = True
            self._save()
            self._notify()

    def _persisted_state(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "stop_uncertain": self.stop_uncertain,
            "uncertain_addresses": sorted(self._uncertain_addresses),
            "devices": {
                address: {
                    "name": device.name,
                    "advertised_name": device.advertised_name,
                    "learned_name": device.learned_name,
                    "protocols": device.protocols,
                    "messages": device.messages,
                    "pairing_required": device.pairing_required,
                    "connect_enabled": device.connect_enabled,
                }
                for address, device in self.devices.items()
            },
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._persisted_state, 1)

    def _hello(self) -> dict[str, Any]:
        return {"type": "hello", "protocol": 1, **self._settings()}

    def _settings(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_reconnect": self.config.get(CONF_AUTO_RECONNECT, True),
            "manual_timeout_seconds": self.config.get(
                CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT
            ),
            "blocked_addresses": sorted(
                address for address, device in self.devices.items() if not device.connect_enabled
            ),
        }

    def _spawn(self, coro, name: str, *, request: bool = False) -> None:
        task = self.entry.async_create_background_task(self.hass, coro, f"IntiHA {name}")
        target = self._request_tasks if request else self._background_tasks
        target.add(task)
        task.add_done_callback(target.discard)

    async def async_initialize(self) -> None:
        """Read local state only. No engine or device can block entry setup."""
        data = await self._store.async_load() or {}
        self.enabled = bool(data.get("enabled", True))
        self.stop_uncertain = bool(data.get("stop_uncertain", False))
        self._uncertain_addresses = {
            address for address in data.get("uncertain_addresses", []) if _MAC.fullmatch(address)
        }
        for address, metadata in data.get("devices", {}).items():
            device = self._register_device(address, metadata.get("name", "Device"))
            if device is not None:
                device.advertised_name = _clean_name(metadata.get("advertised_name", ""))
                stored_learned = _specific_name(metadata.get("learned_name", ""), address)
                stored_name = _specific_name(metadata.get("name", ""), address)
                device.learned_name = stored_learned or stored_name or ""
                if device.learned_name:
                    device.name = device.learned_name
                raw_protocols = metadata.get("protocols", [])
                device.protocols = [str(value)[:80] for value in raw_protocols if isinstance(value, str)][:16]
                device.messages = metadata.get("messages", {})
                device.pairing_required = metadata.get("pairing_required") is True
                device.connect_enabled = metadata.get("connect_enabled") is not False
        await self.ble.async_start()

    @callback
    def async_start(self) -> None:
        self._runner = self.entry.async_create_background_task(
            self.hass, self.bridge.run(), "IntiHA engine bridge"
        )
        self._cancel_timer = async_track_time_interval(
            self.hass, self._async_tick, timedelta(seconds=30)
        )

    async def async_shutdown(self) -> None:
        """Stop reachable devices before ending our bridge and BLE ownership."""
        if self._shutdown:
            return
        self._shutdown = True
        if self._cancel_timer:
            self._cancel_timer()
        # Persist the requested master state, so a reload restores connections
        # at zero output. Movement values are never saved.
        await self._store.async_save(self._persisted_state())
        await self.ble.async_disconnect_all()
        self._clear_devices()
        if self.bridge.connected:
            with suppress(BridgeError, TimeoutError):
                async with asyncio.timeout(10):
                    await self.bridge.request("configure", **{**self._settings(), "enabled": False})
        await self.bridge.close()
        if self._runner is not None:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
        for task in tuple(self._background_tasks | self._request_tasks):
            task.cancel()
        await asyncio.gather(*self._background_tasks, *self._request_tasks, return_exceptions=True)
        await self.ble.async_stop()

    async def async_set_enabled(self, enabled: bool) -> None:
        async with self._state_lock:
            self.enabled = enabled
            self._save()
            self._notify()
            if not enabled:
                # Enforce off locally before waiting for any engine reply.
                # A live heartbeat is not proof its admin loop can respond.
                self.scanning = False
                self._scan_until = 0
                await self.ble.async_disconnect_all()
                self._clear_devices()
                self._notify()
            try:
                if self.bridge.connected:
                    await self.bridge.request("configure", **self._settings())
                    if enabled and self.engine_connected:
                        self._last_advertisement.clear()
                        self._spawn(self._async_send_cached(self.bridge.epoch), "enable discovery")
            except BridgeError as err:
                self.last_error = self._safe_error(err)
                if enabled:
                    await self.bridge.reconnect()
            finally:
                self._notify()

    async def async_set_device_connect(self, address: str, enabled: bool) -> None:
        """Persist and enforce the per-device auto-connect policy."""
        address = normalise_address(address)
        async with self._state_lock:
            device = self.devices.get(address)
            if device is None:
                raise HomeAssistantError("Device is no longer known to IntiHA")
            device.connect_enabled = bool(enabled)
            if not enabled:
                device.phase = "offline"
                device.outputs.clear()
            self._save()
            self._notify()
            try:
                if self.bridge.connected:
                    await self.bridge.request("configure", **self._settings())
                    if enabled and self.engine_connected:
                        self._last_advertisement.pop(address, None)
                        for advertisement in self.ble.discovered():
                            if advertisement.get("address", "").upper() == address:
                                await self._async_ble_event({**advertisement, "type": "advertisement"})
                                break
            except BridgeError as err:
                self.last_error = self._safe_error(err)
                if enabled:
                    await self.bridge.reconnect()
            finally:
                self._notify()

    async def async_scan(self) -> None:
        if not self.enabled:
            raise HomeAssistantError("Turn IntiHA on before searching for devices")
        if not self.bridge.connected:
            raise HomeAssistantError(
                "IntiHA engine is offline. Start the app and check its address."
            )
        seconds = self.config.get(CONF_SCAN_DURATION, DEFAULT_SCAN_DURATION)
        self._scan_until = self.hass.loop.time() + seconds
        self.scanning = True
        self._notify()
        try:
            await self.bridge.request("scan", seconds=seconds)
            # HA may already have advertisements cached before the user presses
            # search. The BLE integration remains the only scanner owner.
            self._last_advertisement.clear()
            for advertisement in self.ble.discovered():
                await self._async_ble_event({**advertisement, "type": "advertisement"})
            # The engine's scan command does not start a radio sweep. HA now
            # defaults many ESPHome proxies to AUTO/passive; a wildcard
            # Bluetooth callback alone does not request active scan responses.
            self._spawn(
                self._async_active_scan(self.bridge.epoch, seconds),
                "requested active BLE scan",
            )
        except BridgeError as err:
            self.scanning = False
            self._scan_until = 0
            self._notify()
            raise HomeAssistantError(self._safe_error(err)) from err

    async def _async_active_scan(self, epoch: int, seconds: float) -> None:
        """Perform only a user-requested active sweep through Home Assistant.

        v0.2.5's automatic scans on bridge hello and every three minutes could
        overlap active GATT connection and service discovery on Wi-Fi proxies.
        A user can still explicitly request scan responses with Search.
        Never run overlapping sweeps, or replay advertisements across epochs.
        """
        if self._active_scan_lock.locked():
            return
        async with self._active_scan_lock:
            if (
                self._shutdown
                or not self.enabled
                or not self.engine_connected
                or epoch != self.bridge.epoch
            ):
                return
            try:
                await bluetooth.async_request_active_scan(
                    self.hass, duration=min(float(seconds), 10.0)
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning(
                    "IntiHA manual Bluetooth scan failed: %s",
                    self._safe_error(err),
                )
                return
            if (
                self._shutdown
                or not self.enabled
                or not self.engine_connected
                or epoch != self.bridge.epoch
            ):
                return
            self._last_advertisement.clear()
            await self._async_send_cached(epoch)

    async def async_stop_all(self) -> None:
        async with self._state_lock:
            await self._async_stop_all_locked()

    async def _async_stop_all_locked(self) -> None:
        failure_generation = self._stop_failure_generation
        connected_at_start = {
            address for address, device in self.devices.items() if device.connected
        }
        self._control_paused = True
        self._notify()
        try:
            # Local cached stops remain usable even if the engine's admin
            # task is stalled. Prevent fresh links while enforcing this fence.
            await self.ble.async_disconnect_all()
            self._clear_devices()
            if self.bridge.connected:
                await self.bridge.request("stop")
                if failure_generation == self._stop_failure_generation:
                    self._uncertain_addresses -= connected_at_start
                    self.stop_uncertain = bool(self._uncertain_addresses)
                for device in self.devices.values():
                    device.outputs.clear()
            if failure_generation != self._stop_failure_generation:
                await self.bridge.reconnect()
                raise HomeAssistantError(
                    "Stop could not be confirmed for every device. Check the devices directly."
                )
        except BridgeError as err:
            self._stop_failure_generation += 1
            self._uncertain_addresses |= connected_at_start
            self.stop_uncertain = True
            self.last_error = self._safe_error(err)
            await self.bridge.reconnect()
            raise HomeAssistantError(
                "Stop could not be confirmed for every device. Check the devices directly."
            ) from err
        finally:
            self._control_paused = False
            self._save()
            self._notify()

    async def async_reconnect(self) -> None:
        """Start a fresh bridge session with cleared output and fresh routing."""
        await self.async_stop_all()
        await self.bridge.reconnect()

    async def async_device_stop(self, address: str) -> None:
        device = self.devices[address]
        if not device.connected or device.index is None:
            raise HomeAssistantError("Device is offline; a stop cannot be delivered")
        await self._async_command({"StopDeviceCmd": {"Id": 1, "DeviceIndex": device.index}})
        device.outputs.clear()
        self._notify()

    async def async_set_output(self, address: str, index: int, percentage: float) -> None:
        device = self.devices[address]
        if (
            not self.enabled
            or self._control_paused
            or self._shutdown
            or not self.engine_connected
            or not device.connected
            or device.index is None
            or not device.connection_id
        ):
            raise HomeAssistantError("Device is offline or IntiHA is disabled")
        if self.client_connected:
            raise HomeAssistantError(
                "An external client has control. Disconnect it before using Home Assistant sliders."
            )
        if not math.isfinite(percentage) or not 0 <= percentage <= 100:
            raise HomeAssistantError("Level must be between 0 and 100")
        features = device.scalar_features()
        if not 0 <= index < len(features) or features[index].get("ActuatorType") != "Vibrate":
            raise HomeAssistantError("This device does not support that vibration feature")
        old_index = device.index
        old_epoch = self.bridge.epoch
        old_connection_id = device.connection_id
        await self._async_command(
            {
                "ScalarCmd": {
                    "Id": 1,
                    "DeviceIndex": old_index,
                    "Scalars": [
                        {"Index": index, "Scalar": percentage / 100, "ActuatorType": "Vibrate"}
                    ],
                }
            },
            connection_id=old_connection_id,
        )
        if (
            device.connected
            and device.index == old_index
            and self.bridge.epoch == old_epoch
            and device.connection_id == old_connection_id
        ):
            device.outputs[index] = percentage
            self._notify()

    async def _async_command(
        self, message: dict[str, Any], *, connection_id: str | None = None
    ) -> dict[str, Any]:
        try:
            payload: dict[str, Any] = {"message": message}
            if connection_id is not None:
                payload["connection_id"] = connection_id
            result = await self.bridge.request("command", **payload)
        except BridgeError as err:
            raise HomeAssistantError(self._safe_error(err)) from err
        response = result.get("message", result)
        if "Error" in response:
            raise HomeAssistantError(
                self._safe_error(response["Error"].get("ErrorMessage", "Command failed"))
            )
        return response

    async def _async_engine_message(self, message: dict[str, Any], epoch: int) -> None:
        if epoch != self.bridge.epoch:
            return
        kind = message.get("type")
        if kind == "hello":
            self.engine_connected = True
            self.last_error = None
            self.version = message.get("version")
            self._last_advertisement.clear()
            self._spawn(self._async_send_cached(epoch), "cached advertisements")
        elif kind == "status":
            self.client_connected = bool(message.get("client_connected", False))
            self.scanning = bool(message.get("scanning", False))
            if message.get("version"):
                self.version = str(message["version"])
            if message.get("last_error"):
                self.last_error = self._safe_error(str(message["last_error"]))
            if self.client_connected:
                for device in self.devices.values():
                    device.outputs.clear()
        elif kind == "devices":
            self._update_devices(message.get("devices", []))
        elif kind == "device_status":
            address = str(message.get("address", "")).upper()
            device = self.devices.get(address)
            if device is not None:
                connection_id = message.get("connection_id")
                if device.connected and device.connection_id != connection_id:
                    return  # A retired connection cannot overwrite the live device.
                phase = message.get("phase")
                if phase in ("connecting", "initialising", "verifying_stop", "retrying", "ready"):
                    # Inventory's ready flag remains the only authority for
                    # enabling controls. Stage notifications are diagnostic.
                    device.phase = phase
                if message.get("error"):
                    device.error = self._safe_error(str(message["error"]))
                    self.last_error = device.error
                elif phase in ("connecting", "ready"):
                    device.error = None
        elif kind == "discovered":
            address = str(message.get("address", "")).upper()
            raw_name = _clean_name(message.get("name", ""))
            raw_protocols = message.get("protocols", [])
            protocols = [str(value)[:80] for value in raw_protocols if isinstance(value, str)][:16]
            device = self._register_device(address, raw_name or "Device")
            if device is not None:
                device.protocols = protocols or device.protocols
                self._remember_specific_name(device, raw_name, advertised=True)
                if device.learned_name:
                    device.name = device.learned_name
                elif not _specific_name(device.name, address):
                    device.name = _fallback_name(address, device.protocols, device.name)
                    self._schedule_name_resolution(address)
                self.discovered[address] = {
                    "address": address,
                    "name": raw_name,
                    "protocols": device.protocols,
                }
                metadata = self.discovered[address]
                if profile_requests_pairing(metadata["name"], metadata["protocols"]):
                    device.pairing_required = True
                self._save()
        elif kind == "request":
            if len(self._request_tasks) >= MAX_BLE_REQUESTS:
                await self.bridge.send(
                    {
                        "type": "response",
                        "id": message.get("id"),
                        "ok": False,
                        "error": "BLE bridge is busy",
                    },
                    epoch=epoch,
                )
            else:
                self._spawn(self._async_ble_request(message, epoch), "BLE request", request=True)
        elif kind in ("output_clear", "output_reset", "stopped"):
            target = message.get("address")
            for device in self.devices.values():
                if target is None or device.address == target:
                    device.outputs.clear()
        elif kind == "sensor":
            self._update_sensor(message.get("message", {}))
        self._notify()

    def _register_device(self, address: str, name: str) -> DeviceState | None:
        """Remember a supported device identified by the official engine."""
        if not _MAC.fullmatch(address):
            return None
        if address in self.devices:
            return self.devices[address]
        if len(self.devices) >= MAX_KNOWN_DEVICES:
            return None
        device = DeviceState(address, str(name)[:100] or "Device")
        self.devices[address] = device
        return device

    def _update_devices(self, descriptions: list[dict[str, Any]]) -> None:
        connected: set[str] = set()
        for description in descriptions:
            address = str(description.get("address", "")).upper()
            device = self._register_device(address, description.get("DeviceName", "Device"))
            if device is None:
                continue
            index = description.get("DeviceIndex")
            if not isinstance(index, int):
                continue
            connection_id = description.get("connection_id")
            new_connection = (
                device.index != index
                or device.connection_id != connection_id
                or not device.connected
            )
            if new_connection:
                device.outputs.clear()
                device.battery = None
            if description.get("ready") is True:
                connected.add(address)
                if new_connection and address in self._uncertain_addresses:
                    self._uncertain_addresses.discard(address)
                    self.stop_uncertain = bool(self._uncertain_addresses)
            engine_name = description.get("DeviceName", device.name)
            engine_specific = _specific_name(engine_name, address)
            if engine_specific:
                self._remember_specific_name(device, engine_specific)
            if device.learned_name:
                # Best-known identity is independent of the current advertisement
                # and survives generic Buttplug inventory refreshes/restarts.
                device.name = device.learned_name
            else:
                advertised = _specific_name(device.advertised_name, address)
                if advertised:
                    self._remember_specific_name(device, advertised, advertised=True)
                else:
                    device.name = _fallback_name(address, device.protocols, engine_name)
                    self._schedule_name_resolution(address)
            device.index = index
            device.connection_id = connection_id
            device.connected = description.get("ready") is True
            if device.connected:
                device.phase = "ready"
                device.error = None
            # A routine inventory refresh must not erase a useful failure.
            device.messages = description.get("DeviceMessages", {})
        for address, device in self.devices.items():
            if address not in connected:
                device.connected = False
                device.index = None
                device.connection_id = None
                device.outputs.clear()
                device.battery = None
        self._save()

    def _update_sensor(self, message: dict[str, Any]) -> None:
        reading = message.get("SensorReading", {})
        if reading.get("SensorType") != "Battery":
            return
        for device in self.devices.values():
            if device.connected and device.index == reading.get("DeviceIndex"):
                values = reading.get("Data", [])
                if values and isinstance(values[0], int) and 0 <= values[0] <= 100:
                    device.battery = values[0]

    async def _async_send_cached(self, epoch: int) -> None:
        for advertisement in self.ble.discovered():
            if epoch != self.bridge.epoch:
                return
            await self._async_ble_event({**advertisement, "type": "advertisement"})

    async def _async_ble_request(self, message: dict[str, Any], epoch: int) -> None:
        response: dict[str, Any] = {"type": "response", "id": message.get("id")}
        try:
            if epoch != self.bridge.epoch:
                return
            if message.get("op") == "connect" and (
                not self.enabled or self._shutdown or self._control_paused
            ):
                raise RuntimeError("IntiHA is disabled")
            if message.get("op") == "scan":
                # Scanning is shared and owned by HA. Advertisements are
                # forwarded continuously while enabled. An explicit search
                # also refreshes cached advertisements and retry attempts.
                enabled = bool(message.get("enabled", False))
                if enabled:
                    self._scan_until = self.hass.loop.time() + self.config.get(
                        CONF_SCAN_DURATION, DEFAULT_SCAN_DURATION
                    )
                    self._spawn(self._async_send_cached(epoch), "scan cache")
                else:
                    self._scan_until = 0
                response.update(ok=True, result={})
            else:
                result = await self.ble.async_request(message)
                response.update(ok=True, result=result)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # A failed device operation must not kill the bridge.
            response.update(ok=False, error=self._safe_error(err))
            address = str(message.get("address", "")).upper()
            if address in self.devices:
                self.devices[address].error = self._safe_error(err)
                if message.get("op") in ("connect", "prepare_stop"):
                    self.devices[address].phase = classify_ble_failure(self.devices[address].error)[0]
                    self.last_error = self.devices[address].error
            _LOGGER.debug(
                "IntiHA BLE operation %s failed: %s", message.get("op"), self._safe_error(err)
            )
            self._notify()
        with suppress(BridgeError):
            await self.bridge.send(response, epoch=epoch)

    async def _async_ble_event(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        address = str(message.get("address", "")).upper()
        if kind == "advertisement":
            if address in self.devices:
                device = self.devices[address]
                device.rssi = message.get("rssi")
                device.source = message.get("source")
                device.last_seen = dt_util.utcnow()
                if self._remember_specific_name(device, message.get("name", ""), advertised=True):
                    self._save()
                elif not device.learned_name:
                    self._schedule_name_resolution(address)
                self._notify()
            now = self.hass.loop.time()
            if not self.enabled or self._shutdown:
                return
            if not self.engine_connected:
                return
            if now - self._last_advertisement.get(address, -100) < 1.5:
                return
            if len(self._last_advertisement) >= 512 and address not in self._last_advertisement:
                # Rotating Bluetooth addresses must not permanently fill the
                # throttle cache and prevent new compatible devices appearing.
                oldest = min(self._last_advertisement, key=self._last_advertisement.get)
                self._last_advertisement.pop(oldest)
            self._last_advertisement[address] = now
        elif kind == "identity":
            device = self.devices.get(address)
            if device is not None and self._remember_specific_name(
                device, message.get("name", ""), advertised=True
            ):
                self._save()
                self._notify()
            return
        elif kind == "disconnected":
            if address in self.devices:
                device = self.devices[address]
                if device.connection_id in (None, message.get("connection_id")):
                    device.connected = False
                    if device.phase != "retrying":
                        device.phase = "offline"
                    device.index = None
                    device.connection_id = None
                    device.outputs.clear()
                    device.battery = None
                    self._notify()
        elif kind == "stop_failed":
            self._stop_failure_generation += 1
            if _MAC.fullmatch(address):
                self._uncertain_addresses.add(address)
            self.stop_uncertain = True
            self.last_error = "A stop command could not be confirmed; check the device directly"
            self._save()
            self._notify()
            return
        elif kind not in ("notification",):
            return
        if self.engine_connected:
            with suppress(BridgeError):
                await self.bridge.send(message, epoch=self.bridge.epoch)

    def _clear_devices(self) -> None:
        for device in self.devices.values():
            device.connected = False
            device.index = None
            device.connection_id = None
            device.outputs.clear()
            device.battery = None

    async def _async_disconnected(self, reason: str) -> None:
        _LOGGER.debug("IntiHA engine bridge disconnected: %s", reason)
        for device in self.devices.values():
            device.phase = "offline"
        self.engine_connected = False
        self.client_connected = False
        self.scanning = False
        self._scan_until = 0
        self.last_error = reason
        for task in tuple(self._request_tasks):
            task.cancel()
        await asyncio.gather(*self._request_tasks, return_exceptions=True)
        await self.ble.async_disconnect_all()
        self._clear_devices()
        self._notify()

    async def _async_tick(self, _now: datetime) -> None:
        self._notify()
        if not self.enabled or not self.engine_connected or self.client_connected:
            return
        for device in tuple(self.devices.values()):
            sensor_index = device.battery_index()
            if not device.connected or sensor_index is None or device.index is None:
                continue
            index, epoch, connection_id = device.index, self.bridge.epoch, device.connection_id
            try:
                response = await self._async_command(
                    {
                        "SensorReadCmd": {
                            "Id": 1,
                            "DeviceIndex": index,
                            "SensorIndex": sensor_index,
                            "SensorType": "Battery",
                        }
                    }
                )
                data = response.get("SensorReading", {}).get("Data", [])
                if (
                    data
                    and device.connected
                    and device.index == index
                    and epoch == self.bridge.epoch
                    and connection_id == device.connection_id
                ):
                    value = int(data[0])
                    device.battery = value if 0 <= value <= 100 else None
            except (HomeAssistantError, ValueError, TypeError):
                device.battery = None
        self._notify()

    def _safe_error(self, err: Exception | str) -> str:
        text = str(err).strip() or type(err).__name__
        # Do not record potentially credential-bearing URLs from network errors.
        text = re.sub(r"wss?://\S+", "[engine]", text)
        return text[:180]
