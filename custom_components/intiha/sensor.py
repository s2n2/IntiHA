from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from .entity import IntiHAEntity, discover


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities([HubSensor(c, key, label) for key, label in (
        ("status", "Status"), ("last_error", "Last error"),
        ("connected_devices", "Connected devices"), ("ready_devices", "Ready devices"),
        ("known_devices", "Known devices"), ("engine_url", "Engine address"),
        ("server_name", "Server name"), ("reconnect_count", "Server reconnects"),
        ("server_watchdog", "Server watchdog"), ("probe_result", "Proxy probe result"),
    )])
    def factory(device):
        yield (device.key, "state"), DeviceStatus(c, "status", "Status", device.key)
        if device.description.battery_index is not None:
            yield (device.key, "battery"), Battery(c, "battery", "Battery", device.key)
    discover(c, entry, async_add_entities, factory)


class HubSensor(IntiHAEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, key, label):
        super().__init__(c, key, label)
        self.key = key
        if key == "server_watchdog":
            self._attr_native_unit_of_measurement = "ms"

    @property
    def native_value(self):
        c = self.controller
        values = {"status": c.status, "last_error": (c.last_error or "None")[:250],
            "connected_devices": c.connected_device_count, "ready_devices": c.ready_count,
            "known_devices": len(c.devices), "engine_url": c.url[:250],
            "server_name": c.server_name or None, "reconnect_count": c.reconnect_count,
            "server_watchdog": c.server_watchdog_ms if c.connected else None,
            "probe_result": self.coordinator.probe.last_result.get("status", "not_run")}
        return values[self.key]

    @property
    def extra_state_attributes(self):
        if self.key == "status":
            return {"transport": "native_server", "protocol_version": 3,
                    "scanning": self.controller.scanning,
                    "migration_required": self.coordinator.migration_required,
                    "last_disconnect": self.controller.last_disconnect}
        if self.key == "last_error":
            return {"full_error": self.controller.last_error}
        if self.key == "probe_result":
            return {"report": self.coordinator.probe.last_result}
        if self.key == "server_watchdog":
            return {"enabled": self.controller.server_watchdog_ms > 0,
                    "note": "A server heartbeat is mitigation, not physical stop confirmation"}
        return {}


class DeviceStatus(IntiHAEntity, SensorEntity):
    @property
    def native_value(self):
        return self.device.state if self.device else "unknown"

    @property
    def extra_state_attributes(self):
        d = self.device
        if d is None:
            return {}
        return {"last_error": d.last_error, "ready_for_commands": d.ready,
                "last_stop_acknowledged": d.last_stop_acknowledged,
                "physical_stop_verified": False, "device_index": d.description.index,
                "device_session": d.instance, "ambiguous_identity": d.ambiguous,
                "identity_basis": "server display name and capabilities; not Bluetooth MAC",
                "control_timeout_seconds": self.controller.control_timeout}


class Battery(IntiHAEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    @property
    def available(self):
        return bool(self.controller.connected and self.device and self.device.connected)

    @property
    def native_value(self):
        return self.device.battery if self.device else None
