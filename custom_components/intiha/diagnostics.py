"""Useful diagnostics without BLE addresses or device activity."""

from .const import CONF_AUTO_RECONNECT, CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT, VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "integration_version": VERSION,
        "engine_version": coordinator.version,
        "enabled": coordinator.enabled,
        "engine_connected": coordinator.engine_connected,
        "client_connected": coordinator.client_connected,
        "scanning": coordinator.scanning,
        "stop_unconfirmed": coordinator.stop_uncertain,
        "active_connectable_scanners": coordinator.active_proxy_count,
        "known_device_count": len(coordinator.devices),
        "connected_device_count": sum(device.connected for device in coordinator.devices.values()),
        "auto_reconnect": coordinator.config.get(CONF_AUTO_RECONNECT, True),
        "manual_timeout_seconds": coordinator.config.get(
            CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT
        ),
        "devices": [
            {
                "connected": device.connected,
                "ble_pairing_required": device.pairing_required,
                "vibration_features": len(device.scalar_features()),
                "battery_read_supported": device.battery_index() is not None,
            }
            for device in coordinator.devices.values()
        ],
    }
