"""Server and per-device diagnostics."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.helpers.entity import EntityCategory

from .entity import IntiHAEntity, async_add_discovered_entities
from .device_groups import device_group

PARALLEL_UPDATES = 0


def command_telemetry_state(coordinator, device) -> str:
    """Explain commanded-output availability without inventing a device reading.

    The per-device command sensor is unavailable if the physical BLE link is not
    ready. An external controller can still send intensity telemetry when the
    device is connected through *this* IntiHA engine. Ogg can also control a
    device through a different Intiface server; that is not an IntiHA link.
    """
    if not coordinator.enabled:
        return "server_disabled"
    if not coordinator.engine_connected:
        return "engine_disconnected"
    if not device.connect_enabled:
        return "device_disabled"
    if not device.connected:
        return "bluetooth_disconnected"
    if coordinator.client_connected:
        return "receiving_external_commands" if device.outputs else "awaiting_external_commands"
    return "manual_control_ready"


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
                "command_diagnostic",
                "reported_intensity",
                "reported_pattern",
                "feedback_status",
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
        if key in ("intensity", "reported_intensity"):
            self._attr_entity_category = None
            self._attr_icon = "mdi:vibrate"
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_suggested_display_precision = 0
        elif key == "command_diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_icon = "mdi:message-processing-outline"
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = [
                "server_disabled", "engine_disconnected", "device_disabled",
                "bluetooth_disconnected", "awaiting_external_commands",
                "receiving_external_commands", "manual_control_ready",
            ]
        elif key == "feedback_status":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_icon = "mdi:bluetooth-audio"
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = [
                "disconnected", "waiting", "available", "unsupported",
                "not_readable", "read_timeout", "read_error", "invalid_frame",
            ]
        elif key == "reported_pattern":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_icon = "mdi:waveform"
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
        if self.address and self.key in ("intensity", "command_diagnostic", "reported_intensity", "reported_pattern", "feedback_status"):
            self.async_on_remove(
                self.coordinator.add_intensity_listener(
                    self.address, self.async_write_ha_state
                )
            )

    @property
    def available(self) -> bool:
        if self.key in ("intensity", "reported_intensity", "reported_pattern") and self.address:
            device = self.coordinator.devices[self.address]
            ready = self.coordinator.enabled and self.coordinator.engine_connected and device.connected
            return ready and (self.key == "intensity" or device.reported_intensity is not None)
        return True

    @property
    def native_value(self):
        coordinator = self.coordinator
        if self.address:
            device = coordinator.devices[self.address]
            if self.key == "command_diagnostic":
                return command_telemetry_state(coordinator, device)
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
                "reported_intensity": device.reported_intensity,
                "reported_pattern": device.reported_pattern,
                "feedback_status": device.feedback_status,
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
        if self.address and self.key == "command_diagnostic":
            device = self.coordinator.devices[self.address]
            return {
                "bluetooth_connected": device.connected,
                "connect_to_this_device": device.connect_enabled,
                "engine_connected": self.coordinator.engine_connected,
                "external_client_connected": self.coordinator.client_connected,
                "connection_phase": device.phase,
                "last_error": device.error,
                "received_output_channels": dict(sorted(device.outputs.items())),
                "telemetry_requires_own_intiha_ble_connection": True,
                "hardware_feedback": False,
            }
        if self.address and self.key == "intensity":
            device = self.coordinator.devices[self.address]
            return {
                "value_type": "last_commanded_output",
                "hardware_feedback": False,
                "vibration_channels": dict(sorted(device.outputs.items())),
            }
        if self.address and self.key == "reported_intensity":
            device = self.coordinator.devices[self.address]
            return {
                "value_type": "device_reported_setting",
                "hardware_feedback": True,
                "physical_motor_measurement": False,
                "bluetooth_address": device.address,
                "internal_intensity": device.reported_internal,
                "external_intensity": device.reported_external,
                "reported_pattern": device.reported_pattern,
                "pattern_code": device.reported_pattern_code,
                "last_read": device.reported_at.isoformat() if device.reported_at else None,
                "sensor_source": "wevibe_info_characteristic",
            }
        if self.address and self.key == "feedback_status":
            device = self.coordinator.devices[self.address]
            return {
                "detail": device.feedback_detail,
                "device_profile": device.protocols,
                "last_successful_read": device.reported_at.isoformat() if device.reported_at else None,
                "commanded_intensity_is_available_separately": True,
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
