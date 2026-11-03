"""IntiHA: native Intiface control plus an isolated, read-only proxy laboratory."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import IntiHACoordinator
from .probe import ha_paths, list_paths, validate_address

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _coordinator(hass, data):
    entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)
               if getattr(entry, "runtime_data", None) is not None
               and data.get("entry_id", entry.entry_id) == entry.entry_id]
    if len(entries) != 1:
        raise HomeAssistantError("Select a loaded IntiHA entry")
    return entries[0].runtime_data


async def async_setup(hass, _config):
    async def action(call):
        coordinator = _coordinator(hass, call.data)
        controller = coordinator.controller
        try:
            if call.service == "stop_all":
                await controller.stop_all()
            elif call.service == "scan":
                await controller.scan()
            elif call.service == "reconnect":
                await controller.reconnect()
            elif call.service == "cancel_probe":
                await coordinator.probe.cancel()
                coordinator.updated()
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    for name in ("stop_all", "scan", "reconnect", "cancel_probe"):
        hass.services.async_register(DOMAIN, name, action,
            schema=vol.Schema({vol.Optional("entry_id"): cv.string}))

    async def paths_action(call):
        try:
            return {"paths": list_paths(call.data["address"])}
        except Exception as err:
            raise HomeAssistantError(f"Could not inspect HA proxy paths: {err}") from err

    hass.services.async_register(DOMAIN, "list_proxy_paths", paths_action,
        schema=vol.Schema({vol.Required("address"): validate_address}),
        supports_response=SupportsResponse.ONLY)

    async def probe_action(call):
        coordinator = _coordinator(hass, call.data)
        if coordinator.controller.enabled:
            raise HomeAssistantError("Turn IntiHA Enabled off and stop other device controllers before probing")
        try:
            paths = ha_paths(call.data["address"])
            result = await coordinator.probe.run(paths, call.data["address"], call.data["source"],
                pair=call.data.get("pair", True), read_battery=call.data.get("read_battery", False),
                cycles=call.data.get("cycles", 1))
            return result
        except Exception as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            coordinator.updated()

    hass.services.async_register(DOMAIN, "probe_proxy", probe_action,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string,
            vol.Required("address"): validate_address, vol.Required("source"): cv.string,
            vol.Optional("pair", default=True): cv.boolean,
            vol.Optional("read_battery", default=False): cv.boolean,
            vol.Optional("cycles", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=20))}),
        supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass, entry):
    coordinator = IntiHACoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.initialise()
    # No network request in setup: an offline server cannot unload the controls.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    async def stop(_event):
        await coordinator.shutdown()
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    await coordinator.start()
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.shutdown()
    entry.runtime_data = None
    return True


async def async_migrate_entry(hass, entry):
    if entry.version > 3:
        return False
    if entry.version < 3:
        # Native and old BLE bridge URLs are NOT interchangeable. Preserve the
        # old config as a rollback record, but never silently activate it.
        hass.config_entries.async_update_entry(entry, version=3,
            data={"migration_required": True,
                  "previous_engine_url": entry.options.get("engine_url", entry.data.get("engine_url", "")),
                  "engine_url": "ws://127.0.0.1:12347"},
            options={k: v for k, v in entry.options.items()
                     if k in {"auto_reconnect", "control_timeout"}})
    return True


async def _options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
