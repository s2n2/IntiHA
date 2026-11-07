"""Server and per-device diagnostics."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.helpers.entity import EntityCategory

from .entity import IntiHAEntity, async_add_discovered_entities
from .device_groups import device_group

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = [
        IntiHASensor(coordinator, key)
        for key in (
            "status",
            "engine_address",
            "transport",
            "control_owner",
            "connected_devices",
            "disconnected_devices",
            "disabled_devices",
            "discovered_devices",
            "active_proxies",
            "last_error",
        )
    ]
    async_add_entities(entities)
    async_add_discovered_entities(
        coordinator,
        entry,
        async_add_entities,
        lambda address: (
            IntiHASensor(coordinator, key, address)
            for key in (
                "device_status",
                "intensity",
                "battery",
                "signal_strength",
                "last_seen_via",
                "last_seen",
            )
        ),
    )


class IntiHASensor(IntiHAEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, address=None):
        super().__init__(coordinator, key, address)
        self.key = key
        if key == "intensity":
            self._attr_entity_category = None
            self._attr_icon = "mdi:vibrate"
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_suggested_display_precision = 0
        elif key == "battery":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif key == "signal_strength":
            self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
            self._attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        elif key == "last_seen":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key in ("status", "device_status", "control_owner"):
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = {
                "status": ["disabled", "connecting", "ready", "stop_unconfirmed"],
                "device_status": [
                    "disabled",
                    "offline",
                    "connecting",
                    "initialising",
                    "verifying_stop",
                    "retrying",
                    "waiting_for_proxy",
                    "waiting_for_advertisement",
                    "connection_timed_out",
                    "connected",
                ],
                "control_owner": ["none", "home_assistant", "external_client"],
            }[key]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.address and self.key == "intensity":
            self.async_on_remove(
                self.coordinator.add_intensity_listener(
                    self.address, self.async_write_ha_state
                )
            )

    @property
    def available(self) -> bool:
        if self.key == "intensity" and self.address:
            device = self.coordinator.devices[self.address]
            return self.coordinator.enabled and self.coordinator.engine_connected and device.connected
        return True

    @property
    def native_value(self):
        coordinator = self.coordinator
        if self.address:
            device = coordinator.devices[self.address]
            if self.key == "device_status":
                return (
                    "disabled"
                    if not coordinator.enabled or not device.connect_enabled
                    else "connected"
                    if device.connected
                    else device.phase
                    if device.phase in ("connecting", "initialising", "verifying_stop", "retrying", "waiting_for_proxy", "waiting_for_advertisement", "connection_timed_out")
                    else "offline"
                )
            return {
                "intensity": max(device.outputs.values(), default=0),
                "battery": device.battery,
                "signal_strength": device.rssi,
                "last_seen_via": device.source,
                "last_seen": device.last_seen,
            }.get(self.key)
        return {
            "status": coordinator.status,
            "engine_address": coordinator.engine_host,
            "transport": "Home Assistant Bluetooth / ESPHome proxies",
            "control_owner": coordinator.owner,
            "connected_devices": sum(device_group(device) == "connected" for device in coordinator.devices.values()),
            "disconnected_devices": sum(device_group(device) == "known" for device in coordinator.devices.values()),
            "disabled_devices": sum(device_group(device) == "disabled" for device in coordinator.devices.values()),
            "discovered_devices": len(coordinator.discovered),
            "active_proxies": coordinator.active_proxy_count,
            "last_error": coordinator.last_error or "None",
        }[self.key]

    @property
    def extra_state_attributes(self):
        if self.address and self.key == "intensity":
            device = self.coordinator.devices[self.address]
            return {
                "value_type": "last_commanded_output",
                "hardware_feedback": False,
                "vibration_channels": dict(sorted(device.outputs.items())),
            }
        if self.address and self.key == "device_status":
            device = self.coordinator.devices[self.address]
            return {
                "bluetooth_address": device.address,
                "advertised_name": device.advertised_name or None,
                "buttplug_protocols": device.protocols,
                "connect_enabled": device.connect_enabled,
                "last_error": device.error,
                "connection_phase": device.phase,
                "ble_pairing_required": device.pairing_required,
            }
        if not self.address and self.key in ("connected_devices", "disconnected_devices", "disabled_devices"):
            group = {
                "connected_devices": "connected",
                "disconnected_devices": "known",
                "disabled_devices": "disabled",
            }[self.key]
            devices = [
                {
                    "name": device.name,
                    "address": device.address,
                    "connect_enabled": device.connect_enabled,
                    "status": "connected" if device.connected else "disconnected",
                }
                for device in sorted(
                    self.coordinator.devices.values(), key=lambda item: (item.name.casefold(), item.address)
                )
                if device_group(device) == group
            ]
            return {"devices": devices}
        return None
