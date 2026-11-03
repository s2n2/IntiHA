"""Automatic app discovery and simple optional settings for IntiHA."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import voluptuous as vol
from aiohasupervisor import SupervisorError
from homeassistant import config_entries
from homeassistant.components.hassio.handler import get_supervisor_client
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import (
    CONF_AUTO_RECONNECT,
    CONF_CONTROL_TIMEOUT,
    CONF_ENGINE_URL,
    CONF_SCAN_DURATION,
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_ENGINE_URL,
    DEFAULT_SCAN_DURATION,
    DOMAIN,
)

_OPTION_KEYS = {
    CONF_ENGINE_URL,
    CONF_AUTO_RECONNECT,
    CONF_CONTROL_TIMEOUT,
    CONF_SCAN_DURATION,
}


def normalise_engine_url(value: str) -> str:
    """Accept a host, host:port, or full WebSocket URL."""
    value = value.strip()
    bare_host = "://" not in value
    if bare_host:
        try:
            if ip_address(value).version == 6:
                value = f"[{value}]"
        except ValueError:
            pass
        value = f"ws://{value}"
    url = urlsplit(value)
    netloc = url.netloc
    if bare_host and url.hostname and url.port is None:
        netloc = f"{netloc}:12346"
    return urlunsplit(
        (
            url.scheme,
            netloc,
            "/bridge" if url.path in ("", "/") else url.path,
            url.query,
            url.fragment,
        )
    )


def validate_connection(data: dict[str, Any]) -> dict[str, str]:
    """Validate locally, so setup works even before the app is running."""
    try:
        url = urlsplit(normalise_engine_url(data[CONF_ENGINE_URL]))
        port = url.port
        if (
            url.scheme not in ("ws", "wss")
            or not url.hostname
            or any(char.isspace() for char in url.hostname)
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path != "/bridge"
            or (port is not None and not 1 <= port <= 65535)
        ):
            return {CONF_ENGINE_URL: "invalid_url"}
    except (AttributeError, KeyError, TypeError, ValueError):
        return {CONF_ENGINE_URL: "invalid_url"}
    return {}


def connection_schema(data: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ENGINE_URL, default=data.get(CONF_ENGINE_URL, DEFAULT_ENGINE_URL)
            ): selector.TextSelector(),
        }
    )


def discovery_connection(config: dict[str, Any]) -> dict[str, str] | None:
    """Convert the app's Supervisor announcement into a bridge address."""
    host = config.get("host")
    port = config.get("port", 12346)
    if not isinstance(host, str) or not host or not isinstance(port, int) or isinstance(port, bool):
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    connection = {CONF_ENGINE_URL: f"ws://{host}:{port}/bridge"}
    return None if validate_connection(connection) else connection


class IntiHAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One shared Bluetooth transport and engine per HA instance."""

    VERSION = 2
    _discovered_connection: dict[str, str] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_connection(user_input)
            if not errors:
                return self.async_create_entry(
                    title="IntiHA",
                    data={CONF_ENGINE_URL: normalise_engine_url(user_input[CONF_ENGINE_URL])},
                )
        elif is_hassio(self.hass):
            # Add integration can be opened before accepting the discovery card.
            try:
                async with asyncio.timeout(3):
                    announcements = await get_supervisor_client(self.hass).discovery.list()
                for announcement in announcements:
                    if announcement.service == DOMAIN and (
                        connection := discovery_connection(announcement.config)
                    ):
                        self._discovered_connection = connection
                        return await self.async_step_hassio_confirm()
            except (SupervisorError, TimeoutError):
                pass
        return self.async_show_form(
            step_id="user",
            data_schema=connection_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo):
        """Let the app supply its reachable internal address automatically."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        self._discovered_connection = discovery_connection(discovery_info.config)
        if self._discovered_connection is None:
            return self.async_abort(reason="invalid_discovery")
        self.context["title_placeholders"] = {"name": "IntiHA"}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None):
        """Accept the discovered app with no settings to enter."""
        if user_input is not None:
            return self.async_create_entry(title="IntiHA", data=self._discovered_connection or {})
        self._set_confirm_only()
        return self.async_show_form(step_id="hassio_confirm", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IntiHAOptionsFlow()


class IntiHAOptionsFlow(config_entries.OptionsFlow):
    """Optional connection and reconnect settings; devices join automatically."""

    def _current(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {**self.config_entry.data, **self.config_entry.options}.items()
            if key in _OPTION_KEYS
        }

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["behaviour", "connection"])

    async def async_step_connection(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_connection(user_input)
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        **self._current(),
                        CONF_ENGINE_URL: normalise_engine_url(user_input[CONF_ENGINE_URL]),
                    },
                )
        return self.async_show_form(
            step_id="connection",
            data_schema=connection_schema(user_input or self._current()),
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
