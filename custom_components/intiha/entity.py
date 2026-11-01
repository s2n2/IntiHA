"""Shared IntiHA entity lifecycle and device identifiers."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, VERSION
from .coordinator import IntiHACoordinator


class IntiHAEntity(Entity):
    """A push entity whose availability does not hide the master controls."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, coordinator: IntiHACoordinator, key: str, address: str | None = None
    ) -> None:
        self.coordinator = coordinator
        self.address = address
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{address or 'server'}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        entry_id = self.coordinator.entry.entry_id
        if self.address is None:
            return DeviceInfo(
                identifiers={(DOMAIN, entry_id)},
                name="IntiHA",
                manufacturer="IntiHA",
                model="Bluetooth bridge",
                sw_version=self.coordinator.version or VERSION,
            )
        device = self.coordinator.devices[self.address]
        hub = dr.async_get(self.coordinator.hass).async_get_device_by_identifier(
            (DOMAIN, entry_id), entry_id
        )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}:{self.address}")},
            name=device.name,
            via_device_id=hub.id if hub is not None else None,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.add_listener(self.async_write_ha_state))
