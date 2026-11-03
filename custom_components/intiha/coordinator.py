"""Small Home Assistant adapter around the independently testable client."""
from __future__ import annotations

from collections.abc import Callable
import logging

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (CONF_AUTO_RECONNECT, CONF_AUTO_SCAN, CONF_CONTROL_TIMEOUT,
                    CONF_ENGINE_URL, CONF_MIGRATION_REQUIRED, DEFAULT_CONTROL_TIMEOUT,
                    DEFAULT_ENGINE_URL, DOMAIN)
from .controller import Controller
from .probe import ProxyProbe

_LOGGER = logging.getLogger(__name__)


class IntiHACoordinator:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.listeners: set[Callable] = set()
        self.store = Store(hass, 1, f"{DOMAIN}_native.{entry.entry_id}")
        self.probe = ProxyProbe()
        self.migration_required = bool(entry.data.get(CONF_MIGRATION_REQUIRED))
        self.controller = Controller(async_get_clientsession(hass),
            entry.options.get(CONF_ENGINE_URL, entry.data.get(CONF_ENGINE_URL, DEFAULT_ENGINE_URL)),
            entry.entry_id, auto_scan=entry.options.get(CONF_AUTO_SCAN, True),
            auto_reconnect=entry.options.get(CONF_AUTO_RECONNECT, True),
            control_timeout=entry.options.get(CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT),
            on_update=self.updated)
        self._shutting_down = False
        self._initial_enabled = not self.migration_required

    async def initialise(self):
        try:
            data = await self.store.async_load() or {}
            self.controller.restore_known_devices(data.get("devices", []))
            self._initial_enabled = bool(data.get("enabled", self._initial_enabled)) and not self.migration_required
        except Exception:
            _LOGGER.exception("Could not restore IntiHA metadata; starting disabled")
            self._initial_enabled = False
        if self.migration_required:
            self.controller.status = "migration_required"
            self.controller.last_error = "Select a native Intiface server in Configure; the old /bridge is retired"

    async def start(self):
        if self._initial_enabled:
            await self.controller.set_enabled(True)

    def add_listener(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def updated(self):
        for callback in tuple(self.listeners):
            try:
                callback()
            except Exception:
                _LOGGER.exception("IntiHA entity update failed")
        if not self._shutting_down:
            self.store.async_delay_save(self._saved, 5)

    def _saved(self):
        return {"enabled": self.controller.enabled,
                "devices": self.controller.export_known_devices()}

    async def set_enabled(self, enabled):
        if enabled and self.migration_required:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("Configure the native Intiface server before enabling the rebuilt integration")
        if enabled and self.probe.running:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("Finish or cancel the proxy probe before enabling control")
        await self.controller.set_enabled(enabled)
        await self.store.async_save(self._saved())

    async def shutdown(self):
        self._shutting_down = True
        persisted = self._saved()
        await self.probe.cancel()
        await self.controller.set_enabled(False)
        await self.store.async_save(persisted)
