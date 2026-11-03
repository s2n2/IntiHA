"""Read-only, explicitly selected ESPHome proxy qualification.

This is NOT a production control backend. It performs no GATT writes and no
unpair/reset. A successful pairing request is not proof that the protected
motor characteristic is usable. It uses the connector of the named registered
HA scanner directly, not the best-RSSI wrapper that may silently choose another.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
import inspect
import re
import time
from typing import Any

_ADDRESS = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHARACTERISTIC = "00002a19-0000-1000-8000-00805f9b34fb"


@dataclass
class Path:
    source: str
    name: str
    rssi: int | None
    device: Any
    factory: Callable
    can_connect: Callable[[], bool]


class ProbeError(RuntimeError):
    pass


def validate_address(address: str) -> str:
    if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
        raise ValueError("Use the device's Bluetooth MAC address")
    return address.upper()


def select_path(paths: list[Path], source: str) -> Path:
    """A unique source MAC or a unique name fragment such as 'garage'."""
    source = source.strip().casefold()
    if not source:
        raise ValueError("Choose a proxy source; automatic selection is not a qualification test")
    exact = [p for p in paths if p.source.casefold() == source or p.name.casefold() == source]
    matches = exact or [p for p in paths if source in p.name.casefold()]
    if len(matches) != 1:
        raise ProbeError("Selected proxy is absent or ambiguous; use the exact source from list_proxy_paths")
    path = matches[0]
    if not path.can_connect():
        raise ProbeError(f"{path.name} is not currently connectable or has no free connection slot")
    return path


def ha_paths(address: str) -> list[Path]:
    """Read already registered HA scanners; never create another scanner/API connection."""
    from habluetooth import get_manager
    manager = get_manager()
    paths = []
    for candidate in manager.async_scanner_devices_by_address(validate_address(address), True):
        scanner = candidate.scanner
        connector = getattr(scanner, "connector", None)
        if not connector or not getattr(connector, "client", None):
            continue  # This test is for remote proxies, not native BlueZ.
        source = str(scanner.source)
        details = getattr(candidate.ble_device, "details", {}) or {}
        if str(details.get("source", "")).casefold() != source.casefold():
            continue  # Refuse to claim pinning if metadata cannot establish it.
        paths.append(Path(source, str(scanner.name), candidate.advertisement.rssi,
                          candidate.ble_device, connector.client, connector.can_connect))
    return paths


def list_paths(address: str) -> list[dict]:
    return [{"source": p.source, "name": p.name, "rssi": p.rssi,
             "can_connect_now": bool(p.can_connect())} for p in ha_paths(address)]


def installed_versions() -> dict[str, str]:
    result = {}
    for package in ("homeassistant", "bleak", "bleak-esphome", "habluetooth",
                    "aioesphomeapi", "bleak-retry-connector"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "not_installed"
    return result


class ProxyProbe:
    def __init__(self):
        self.running = False
        self._lock = asyncio.Lock()
        self.last_result: dict[str, Any] = {}
        self._task: asyncio.Task | None = None

    async def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def run(self, paths: list[Path], address: str, source: str, *, pair: bool = True,
                  read_battery: bool = False, cycles: int = 1,
                  timeout: float = 40, cooldown: float = 3,
                  disconnected_callback=None) -> dict:
        address = validate_address(address)
        if type(cycles) is not int or not 1 <= cycles <= 20:
            raise ValueError("Probe cycles must be between 1 and 20")
        if self._lock.locked():
            raise ProbeError("A proxy probe is already running")
        async with self._lock:
            self._task = asyncio.current_task()
            self.running = True
            self.last_result = {"address": address, "requested_source": source,
                                "status": "running", "pairing_requested": pair,
                                "protected_motor_write_verified": False,
                                "versions": await asyncio.to_thread(installed_versions), "attempts": []}
            try:
                path = select_path(paths, source)
                self.last_result.update(source=path.source, proxy_name=path.name,
                                        rssi=path.rssi)
                for cycle in range(cycles):
                    result = await self._attempt(path, cycle + 1, pair, read_battery,
                                                 timeout, disconnected_callback)
                    self.last_result["attempts"].append(result)
                    if not result["ok"]:
                        # A probe is not an aggressive reconnect loop. Stop on
                        # the first failure, preserving the actual stage/error.
                        self.last_result["status"] = "failed"
                        break
                    if cycle + 1 < cycles:
                        await asyncio.sleep(cooldown)
                else:
                    self.last_result["status"] = "passed_connection_test_only"
            except asyncio.CancelledError:
                self.last_result["status"] = "cancelled"
                raise
            except Exception as err:
                self.last_result.update(status="failed", error=f"{type(err).__name__}: {err}")
            finally:
                self.running = False
                self._task = None
            return self.last_result

    async def _attempt(self, path: Path, cycle: int, pair: bool, read_battery: bool,
                       timeout: float, callback) -> dict:
        started = time.monotonic()
        stage = "proxy_available"
        result = {"cycle": cycle, "source": path.source, "ok": False,
                  "pairing_requested": pair, "pairing_completed": False,
                  "protected_motor_write_verified": False}
        backend = None
        callback_fired = False
        disconnecting = False
        unexpected_disconnect = False

        def lost():
            nonlocal callback_fired, unexpected_disconnect
            callback_fired = True
            unexpected_disconnect = unexpected_disconnect or not disconnecting
            if callback:
                callback()
        try:
            if not path.can_connect():
                raise ProbeError("Selected proxy API is unavailable or all slots are occupied")
            # This factory is already bound by HA/bleak-esphome to THIS proxy.
            # Never replace it with BleakClient(address), which can reroute.
            backend = path.factory(path.device, disconnected_callback=lost,
                                   services=None, timeout=min(20.0, timeout), bluez={})
            if "pair" not in inspect.signature(backend.connect).parameters:
                raise ProbeError("Installed proxy backend lacks the reviewed connect(pair=...) API")
            actual_source = getattr(backend, "_source", None)
            if str(actual_source).casefold() != path.source.casefold():
                raise ProbeError("Backend does not confirm the selected proxy; refusing an unpinned test")
            result["actual_source"] = actual_source
            device_info = getattr(backend, "_device_info", None)
            if device_info is not None:
                result["esphome_version"] = getattr(device_info, "esphome_version", "unknown")
                result["proxy_model"] = getattr(device_info, "model", "unknown")
            stage = "connect_pair_services" if pair else "connect_services"
            async with asyncio.timeout(timeout):
                await backend.connect(pair=pair, dangerous_use_bleak_cache=False)
            if not backend.is_connected:
                raise ProbeError("Backend returned without a connected link")
            result["pairing_completed"] = pair
            result["connect_pair_services_seconds"] = round(time.monotonic() - started, 3)
            stage = "inspect_services"
            result["services"] = [{"uuid": str(service.uuid), "handle": service.handle,
                "characteristics": [{"uuid": str(c.uuid), "handle": c.handle,
                                     "properties": list(c.properties)} for c in service.characteristics]}
                                  for service in backend.services]
            if read_battery:
                stage = "read_standard_battery"
                matches = [c for s in backend.services if str(s.uuid).lower() == BATTERY_SERVICE
                           for c in s.characteristics if str(c.uuid).lower() == BATTERY_CHARACTERISTIC
                           and "read" in c.properties]
                if len(matches) == 1:
                    async with asyncio.timeout(10):
                        value = await backend.read_gatt_char(matches[0])
                    result["battery_raw"] = list(value)
                else:
                    result["battery_read"] = "standard_characteristic_not_unique_or_absent"
            if not backend.is_connected or unexpected_disconnect:
                raise ProbeError("Bluetooth connection was lost during the probe")
            result["ok"] = True
            stage = "disconnect"
        except asyncio.CancelledError:
            result.update(error="cancelled", failed_stage=stage)
            raise
        except Exception as err:
            result.update(error=f"{type(err).__name__}: {err}", failed_stage=stage)
            cause = err.__cause__
            if cause:
                result["cause"] = f"{type(cause).__name__}: {cause}"
        finally:
            disconnecting = True
            if backend is not None:
                try:
                    async with asyncio.timeout(6):
                        await backend.disconnect()
                    result["cleanup"] = "disconnect_completed"
                except Exception as err:
                    result["ok"] = False
                    result["cleanup"] = f"disconnect_failed:{type(err).__name__}:{err}"
            result["disconnect_callback_seen"] = callback_fired
            result["unexpected_disconnect"] = unexpected_disconnect
            result["seconds"] = round(time.monotonic() - started, 3)
        return result
