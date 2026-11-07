"""Useful diagnostics without BLE addresses or device activity."""

from .const import CONF_AUTO_RECONNECT, CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT, VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "integration_version": VERSION,
        "bluetooth_transport": "home_assistant_managed",
        "local_bluetooth_required": False,
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
                # No MAC, unique name, GATT bytes or user activity in exports.
                "feedback_status": device.feedback_status,
                "feedback_profile": device.feedback_profile,
                "feedback_detail": device.feedback_detail,
                "feedback_reads_attempted": device.feedback_attempts,
                "feedback_consecutive_failures": device.feedback_failures,
            }
            for device in coordinator.devices.values()
        ],
    }
