"""Diagnostic export without household credentials, raw advertisements or device addresses."""
from homeassistant.helpers.redact import async_redact_data
from .const import VERSION
from .probe import installed_versions


async def async_get_config_entry_diagnostics(hass, entry):
    c = entry.runtime_data
    ctl = c.controller
    raw = {"integration_version": VERSION, "versions": installed_versions(),
           "transport": "native_server", "protocol_version": 3,
           "status": ctl.status, "enabled": ctl.enabled,
           "server_name": ctl.server_name, "engine_url": ctl.url,
           "server_watchdog_ms": ctl.server_watchdog_ms,
           "reconnect_count": ctl.reconnect_count, "last_disconnect": ctl.last_disconnect,
           "last_error": ctl.last_error,
           "devices": [{"name": d.description.label, "state": d.state,
                        "ready": d.ready, "last_error": d.last_error,
                        "stop_acknowledged": d.last_stop_acknowledged}
                       for d in ctl.devices.values()],
           "probe": c.probe.last_result}
    # Errors may include identifiers; preserve them only in local entity attributes.
    return async_redact_data(raw, {"engine_url", "name", "server_name", "address", "source",
        "actual_source", "requested_source", "proxy_name", "last_error", "error", "cause", "reason"})
