"""IntiHA: Home Assistant Bluetooth transport for the official Buttplug engine."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import IntiHACoordinator

type IntiHAConfigEntry = ConfigEntry[IntiHACoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register actions independent of the current engine connection."""

    async def handle_action(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if call.data.get("entry_id") not in (None, entry.entry_id):
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is None:
                continue
            if call.service == "stop_all":
                await coordinator.async_stop_all()
            elif call.service == "scan":
                await coordinator.async_scan()
            elif call.service == "reconnect":
                await coordinator.async_reconnect()

    async def capture_status(call: ServiceCall) -> ServiceResponse:
        """Research aid: record a device's status values (read/subscribe only)."""
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is None:
                continue
            records = await coordinator.ble.async_capture(call.data["address"], call.data["seconds"])
            return {"records": records}
        raise HomeAssistantError("IntiHA is not loaded")

    hass.services.async_register(
        DOMAIN,
        "capture_status",
        capture_status,
        schema=vol.Schema({
            vol.Required("address"): cv.string,
            vol.Optional("seconds", default=10): vol.All(vol.Coerce(float), vol.Range(min=1, max=60)),
        }),
        supports_response=SupportsResponse.ONLY,
    )

    for action in ("stop_all", "scan", "reconnect"):
        hass.services.async_register(
            DOMAIN,
            action,
            handle_action,
            schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: IntiHAConfigEntry) -> bool:
    """Always load; offline engine/devices are reported by entities."""
    coordinator = IntiHACoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_initialize()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def async_on_stop(_event):
        await coordinator.async_shutdown()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_on_stop))
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IntiHAConfigEntry) -> bool:
    """Unload all entities and stop everything owned by this entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: IntiHAConfigEntry) -> bool:
    """Remove obsolete setup fields when upgrading to automatic discovery."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        obsolete = {"bridge_token", "client_token", "selected_devices"}
        hass.config_entries.async_update_entry(
            entry,
            data={key: value for key, value in entry.data.items() if key not in obsolete},
            options={key: value for key, value in entry.options.items() if key not in obsolete},
            version=2,
        )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: IntiHAConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
