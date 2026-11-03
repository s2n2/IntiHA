"""Strict Buttplug v3 device descriptions; no manufacturer-specific packets."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class ProtocolError(RuntimeError):
    """An invalid or unsuccessful protocol operation."""


class DisconnectedError(ProtocolError):
    """The particular server session is no longer usable."""


class ServerError(ProtocolError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def normalise_url(value: str) -> str:
    """Accept host[:port] or an explicit ws/wss URL. Never accept credentials."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("An Intiface server address is required")
    value = value.strip()
    bare = "://" not in value
    if bare:
        # A bare IPv6 address needs brackets. Bracketed IPv6:port is left intact.
        import ipaddress
        try:
            if ipaddress.ip_address(value).version == 6:
                value = f"[{value}]"
        except ValueError:
            pass
        value = "ws://" + value
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as err:
        raise ValueError("Invalid server port") from err
    if (parts.scheme not in ("ws", "wss") or not parts.hostname
        or any(c.isspace() for c in parts.netloc)
        or parts.username is not None or parts.password is not None
        or parts.query or parts.fragment or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("Use a ws:// or wss:// server address without credentials or query")
    if parts.path.rstrip("/") == "/bridge":
        raise ValueError("The retired IntiHA /bridge endpoint is not an Intiface server")
    netloc = parts.netloc.lower()
    if bare and port is None:
        netloc += ":12345"
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def integer(value: Any, field_name: str, minimum: int = 0, maximum: int = 2**32-1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError(f"Invalid {field_name}")
    return value


def percent(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("A numeric percentage is required")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError("Percentage must be finite and between 0 and 100")
    return result


@dataclass(frozen=True, slots=True)
class Motor:
    index: int
    name: str
    steps: int


@dataclass(frozen=True, slots=True)
class Description:
    index: int
    name: str
    display_name: str | None
    motors: tuple[Motor, ...]
    gap_ms: int
    battery_index: int | None = None
    battery_range: tuple[int, int] = (0, 100)

    @property
    def label(self) -> str:
        return self.display_name or self.name

    def identity(self, namespace: str) -> str:
        # Standard Buttplug v3 does NOT expose a stable Bluetooth address.
        # Never pretend DeviceIndex is a physical identity across reconnects.
        data = [namespace, self.name, self.display_name,
                [(m.index, m.name, m.steps) for m in self.motors]]
        return hashlib.sha256(json.dumps(data, ensure_ascii=True).encode()).hexdigest()[:24]

    @classmethod
    def parse(cls, data: Any) -> Description:
        if not isinstance(data, dict):
            raise ProtocolError("Invalid device description")
        index = integer(data.get("DeviceIndex"), "DeviceIndex")
        name = data.get("DeviceName")
        if not isinstance(name, str) or not name or len(name) > 512:
            raise ProtocolError("Invalid DeviceName")
        display = data.get("DeviceDisplayName")
        if display is not None and (not isinstance(display, str) or len(display) > 512):
            raise ProtocolError("Invalid DeviceDisplayName")
        messages = data.get("DeviceMessages", {})
        if not isinstance(messages, dict):
            raise ProtocolError("Invalid DeviceMessages")
        motors = []
        scalar = messages.get("ScalarCmd", [])
        if not isinstance(scalar, list) or len(scalar) > 64:
            raise ProtocolError("Invalid ScalarCmd capabilities")
        for i, capability in enumerate(scalar):
            if not isinstance(capability, dict):
                raise ProtocolError("Invalid actuator capability")
            if capability.get("ActuatorType") != "Vibrate":
                continue
            steps = integer(capability.get("StepCount", 100), "StepCount", 1)
            feature = capability.get("FeatureDescriptor", f"Motor {i + 1}")
            if not isinstance(feature, str):
                raise ProtocolError("Invalid motor name")
            motors.append(Motor(i, feature[:128] or f"Motor {i + 1}", steps))
        battery_index = None
        battery_range = (0, 100)
        sensors = messages.get("SensorReadCmd", [])
        if not isinstance(sensors, list):
            raise ProtocolError("Invalid sensor capabilities")
        for i, capability in enumerate(sensors):
            if isinstance(capability, dict) and capability.get("SensorType") == "Battery":
                ranges = capability.get("SensorRange", [[0, 100]])
                if (isinstance(ranges, list) and ranges and isinstance(ranges[0], list)
                    and len(ranges[0]) == 2 and all(type(x) is int for x in ranges[0])
                    and ranges[0][1] > ranges[0][0]):
                    battery_range = tuple(ranges[0])
                battery_index = i
                break
        return cls(index, name, display or None, tuple(motors),
                   integer(data.get("DeviceMessageTimingGap", 0), "timing gap", 0, 60000),
                   battery_index, battery_range)


@dataclass(slots=True)
class Device:
    key: str
    description: Description
    instance: str = field(default_factory=lambda: uuid.uuid4().hex)
    connected: bool = True
    ready: bool = False
    ambiguous: bool = False
    state: str = "stopping_before_control"
    last_error: str | None = None
    last_stop_acknowledged: bool = False
    command_generation: int = 0
    values: dict[int, float | None] = field(default_factory=dict)
    battery: float | None = None
    last_output_at: float = 0.0

    def invalidate(self, reason: str = "disconnected") -> None:
        self.ready = False
        self.connected = False
        self.state = reason
        self.command_generation += 1
        self.values = {motor.index: None for motor in self.description.motors}
        self.battery = None
        self.last_stop_acknowledged = False
