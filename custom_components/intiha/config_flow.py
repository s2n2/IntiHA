"""Native Intiface setup without tokens or device approval lists."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DEFAULT_ENGINE_URL, DOMAIN
from .models import normalise_url


def settings_schema(defaults):
    return vol.Schema({
        vol.Required("engine_url", default=defaults.get("engine_url", DEFAULT_ENGINE_URL)): str,
        vol.Optional("auto_reconnect", default=defaults.get("auto_reconnect", True)): bool,
        vol.Optional("auto_scan", default=defaults.get("auto_scan", True)): bool,
        vol.Optional("control_timeout", default=defaults.get("control_timeout", 60)):
            vol.All(vol.Coerce(int), vol.Range(min=5, max=600)),
    })


class IntiHAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input:
            try:
                url = normalise_url(user_input["engine_url"])
            except ValueError:
                errors["engine_url"] = "invalid_url"
            else:
                return self.async_create_entry(title="IntiHA", data={"engine_url": url})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("engine_url", default=DEFAULT_ENGINE_URL): str}), errors=errors)

    async def async_step_hassio(self, discovery_info):
        config = discovery_info.config
        # Only this native add-on announces the new transport. Ignore the old
        # 0.2 engine's service so it cannot restore the retired BLE bridge.
        if config.get("transport") != "native":
            return self.async_abort(reason="legacy_engine")
        host, port = config.get("host"), config.get("port", 12347)
        if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
            return self.async_abort(reason="invalid_discovery")
        try:
            self._discovered = normalise_url(f"ws://[{host}]:{port}" if ":" in host else f"ws://{host}:{port}")
        except ValueError:
            return self.async_abort(reason="invalid_discovery")
        await self.async_set_unique_id(DOMAIN)
        # Existing migrated entries are reconfigured explicitly, not overwritten
        # from an unsolicited network/discovery announcement.
        self._abort_if_unique_id_configured()
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="IntiHA", data={"engine_url": self._discovered})
        return self.async_show_form(step_id="hassio_confirm", data_schema=vol.Schema({}),
            description_placeholders={"url": self._discovered})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IntiHAOptionsFlow()


class IntiHAOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        defaults = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            try:
                user_input = {**user_input, "engine_url": normalise_url(user_input["engine_url"])}
            except ValueError:
                errors["engine_url"] = "invalid_url"
            else:
                self.hass.config_entries.async_update_entry(self.config_entry,
                    data={**self.config_entry.data, "migration_required": False,
                          "engine_url": user_input["engine_url"]})
                return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=settings_schema(defaults), errors=errors)
