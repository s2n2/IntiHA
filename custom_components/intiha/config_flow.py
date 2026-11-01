"""User setup and options for IntiHA."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_RECONNECT,
    CONF_BRIDGE_TOKEN,
    CONF_CONTROL_TIMEOUT,
    CONF_DEVICES,
    CONF_ENGINE_URL,
    CONF_SCAN_DURATION,
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_ENGINE_URL,
    DEFAULT_SCAN_DURATION,
    DOMAIN,
)
from .coordinator import normalise_address


def validate_connection(data: dict[str, Any]) -> dict[str, str]:
    """Validate locally, so setup works even before the app is running."""
    errors: dict[str, str] = {}
    try:
        url = urlsplit(data[CONF_ENGINE_URL])
        port = url.port
        if (
            url.scheme not in ("ws", "wss")
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path != "/bridge"
            or (port is not None and not 1 <= port <= 65535)
        ):
            errors[CONF_ENGINE_URL] = "invalid_url"
    except ValueError, KeyError:
        errors[CONF_ENGINE_URL] = "invalid_url"
    token = data.get(CONF_BRIDGE_TOKEN, "")
    if (
        not 32 <= len(token) <= 256
        or token != token.strip()
        or any(ord(char) < 33 or ord(char) > 126 for char in token)
    ):
        errors[CONF_BRIDGE_TOKEN] = "invalid_token"
    return errors


def connection_schema(data: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ENGINE_URL, default=data.get(CONF_ENGINE_URL, DEFAULT_ENGINE_URL)
            ): selector.TextSelector(),
            vol.Required(
                CONF_BRIDGE_TOKEN, default=data.get(CONF_BRIDGE_TOKEN, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


class IntiHAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One shared Bluetooth transport and engine per HA instance."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_connection(user_input)
            if not errors:
                return self.async_create_entry(title="IntiHA", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=connection_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IntiHAOptionsFlow()


class IntiHAOptionsFlow(config_entries.OptionsFlow):
    """Manage approved devices without ever connecting unselected devices."""

    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["devices", "connection", "behaviour"]
        )

    async def async_step_connection(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_connection(user_input)
            if not errors:
                return self.async_create_entry(title="", data={**self._current(), **user_input})
        return self.async_show_form(
            step_id="connection",
            data_schema=connection_schema(user_input or self._current()),
            errors=errors,
        )

    async def async_step_devices(self, user_input=None):
        current = self._current()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                selected = {
                    normalise_address(address) for address in user_input.get(CONF_DEVICES, [])
                }
                selected.update(
                    normalise_address(address)
                    for address in user_input.get("manual_addresses", "").split(",")
                    if address.strip()
                )
                if len(selected) > 32:
                    raise ValueError("Too many devices")
            except ValueError, TypeError:
                errors["manual_addresses"] = "invalid_address"
            else:
                return self.async_create_entry(
                    title="", data={**current, CONF_DEVICES: sorted(selected)}
                )
        coordinator = getattr(self.config_entry, "runtime_data", None)
        candidates = dict(coordinator.discovered) if coordinator else {}
        for address in current.get(CONF_DEVICES, []):
            if address not in candidates:
                name = (
                    coordinator.devices[address].name
                    if coordinator and address in coordinator.devices
                    else "Selected device"
                )
                candidates[address] = {"name": name, "address": address}
        options = [
            selector.SelectOptionDict(
                value=address, label=f"{device.get('name', 'Device')} · {address}"
            )
            for address, device in sorted(
                candidates.items(), key=lambda item: (item[1].get("name", ""), item[0])
            )
        ]
        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICES, default=current.get(CONF_DEVICES, [])
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional("manual_addresses", default=""): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_behaviour(self, user_input=None):
        current = self._current()
        if user_input is not None:
            return self.async_create_entry(title="", data={**current, **user_input})
        return self.async_show_form(
            step_id="behaviour",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTO_RECONNECT, default=current.get(CONF_AUTO_RECONNECT, True)
                    ): bool,
                    vol.Required(
                        CONF_CONTROL_TIMEOUT,
                        default=current.get(CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT),
                    ): vol.All(vol.Coerce(int), vol.Range(min=15, max=600)),
                    vol.Required(
                        CONF_SCAN_DURATION,
                        default=current.get(CONF_SCAN_DURATION, DEFAULT_SCAN_DURATION),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
                }
            ),
        )
